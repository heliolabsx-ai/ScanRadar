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
