import json, datetime as dt
import pandas as pd
import desk, live_desk
from conftest import synthetic_day

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def test_cost_uses_the_price_table_and_cache_rates():
    u = {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 0}
    usd = (1000 * 4 + 500 * 20 + 3000 * 0.2) / 1e6
    assert desk.cost_inr("claude-opus-5-5", u, usdinr=88) == round(usd * 88, 4)
    assert desk.cost_inr("claude-haiku-5-5", {"input_tokens": 1_000_000}, usdinr=88) == round(0.10 * 88, 4)
    assert desk.cost_inr("claude-opus-5-5-20270101", {"output_tokens": 1_000_000}, usdinr=1) == 20.0   # dated id → family price


def test_budget_shares():
    assert desk.budget_allows("verdict", 99, budget=100) and not desk.budget_allows("verdict", 100, budget=100)
    assert desk.budget_allows("brief", 69, budget=100) and not desk.budget_allows("brief", 70, budget=100)
    assert desk.budget_allows("ask", 97, budget=100) and not desk.budget_allows("ask", 98, budget=100)
    assert not desk.budget_allows("verdict", 0, budget=0)


def test_bars5m_tail_is_complete_bars_before_at():
    df = synthetic_day()
    at = pd.Timestamp("2026-10-16 10:02", tz="Asia/Kolkata")
    bars = desk.bars5m_tail(df, at, n=3)
    assert [b["t"] for b in bars] == ["09:45", "09:50", "09:55"] or [b["t"] for b in bars] == ["09:50", "09:55", "10:00"]
    assert all(k in bars[-1] for k in ("o", "h", "l", "c", "v", "oi"))


def _snap():
    return {"n_bull": 90, "n_bear": 110, "sectors": {"METALS": 0.6, "IT": 2.9},
            "bulls": [{"symbol": "TATASTEEL", "rank": 4, "side_rank": 2, "picked": True, "score": 61.2, "pct": 1.23456, "price": 174.9, "vwap": 174.1,
                       "above_vwap": True, "oi_pct": -1.9, "oi_label": "short covering", "rvol": 2.1, "S1": 172.0, "R1": 177.0, "dir": 1,
                       "ob": {"lo": 173.0, "hi": 173.8, "dir": 1}, "stop": 172.4, "target": 177.5, "rr": 1.0}], "bears": []}


def test_rank_row_trims_and_rounds():
    r = desk.rank_row(_snap(), "TATASTEEL")
    assert r["pct"] == 1.23 and r["side"] == "bull" and r["ob"] == {"lo": 173.0, "hi": 173.8, "dir": 1}
    assert desk.rank_row(_snap(), "NOPE") is None and desk.rank_row(None, "X") is None


def test_size_for_rounds_down_to_lots():
    s = desk.size_for(174.9, 172.35, 750, risk=2000)
    assert (s["qty"], s["lots"], s["lot"], s["risk_inr"]) == (750, 1, 750, 2000) and abs(s["rupees_at_stop"] - 1912.5) <= 0.5
    assert desk.size_for(100, 99, 5000, risk=2000)["qty"] == 0
    assert desk.size_for(100, 100, 100)["qty"] == 0


def _trigger():
    return {"source": "pattern", "kind": "double_bottom", "label": "double bottom", "at": "10:45", "score": 4, "checks": [True] * 5 + [True],
            "symbol": "TATASTEEL", "side": "long", "entry": 174.9, "stop": 172.35, "target": 177.45}


def test_verdict_context_has_the_ticket_from_the_system():
    ctx = desk.verdict_context(_trigger(), snap=_snap(), sector_pct=0.6, bars=[{"t": "10:40"}], ref={"sector": "METALS", "prev_close": 176.8, "prev_high": 178, "prev_low": 173},
                               lot=750, setup_stats={"hit_rate_pct": 67}, now_hhmm="10:45")
    assert ctx["ticket"]["entry"] == 174.9 and ctx["ticket"]["rr"] == 1.0 and ctx["ticket"]["size"]["qty"] == 750
    assert ctx["minutes_since_open"] == 90 and ctx["sector"] == {"name": "METALS", "pct": 0.6} and ctx["breadth"] == {"bulls": 90, "bears": 110}
    assert ctx["ranking_row"]["oi_label"] == "short covering" and ctx["blind_to"] == desk.BLIND_TO
    assert "entry" not in ctx["trigger"]                       # the model never sees levels twice / never sets them


