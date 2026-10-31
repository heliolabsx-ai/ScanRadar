"""The four PD fade scans (scans/PDH_*.json, scans/PDL_*.json) on hand-built days, and the rule
that a scan without a trade block is never graded at pivot levels."""
import datetime as dt
import pandas as pd
import alerts
from conftest import ROOT, shaped_day, IST as IST_NAME
from core.backtest import fires
from core.rules import resample, trade_levels


def _load_scans() -> dict:
    keep = alerts.SCAN_DIR
    alerts.SCAN_DIR = str(ROOT / "scans")
    try:
        return {s["name"]: s for s, _ in alerts.scan_files()}
    finally:
        alerts.SCAN_DIR = keep


SCANS = {k: v for k, v in _load_scans().items() if k in ("PDH break", "PDH fade", "PDL break", "PDL fade")}


def test_pdh_fade_fires_once_at_the_first_close_back_below():
    # PDH 5010 (+0.2 %): closes 0, 0.1 (below), 0.9 (above: run to ~+0.9 %), 0.1 (back below -> fire at 09:35), 0.6 (above again), 0.0 (below again: no second fire)
    df, _ = shaped_day([0, 0.1, 0.9, 0.1, 0.6, 0.0] + [0.0] * 20, base=5000.0)
    ctx = {"prev_day_high": 5010.0, "prev_day_low": 4900.0, "prev_day_close": 4950.0}
    out = fires(SCANS["PDH fade"], df, ctx, 400)
    assert [t.strftime("%H:%M") for t in out] == ["09:35"]
    bars = resample(df[df.ts < out[0]], "5m"); bars.attrs["session_start"] = 0
    lv = trade_levels(SCANS["PDH fade"], bars, ctx, entry=float(df[df.ts < out[0]].close.iloc[-1]))
    assert lv["stop"] == float(bars.high.max()) and lv["target"] == 4950.0 and lv["basis"] == "scan"


def test_pdl_fade_needs_a_one_percent_flush_and_the_morning():
    df, _ = shaped_day([0, -0.5, -1.3, 0.1] + [0.1] * 30, base=5000.0)         # flush to -1.3 % below PDL 5000, then a close back above
    ctx = {"prev_day_high": 5100.0, "prev_day_low": 5000.0, "prev_day_close": 5040.0}
    assert [t.strftime("%H:%M") for t in fires(SCANS["PDL fade"], df, ctx, 400)] == ["09:35"]
    late = df.copy(); late["ts"] = late.ts + pd.Timedelta(hours=2)                  # same shape from 11:15: outside the window
    assert fires(SCANS["PDL fade"], late, ctx, 400) == []
    shallow, _ = shaped_day([0, -0.5, -0.8, 0.1] + [0.1] * 30, base=5000.0)        # only 0.8 % below: no fire
    assert fires(SCANS["PDL fade"], shallow, ctx, 400) == []


def test_break_scans_fire_on_a_gap_open_with_yesterdays_bar_and_carry_no_trade():
    prev, _ = shaped_day([0] * 75, base=100.0, day="2026-10-29")                  # yesterday closed at 100 (PDH 100.2)
    df, _ = shaped_day([1.0, 1.1, 1.2], base=100.0)                               # opens 1 % above PDH
    ctx = {"prev_day_high": 100.2, "prev_day_low": 99.0, "prev_day_close": 100.0}
    out = fires(SCANS["PDH break"], df, ctx, 400, prev1m=prev)
    assert [t.strftime("%H:%M") for t in out] == ["09:20"]
    assert "trade" not in SCANS["PDH break"] and SCANS["PDH break"]["desk"] is False and SCANS["PDH break"]["alert"]["telegram"] == "silent"
    assert fires(SCANS["PDH fade"], df, ctx, 400) == []                           # non-continuous fade: nothing to fade yet


