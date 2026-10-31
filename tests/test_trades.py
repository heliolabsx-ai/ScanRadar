import datetime as dt
import trades
from core.sizing import DEFAULTS
from conftest import synthetic_day
from test_api_alerts import FakeCon          # recording cursor

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T = lambda hhmm: dt.datetime(2026, 10, 28, int(hhmm[:2]), int(hhmm[3:]), tzinfo=IST)

def test_candidates_apply_the_desk_filters_and_the_repeat_rule(monkeypatch):
    pat = [{"symbol": "LT", "kind": "triple_top", "side": "short", "state": "triggered", "score": 6, "rr": 0.9, "entry": 3793.7, "stop": 3833.0, "target": 3700.0,
            "facts": {"target1": 3759.2}, "formed_at": T("13:00"), "triggered_at": T("13:25")},
           {"symbol": "X", "kind": "double_top", "side": "short", "state": "failed", "score": 6, "rr": 2.0, "entry": 100, "stop": 101, "target": 98, "facts": {}, "formed_at": T("10:00"), "triggered_at": T("10:20")}]
    als = [{"ts": T("10:20").isoformat(), "scan": "B0101", "symbol": "KFINTECH", "price": 854.6, "stop": 845.9, "target": 872.0, "side": "long", "source": "live", "id": 7},
           {"ts": T("10:40").isoformat(), "scan": "B0101", "symbol": "KFINTECH", "price": 856.0, "stop": 846.0, "target": 876.0, "side": "long", "source": "live", "id": 8},   # repeat within 60 min
           {"ts": T("10:30").isoformat(), "scan": "vwap_reclaim_long", "symbol": "Y", "price": 100, "stop": 99, "target": 101, "side": "long", "source": "live", "id": 9}]    # R:R 1.0 < 1.5
    monkeypatch.setattr(trades, "_pattern_rows_at", lambda con, day, at, include_replay: pat)
    monkeypatch.setattr(trades, "_alert_rows", lambda con, day: als)
    out = trades.candidates(None, dt.date(2026, 10, 28), T("14:00"), include_replay=False, max_age=None, pat_stats={}, scans={})
    assert [(c["ref"], c["bar"], c["target"]) for c in out] == [("alert:7", "10:15", 872.0), ("pattern:LT|triple_top|13:00", "13:20", 3759.2)]
    assert out[0]["source"] == "alert" and out[1]["target2"] == 3700.0
    assert trades.candidates(None, dt.date(2026, 10, 28), T("14:00"), include_replay=False, max_age=10, pat_stats={}, scans={}) == []

def test_build_row_sizes_gates_and_keeps_odds_optional():
    hist = trades.tm.history({f"2026-09-{d:02d}": synthetic_day(seed=d, day=f"2026-09-{d:02d}") for d in range(1, 6)})
    cand = {"ref": "alert:7", "source": "alert", "symbol": "KFINTECH", "side": "long", "ts": T("10:20"), "bar": "10:15", "entry": 100.0, "stop": 99.0, "target": 102.0, "target2": None, "rr": 2.0, "score": None, "label": "B0101"}
    state = trades.ledger([], DEFAULTS)
    r = trades.build_row(cand, DEFAULTS, state, hist, apply_rules=True)
    assert r["size"]["shares"] == 2000 // 1 and r["gate"] == "PASS" and r["odds"]["mc"]["sessions"] == 5 and r["odds"]["verdict"] in ("flat", "coin", "edge", "bad")
    assert r["day"] == dt.date(2026, 10, 28) and r["ts"] == T("10:20") and r["rr"] == 2.0
    assert trades.build_row(cand, DEFAULTS, state, {}, apply_rules=False)["odds"] is None
    assert trades.build_row(cand, DEFAULTS, state, hist, apply_rules=False)["gate"] is None

def test_insert_and_paper_sql_shapes():
    con = FakeCon()
    trades.insert_row(con.cur, {"day": dt.date(2026, 10, 28), "ts": T("10:20"), "ref": "alert:7", "symbol": "K", "side": "long", "source": "alert",
                                "entry": 1, "stop": 0.5, "target": 2, "rr": 2.0, "odds": None, "size": {"shares": 1}, "gate": "PASS", "gate_reason": ""})
    assert "ON CONFLICT (day, ref) DO NOTHING RETURNING id" in con.cur.executed[-1]
    trades.log_paper(con, 5, "took it")
    assert "paper IS NULL" in con.cur.executed[-1]

def test_settings_roundtrip_validates():
    con = FakeCon()
    assert trades.get_settings(con) == DEFAULTS                       # empty table → defaults
    try:
        trades.put_settings(con, {"product": "cash"}); assert False
    except ValueError:
        pass

def _alert(hhmm, i, price=100.0, stop=99.0, target=102.0, scan="B0101", symbol="K"):
    return {"ts": T(hhmm).isoformat(), "scan": scan, "symbol": symbol, "price": price, "stop": stop, "target": target, "side": "long", "source": "live", "id": i}

