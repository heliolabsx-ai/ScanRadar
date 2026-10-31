import datetime as dt
import trades
from core.sizing import DEFAULTS

def test_trades_view_marks_rules_not_applied_before_go_live():
    v = trades.trades_view([], DEFAULTS, dt.date(2026, 10, 28), "15:30")
    assert v["applied"] is False and v["rules_from"] == "2026-11-03" and v["ledger"]["taken"] == 0
    assert trades.trades_view([], DEFAULTS, dt.date(2026, 11, 3), "10:00")["applied"] is True

def test_attach_trade_fields():
    by_ref = {"pattern:LT|triple_top|13:00": {"id": 4, "odds": {"verdict": "flat"}, "size": {"shares": 0, "risk_inr": 0}, "gate": "BLOCK", "gate_reason": "x"}}
    rows = trades.attach(by_ref, [{"symbol": "LT", "kind": "triple_top", "formed_at": "2026-10-28T13:00:00+05:30"}, {"symbol": "A", "kind": "flag", "formed_at": "2026-10-28T09:30:00+05:30"}], "pattern")
    assert rows[0]["odds"] == {"verdict": "flat"} and rows[0]["trade"] == {"id": 4, "shares": 0, "risk_inr": 0, "gate": "BLOCK", "gate_reason": "x"}
    assert rows[1]["odds"] is None and rows[1]["trade"] is None
    assert trades.attach({"alert:7": by_ref["pattern:LT|triple_top|13:00"]}, [{"id": 7, "scan": "B0101"}], "alert")[0]["trade"]["id"] == 4