def _verdict_rec():
    return {"ts": "2026-10-19T10:45:00+05:30", "symbol": "TATASTEEL",
            "context": desk.verdict_context(_trigger(), snap=_snap(), sector_pct=0.6, bars=[], ref={"sector": "METALS"}, lot=750, setup_stats={}, now_hhmm="10:45"),
            "output": {"call": "take", "confidence": 7, "reasons": [{"mark": "ok", "text": "OI -1.9% between the lows: covering"},
                                                                    {"mark": "ok", "text": "Metals +0.6%, stock +1.2%"},
                                                                    {"mark": "warn", "text": "Prev close 176.80 sits before target"}],
                       "exit_rule": "5m close < 174.20 in the next 3 bars", "book_half_at": 176.8}}


def test_telegram_text_order():
    t = desk.telegram_text(_verdict_rec())
    assert t.splitlines() == ["<b>TAKE 7/10 · TATASTEEL · BUY · double bottom</b>",
                              "Entry 174.90 · Stop 172.35 · Target 177.45 · Size 750",
                              "✓ OI -1.9% between the lows: covering · ✓ Metals +0.6%, stock +1.2% · ! Prev close 176.80 sits before target",
                              "Exit: 5m close < 174.20 in the next 3 bars", "Book half at 176.80", "10:45 · scanradar.tech"]


def test_day_view_cuts_at_the_minute_but_keeps_the_review_and_spend():
    rows = [{"kind": "verdict", "ts": "2026-10-19T10:45:00+05:30", "cost_inr": 1.0, "output": {"call": "take"}},
            {"kind": "verdict", "ts": "2026-10-19T11:05:00+05:30", "cost_inr": 1.0, "output": {"call": "skip"}},
            {"kind": "brief", "ts": "2026-10-19T11:00:00+05:30", "cost_inr": 0.5, "output": {}},
            {"kind": "review", "ts": "2026-10-19T15:40:00+05:30", "cost_inr": 3.0, "output": {}}]
    v = desk.day_view(rows, "2026-10-19T10:50:00+05:30")
    assert [x["ts"][11:16] for x in v["verdicts"]] == ["10:45"] and v["briefs"] == [] and v["review"]["kind"] == "review"
    assert v["spend"] == 5.5 and v["calls"] == {"take": 1, "wait": 0, "skip": 0}
    assert [x["ts"][11:16] for x in desk.day_view(rows)["verdicts"]] == ["11:05", "10:45"]


def test_pnl_for_at_size():
    assert desk.pnl_for({"hit": "target"}, "long", 100, 98, 104, 500) == 2000
    assert desk.pnl_for({"hit": "stop"}, "short", 100, 102, 96, 500) == -1000
    assert desk.pnl_for({"hit": "none", "r60": -0.5}, "long", 100, 98, 104, 500) == -250
    assert desk.pnl_for(None, "long", 100, 98, 104, 500) is None and desk.pnl_for({"hit": "target"}, "long", 100, 98, 104, 0) is None


def test_review_context_sums_pnl_by_bucket():
    pats = [{"symbol": "A", "kind": "double_bottom", "side": "long", "score": 4, "triggered_at": "2026-10-19T10:00:00+05:30", "entry": 100, "stop": 98, "target": 104, "outcome": {"hit": "target", "r30": 1}},
            {"symbol": "B", "kind": "double_top", "side": "short", "score": 2, "triggered_at": "2026-10-19T11:00:00+05:30", "entry": 100, "stop": 102, "target": 96, "outcome": {"hit": "stop", "r30": -1}},
            {"symbol": "C", "kind": "double_top", "side": "short", "score": 5, "triggered_at": None}]
    vds = [{"ts": "2026-10-19T10:00:00+05:30", "context": {"symbol": "A", "side": "long", "trigger": {"label": "double bottom"}, "ticket": {"entry": 100, "stop": 98, "target": 104, "size": {"qty": 500}}},
            "output": {"call": "skip", "confidence": 6, "reasons": [{"mark": "no", "text": "x"}]}, "outcome": {"hit": "target"}}]
    ctx = desk.review_context(dt.date(2026, 10, 19), alerts=[], patterns=pats, verdicts=vds, lots={"A": 500, "B": 500}, candles={"minutes_with_candles": 360}, spend=41.2)
    assert len(ctx["patterns_triggered"]) == 2
    assert ctx["pnl_summary"]["all_pattern_triggers"] == 4000 - 2000 and ctx["pnl_summary"]["patterns_score_ge3"] == 4000   # 2 lots each
    assert ctx["pnl_summary"]["verdict_skip_if_taken"] == 2000 and ctx["verdicts"][0]["hit"] == "target"