def test_no_fire_without_reference_levels():
    df, _ = shaped_day([0, 0.5, 0.1, 0.6, 0.0], base=5000.0)
    zero = {"prev_day_high": 0, "prev_day_low": 0, "prev_day_close": 0}
    for name in SCANS:
        assert fires(SCANS[name], df, zero, 400) == []


def test_one_fade_per_day():
    df, _ = shaped_day([0, 0.9, 0.1, 0.9, 0.0, 0.9, 0.1] + [0.0] * 60, base=5000.0)
    ctx = {"prev_day_high": 5010.0, "prev_day_low": 4900.0, "prev_day_close": 4950.0}
    assert len(fires(SCANS["PDH fade"], df, ctx, 400)) == 1


def test_all_four_scans_load():
    assert set(SCANS) == {"PDH break", "PDH fade", "PDL break", "PDL fade"}
    for name in ("PDL break", "PDL fade"):
        assert SCANS[name]["hours"] == {"from": "09:20", "to": "11:00"}
    for name, sc in SCANS.items():
        assert sc["alert"]["cooldown_min"] == 400 and sc["timeframe"] == "5m"


class _RowsCursor:
    def __init__(self, rows): self.rows = rows; self.executed = []
    def execute(self, sql, params=None): self.executed.append((sql, params))
    def executemany(self, sql, rows): self.executed.append((sql, rows))
    def fetchall(self): return self.rows
    def fetchone(self): return (0,)
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _RowsCon:
    def __init__(self, rows): self.cur = _RowsCursor(rows); self.commits = 0
    def cursor(self): return self.cur
    def commit(self): self.commits += 1


def test_break_alerts_are_never_graded_at_pivot_levels(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("_trade_payload called for a scan without a trade block")
    monkeypatch.setattr(alerts, "_trade_payload", boom)
    df, _ = shaped_day([1.0, 1.1, 1.2] + [1.0] * 20, base=100.0)
    ts = pd.Timestamp("2026-10-16 09:20", tz=IST_NAME).to_pydatetime()
    con = _RowsCon([(11, ts, "PDH break", "X", 101.0, {"tf": "5m", "side": "short"})])
    settled = dt.datetime(2026, 10, 17, 9, 0, tzinfo=ts.tzinfo)
    scans = [(SCANS["PDH break"], "h")]
    alerts.ensure_outcomes(con, dt.date(2026, 10, 16), frames={"X": df}, refs={"X": {"prev_close": 100.0, "prev_high": 100.2, "prev_low": 99.0}},
                           scans=scans, now=settled)
    updates = [(q, p) for q, p in con.cur.executed if q.lstrip().startswith("UPDATE")]
    assert len(updates) == 1 and '"outcome": null' in updates[0][1][0] and con.commits == 1


def test_replay_of_a_break_scan_carries_no_levels(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("_trade_payload called for a scan without a trade block")
    monkeypatch.setattr(alerts, "_trade_payload", boom)
    monkeypatch.setattr(alerts, "prev_session_frames", lambda con, day: {})
    df, _ = shaped_day([0, 0.5, -0.5] + [-0.5] * 20, base=100.0)                 # closes above PDH 100.2 at 09:25, back below at 09:30
    con = _RowsCon([])
    settled = dt.datetime(2026, 10, 17, 9, 0, tzinfo=pd.Timestamp("2026-10-16", tz=IST_NAME).tzinfo)
    alerts.ensure_replay_alerts(con, dt.date(2026, 10, 16), frames={"X": df}, refs={"X": {"prev_close": 100.0, "prev_high": 100.2, "prev_low": 99.0}},
                                scans=[(SCANS["PDH break"], "h")], now=settled)
    ins = [rows for q, rows in con.cur.executed if q.lstrip().startswith("INSERT")]
    assert len(ins) == 1 and len(ins[0]) == 1
    import json
    p = json.loads(ins[0][0][4])
    assert p["side"] == "short" and "stop" not in p and "target" not in p and p["story"]["break_at"] == "09:25"
