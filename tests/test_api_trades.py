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


def test_attach_leaves_input_rows_unmutated():
    by_ref = {"alert:7": {"id": 4, "odds": {"verdict": "flat"}, "size": {"shares": 3, "risk_inr": 90}, "gate": "PASS", "gate_reason": None}}
    rows = [{"id": 7, "scan": "B0101"}, {"id": 8, "scan": "B0101"}]
    out = trades.attach(by_ref, rows, "alert")
    assert rows == [{"id": 7, "scan": "B0101"}, {"id": 8, "scan": "B0101"}]
    assert out[0] is not rows[0] and out[0]["trade"]["shares"] == 3


class _Cur:
    def __init__(self, recs): self.recs, self.calls = recs, []
    def execute(self, sql, params=None): self.calls.append((sql, params))
    def fetchall(self): return self.recs
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Con:
    def __init__(self, recs): self.cur = _Cur(recs)
    def cursor(self): return self.cur


def test_odds_by_ref_cuts_at_the_as_of_minute():
    day = dt.date(2026, 10, 28)
    at = dt.datetime(2026, 10, 28, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    con = _Con([("pattern:LT|triple_top|11:00", 4, {"v": 1}, {"shares": 1}, "PASS", None)])
    by_ref = trades.odds_by_ref(con, day, at)
    sql, params = con.cur.calls[-1]
    assert "ts <= %s" in sql and params == (day, at)
    assert by_ref["pattern:LT|triple_top|11:00"]["id"] == 4
    trades.odds_by_ref(con, day)
    sql, params = con.cur.calls[-1]
    assert "ts <=" not in sql and params == (day,)


def test_recent_alerts_carry_the_id_for_the_odds_join():
    import alerts as al
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    con = _Con([(7, dt.datetime(2026, 10, 28, 10, 20, tzinfo=ist), "B0101", "KFINTECH", 854.6, {"tf": "5m", "side": "long"}, "live"),
                (6, dt.datetime(2026, 10, 28, 10, 20, 30, tzinfo=ist), "B0101", "KFINTECH", 854.0, {"tf": "5m"}, "replay")])
    rows = al.recent_alerts(con, 100)
    sql, params = con.cur.calls[-1]
    assert sql.startswith("SELECT id,") and params == (200,)
    assert [r["id"] for r in rows] == [7]                                          # the replay twin merged away
    by_ref = {"alert:7": {"id": 4, "odds": {"verdict": "edge"}, "size": {"shares": 3, "risk_inr": 90}, "gate": "PASS", "gate_reason": None}}
    out = trades.attach(by_ref, rows, "alert")
    assert out[0]["odds"] == {"verdict": "edge"} and out[0]["trade"]["shares"] == 3