def test_repeat_rule_anchors_on_the_last_alert_that_got_a_row(monkeypatch):
    monkeypatch.setattr(trades, "_pattern_rows_at", lambda con, day, at, include_replay: [])

    def run(als):
        monkeypatch.setattr(trades, "_alert_rows", lambda con, day: als)
        return [c["ts"] for c in trades.candidates(None, dt.date(2026, 10, 28), T("14:00"), include_replay=False, max_age=None, pat_stats={}, scans={})]

    assert run([_alert("10:00", 1), _alert("10:40", 2), _alert("11:10", 3)]) == [T("10:00"), T("11:10")]
    assert run([_alert("10:00", 1, target=100.5), _alert("10:30", 2)]) == [T("10:30")]          # R:R 0.5 gets no row, so no anchor
    monkeypatch.setattr(trades, "_alert_rows", lambda con, day: [_alert("13:00", 1), _alert("13:55", 2)])
    assert trades.candidates(None, dt.date(2026, 10, 28), T("14:00"), include_replay=False, max_age=10, pat_stats={}, scans={}) == []   # stale 13:00 still anchors

def test_pattern_trigger_that_failed_later_is_still_a_candidate(monkeypatch):
    stored = {"symbol": "LT", "kind": "double_top", "side": "short", "source": "live", "formed_at": T("10:00"), "triggered_at": T("10:20"),
              "failed_at": T("10:35"), "abandoned_at": None, "superseded_at": None, "neckline": 101.0, "entry": 100.0, "stop": 102.0,
              "target": 96.0, "checks": [True] * 6, "facts": None, "score": 6, "outcome": None}
    monkeypatch.setattr(trades.pt, "stored_rows", lambda con, day: [stored])
    monkeypatch.setattr(trades, "_alert_rows", lambda con, day: [])
    cands = lambda at: trades.candidates(None, dt.date(2026, 10, 28), at, include_replay=False, max_age=None, pat_stats={}, scans={})
    assert [(c["ref"], c["bar"], c["target"]) for c in cands(T("14:00"))] == [("pattern:LT|double_top|10:00", "10:15", 96.0)]
    assert cands(T("10:15")) == []                                                             # not triggered yet
    monkeypatch.setattr(trades.pt, "stored_rows", lambda con, day: [dict(stored, failed_at=T("10:20"))])
    assert cands(T("14:00")) == []                                                             # failed on the trigger minute

def test_rows_as_of_clear_later_settlements_and_paper_logs():
    rows = [{"ts": T("10:20").isoformat(), "outcome": {"settled_at": T("11:00").isoformat()}, "paper": {"logged_at": T("10:30").isoformat(), "note": ""}},
            {"ts": T("10:40").isoformat(), "outcome": None, "paper": {"logged_at": T("12:00").isoformat(), "note": ""}},
            {"ts": T("11:30").isoformat(), "outcome": None, "paper": None}]
    cut = trades._cut(rows, T("11:00"))
    assert [r["ts"][11:16] for r in cut] == ["10:20", "10:40"]
    assert cut[0]["outcome"] is not None and cut[0]["paper"] is not None and cut[1]["paper"] is None
    assert trades._cut(rows, T("10:50"))[0]["outcome"] is None and rows[0]["outcome"] is not None   # input untouched

def test_labels_name_the_setup_of_each_row():
    rows = [{"ref": "pattern:LT|triple_top|13:00", "source": "pattern"},
            {"ref": "pattern:X|some_new_kind|10:00", "source": "pattern"},
            {"ref": "alert:7", "source": "alert"},
            {"ref": "alert:99", "source": "alert"}]
    assert trades._alert_ids(rows) == [7, 99]
    out = trades._labels(rows, {7: "B0101"})
    assert [r["label"] for r in out] == ["Triple top", "some_new_kind", "B0101", None]
    assert "label" not in rows[0]                                     # new dicts, the input is untouched

def test_rows_for_day_reads_scan_names_only_when_there_are_alert_rows():
    con = FakeCon()
    assert trades.rows_for_day(con, dt.date(2026, 10, 28)) == []
    assert not any("FROM alerts" in q for q in con.cur.executed)

    class Cur(type(con.cur)):
        def __init__(self, results): super().__init__(); self.results = results; self.params = []
        def execute(self, sql, params=None): super().execute(sql, params); self.params.append(params)
        def fetchall(self): return self.results.pop(0)
    rec = lambda i, ref, source: tuple({"id": i, "day": dt.date(2026, 10, 28), "ts": T("10:20"), "ref": ref, "source": source}.get(c) for c in trades.COLS)
    con.cur = Cur([[rec(1, "alert:7", "alert"), rec(2, "pattern:LT|triple_top|13:00", "pattern")], [(7, "B0101")]])
    rows = trades.rows_for_day(con, dt.date(2026, 10, 28))
    assert [r["label"] for r in rows] == ["B0101", "Triple top"]
    assert "SELECT id, scan FROM alerts WHERE id = ANY(%s)" in con.cur.executed[-1] and con.cur.params[-1] == ([7],)


def test_forget_other_days_keeps_only_the_day(monkeypatch):
    d1, d2 = dt.date(2026, 10, 27), dt.date(2026, 10, 28)
    monkeypatch.setattr(trades, "_HIST", {("A", d1): {"x": 1}, ("B", d1): {}, ("A", d2): {"y": 2}, ("C", d2): {}})
    trades.forget_other_days(d2)
    assert trades._HIST == {("A", d2): {"y": 2}, ("C", d2): {}}
    trades.forget_other_days(d1)
    assert trades._HIST == {}
    assert trades.HIST_CACHE_MAX == 60
