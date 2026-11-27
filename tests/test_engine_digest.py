"""engine/main.run_once with a digest group (the two Inside open scans) next to an ordinary scan: the digest fires are
ranked and stored with payload.digest, go out as ONE quiet message with a delivery row per alert, and the ordinary
scan's alerts are still queued one by one. Redis, Postgres and Telegram are fakes; the engine module is loaded from
engine/ with its own analytics / rules / notify."""
import datetime as dt
import importlib.util
import json
import sys
import types
import pandas as pd
import pytest
from conftest import ROOT, shaped_day
from core.asof import view_at

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FIRE = dt.datetime(2026, 10, 16, 9, 20, 2, tzinfo=IST)


class FakeR:
    def __init__(self, refs): self.refs, self.kv, self.lists, self.published = refs, {}, {}, []
    def hgetall(self, k): return dict(self.refs.get(k.split(":", 1)[1], {}))
    def get(self, k): return self.kv.get(k)
    def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True
    def publish(self, ch, msg): self.published.append((ch, msg))
    def rpush(self, k, v): self.lists.setdefault(k, []).append(v)
    def expire(self, *a): pass


class FakeDB:
    """One shared log of statements; INSERT ... RETURNING id hands out 1, 2, 3 ..."""
    def __init__(self): self.sql, self.ids = [], 0
    def connect(self, dsn): return self
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return self
    def commit(self): pass
    def execute(self, q, params=None):
        self.sql.append((q, params))
        if "RETURNING id" in q:
            self.ids += 1
    def executemany(self, q, seq): self.sql.extend((q, p) for p in seq)
    def fetchone(self): return (self.ids,)


@pytest.fixture
def engine(monkeypatch):
    db, sent = FakeDB(), []
    monkeypatch.setenv("REDIS_URL", "redis://test/0"); monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fakes = {"redis": types.SimpleNamespace(from_url=lambda *a, **k: None), "psycopg": types.SimpleNamespace(connect=db.connect),
             "requests": types.SimpleNamespace(post=lambda *a, **k: None)}
    saved = {k: sys.modules.get(k) for k in ("analytics", "rules", "notify", *fakes)}
    for k in ("analytics", "rules", "notify"):
        sys.modules.pop(k, None)
    sys.modules.update(fakes)
    sys.path.insert(0, str(ROOT / "engine"))
    try:
        spec = importlib.util.spec_from_file_location("engine_main_under_test", ROOT / "engine" / "main.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    finally:
        sys.path.remove(str(ROOT / "engine"))
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None): return FIRE.astimezone(tz) if tz else FIRE
    monkeypatch.setattr(m, "dt", types.SimpleNamespace(datetime=_DT, date=dt.date, time=dt.time, timezone=dt.timezone, timedelta=dt.timedelta))
    monkeypatch.setattr(m, "analyse", lambda s, df, ref: {"symbol": s, "sector": "X", "pct": 0.1, "dir": 1, "rr": 2.0, "oi_pct": 0.0, "oi_label": "", "rvol": 1.0})
    monkeypatch.setattr(m, "score", lambda a, avg: 1.0)
    monkeypatch.setattr(m, "telegram", lambda text, silent=False: (sent.append((text, silent)) or (True, None)))
    m._db, m._sent = db, sent
    return m


def _frames(shapes: dict, rolled: bool = True) -> tuple[dict, dict]:
    """symbol -> (5m closes in %, base) as the engine sees it at 09:20: five closed minutes and the forming one, or
    (rolled=False) a stock that has not ticked since 09:20:00, whose last row is still the 09:19 candle."""
    frames, refs = {}, {}
    for sym, (rel, base) in shapes.items():
        df, _ = shaped_day(rel, base=base)
        frames[sym] = view_at(df, df.ts.iloc[5]) if rolled else df.iloc[:5].reset_index(drop=True)
        refs[sym] = {"prev_close": str(base), "prev_high": str(base * 1.01), "prev_low": str(base * 0.99)}
    return frames, refs


UP, DOWN = [0.2, 0.3], [-0.2, -0.3]
SHAPES = {"AAA": ([0.05, 0.1], 2500.0), "BBB": ([0.4, 0.5], 3000.0), "CCC": ([0.2, 0.3], 2100.0), "DDD": ([0.3, 0.4], 4000.0),
          "M&M": (DOWN, 3400.0), "LOW": (UP, 900.0)}                     # LOW is under 2,000: no fire, no vote
PLAIN = {"name": "Plain", "side": "long", "timeframe": "1m", "match": "all",
         "rules": [{"lhs": {"off": 0, "field": "close"}, "op": ">", "rhs": {"number": 3900}}],
         "alert": {"telegram": True, "cooldown_min": 30}}


def _run(engine, monkeypatch, shapes=SHAPES, scans=None):
    frames, refs = _frames(shapes)
    R = FakeR(refs)
    monkeypatch.setattr(engine, "R", R)
    monkeypatch.setattr(engine, "load_today", lambda: frames)
    if scans is None:
        scans = [json.loads((ROOT / "scans" / f"Inside_open_{s}.json").read_text()) for s in ("long", "short")] + [PLAIN]
    monkeypatch.setattr(engine, "load_scans", lambda: scans)
    engine.run_once()
    alerts = [(p[0], p[1], p[2], json.loads(p[3])) for q, p in engine._db.sql if q.startswith("INSERT INTO alerts")]   # scan, symbol, price, payload
    deliveries = [p for q, p in engine._db.sql if q.startswith("INSERT INTO deliveries")]
    return R, alerts, deliveries