def test_run_job_records_budget_stop_without_calling(monkeypatch):
    class Cur:
        def __init__(self): self.rows = []
        def execute(self, sql, params=None): self.rows.append(params)
        def fetchone(self): return (7,)
    class R:
        store = {}
        def get(self, k): return self.store.get(k)
        def incrbyfloat(self, k, v): self.store[k] = float(self.store.get(k, 0)) + v; return self.store[k]
        def expire(self, k, s): pass
    called = []
    monkeypatch.setattr(desk, "call_model", lambda *a, **k: called.append(1) or {"output": {"x": 1}, "usage": {"input_tokens": 10, "output_tokens": 5}, "model": "claude-opus-5-5", "latency_ms": 9})
    r = R(); r.store[desk.spend_key("2026-10-19")] = "100"
    cur = Cur()
    rec = desk.run_job(cur, r, day="2026-10-19", kind="verdict", ts="t", ref="x", symbol="A", system="s", context={}, schema={}, model="m")
    assert rec["error"].startswith("budget reached") and not called and rec["id"] == 7
    r.store[desk.spend_key("2026-10-19")] = "10"
    rec = desk.run_job(cur, r, day="2026-10-19", kind="verdict", ts="t", ref="x", symbol="A", system="s", context={}, schema={}, model="m")
    assert called and rec["output"] == {"x": 1} and rec["cost_inr"] > 0 and float(r.store[desk.spend_key("2026-10-19")]) > 10


def test_run_job_records_declines_and_errors(monkeypatch):
    class Cur:
        def execute(self, *a): pass
        def fetchone(self): return (1,)
    class R:
        def get(self, k): return "0"
        def incrbyfloat(self, k, v): return v
        def expire(self, *a): pass
    def declined(*a, **k): raise desk.DeskDeclined("general_harms", {"usage": {"input_tokens": 5}, "model": "claude-opus-5-5", "latency_ms": 3})
    monkeypatch.setattr(desk, "call_model", declined)
    rec = desk.run_job(Cur(), R(), day="d", kind="verdict", ts="t", ref="x", symbol="A", system="s", context={}, schema={}, model="m")
    assert rec["error"] == "declined: general_harms" and rec["output"] is None
    def boom(*a, **k): raise RuntimeError("network down")
    monkeypatch.setattr(desk, "call_model", boom)
    rec = desk.run_job(Cur(), R(), day="d", kind="verdict", ts="t", ref="x", symbol="A", system="s", context={}, schema={}, model="m")
    assert rec["error"] == "RuntimeError: network down"


def test_live_desk_schedule():
    assert live_desk.should_run(dt.datetime(2026, 10, 18, 10, 0, tzinfo=IST)) is False      # Sunday
    assert live_desk.should_run(dt.datetime(2026, 10, 19, 9, 19, tzinfo=IST)) is False
    assert live_desk.should_run(dt.datetime(2026, 10, 19, 15, 41, tzinfo=IST)) is True       # review window
    assert live_desk.should_run(dt.datetime(2026, 10, 19, 15, 46, tzinfo=IST)) is False
    due = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in range(0, 60) if live_desk.brief_due(dt.datetime(2026, 10, 19, h, m, tzinfo=IST))]
    assert due[:3] == ["09:30", "09:45", "10:00"] and due[-1] == "15:15" and len(due) == 24


def test_gate_and_pattern_ref():
    assert live_desk.gate(0, 0) is True                        # defaults: every trigger
    assert live_desk.pattern_ref({"symbol": "X", "kind": "double_top", "formed_at": "2026-10-19T10:05:00+05:30"}) == "pattern:X|double_top|10:05"
    rec = live_desk.pattern_record({"kind": "double_bottom", "score": 4}, {"double_bottom": {"hit_rate": 67.3, "triggers": 340, "avg_r30": 0.01, "median_tt_min": 20, "by_score": {">=3": {"hit_rate": 70.0, "triggers": 200}, "<3": {"hit_rate": 58.0, "triggers": 140}}}})
    assert rec["same_score_bucket"] == {"hit_rate_pct": 70.0, "triggers": 200} and rec["hit_rate_pct"] == 67.3