def test_digest_fires_are_ranked_stored_and_sent_as_one_quiet_message(engine, monkeypatch):
    R, alerts, deliveries = _run(engine, monkeypatch)
    longs = {sym: pl for scan, sym, _, pl in alerts if scan == "Inside open long"}
    shorts = {sym: pl for scan, sym, _, pl in alerts if scan == "Inside open short"}
    price = {sym: px for scan, sym, px, _ in alerts if scan.startswith("Inside open")}
    assert set(longs) == {"AAA", "BBB", "CCC", "DDD"} and set(shorts) == {"M&M"}
    ranks = sorted(longs, key=lambda s: longs[s]["digest"]["rank"])
    assert ranks[-1] == "AAA" and [longs[s]["digest"]["star"] for s in ranks] == [True, True, True, False]    # the weakest push is not starred
    d = longs["AAA"]["digest"]
    assert d["group"] == "Inside open" and d["of"] == 4 and d["breadth"] == 80.0 and d["market"] == "long"   # 4 of the 5 voters are above VWAP
    assert shorts["M&M"]["digest"]["rank"] == 1 and shorts["M&M"]["digest"]["star"] and shorts["M&M"]["digest"]["market"] == "long"
    assert all(pl["stop"] < price[s] < pl["target"] and pl["levels"] == "scan" for s, pl in longs.items())
    assert shorts["M&M"]["target"] < price["M&M"] < shorts["M&M"]["stop"]
    # one summary, quiet, both sides, the long side first because the market leans long
    assert len(engine._sent) == 1 and engine._sent[0][1] is True
    lines = engine._sent[0][0].splitlines()
    assert lines[0] == "<b>Inside open · 09:20</b>" and lines[1] == "market: 80 % of stocks above VWAP · with the longs"
    assert lines[2] == "<b>LONG · target PDH</b> (4)" and lines[6] == "+ 1 more: AAA"
    assert lines[7] == "<b>SHORT · target PDL</b> (1)" and lines[8].startswith("★ M&amp;M ")
    # a delivery row for each of the five alerts, same result, marked silent
    ids = {p[1] for p in deliveries}
    assert len(deliveries) == 5 and ids == {f"alert:{i}" for i in range(1, 6)} and all(p[2] == "telegram" and p[4] is True and p[6] is True for p in deliveries)


def test_an_ordinary_scan_in_the_same_run_is_still_queued_one_by_one(engine, monkeypatch):
    R, alerts, deliveries = _run(engine, monkeypatch)
    plain = [(sym, pl) for scan, sym, _, pl in alerts if scan == "Plain"]
    assert [s for s, _ in plain] == ["DDD"] and "digest" not in plain[0][1]
    queued = [json.loads(x) for x in R.lists["tg:defer:2026-10-16"]]
    assert len(queued) == 1 and queued[0]["ref"] == "alert:6" and queued[0]["silent"] is False and queued[0]["text"].startswith("<b>Plain</b>\nDDD @ ")


def test_a_second_run_in_the_cooldown_sends_nothing_more(engine, monkeypatch):
    frames, refs = _frames(SHAPES)
    R = FakeR(refs)
    monkeypatch.setattr(engine, "R", R); monkeypatch.setattr(engine, "load_today", lambda: frames)
    monkeypatch.setattr(engine, "load_scans", lambda: [json.loads((ROOT / "scans" / f"Inside_open_{s}.json").read_text()) for s in ("long", "short")])
    engine.run_once(); engine.run_once()
    assert len(engine._sent) == 1 and sum(q.startswith("INSERT INTO alerts") for q, _ in engine._db.sql) == 5


def test_no_summary_when_nothing_fires_and_a_failing_ranking_still_sends(engine, monkeypatch):
    _run(engine, monkeypatch, shapes={"LOW": (UP, 900.0)})
    assert engine._sent == []
    monkeypatch.setattr(engine, "apply_digest", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    R, alerts, deliveries = _run(engine, monkeypatch)
    assert len([a for a in alerts if a[0].startswith("Inside")]) == 5 and all("digest" not in a[3] for a in alerts)
    assert len(engine._sent) == 1 and "<b>LONG · target PDH</b> (4)" in engine._sent[0][0] and "★" not in engine._sent[0][0]


def test_stocks_that_have_not_ticked_in_the_new_minute_fire_on_the_same_side(engine, monkeypatch):
    frames, refs = _frames(SHAPES, rolled=False)
    R = FakeR(refs)
    monkeypatch.setattr(engine, "R", R); monkeypatch.setattr(engine, "load_today", lambda: frames)
    monkeypatch.setattr(engine, "load_scans", lambda: [json.loads((ROOT / "scans" / f"Inside_open_{s}.json").read_text()) for s in ("long", "short")])
    engine.run_once()
    fired = {(p[0], p[1]) for q, p in engine._db.sql if q.startswith("INSERT INTO alerts")}
    assert fired == {("Inside open long", s) for s in ("AAA", "BBB", "CCC", "DDD")} | {("Inside open short", "M&M")}
    assert len(engine._sent) == 1 and "with the longs" in engine._sent[0][0]
