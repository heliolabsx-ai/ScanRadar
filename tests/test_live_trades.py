# tests/test_live_trades.py
import json, datetime as dt
import live_trades as lt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

def test_should_run_window():
    assert lt.should_run(dt.datetime(2026, 10, 28, 9, 20, tzinfo=IST)) and lt.should_run(dt.datetime(2026, 10, 28, 15, 30, tzinfo=IST))
    assert not lt.should_run(dt.datetime(2026, 10, 28, 15, 31, tzinfo=IST)) and not lt.should_run(dt.datetime(2026, 10, 31, 10, 0, tzinfo=IST))   # Saturday

def test_odds_line():
    row = {"odds": {"mc": {"target_pct": 39.4, "stop_pct": 41.5}, "verdict": "coin"}, "size": {"shares": 229}, "gate": "PASS", "gate_reason": ""}
    assert lt.odds_line(row) == "T1 39 % · stop 42 % · coin flip · 229 sh · PASS"
    assert lt.odds_line({**row, "gate": "BLOCK", "gate_reason": "5 trades today"}) == "T1 39 % · stop 42 % · coin flip · 229 sh · BLOCK: 5 trades today"
    assert lt.odds_line({**row, "odds": None}) == "no history · 229 sh · PASS"
    assert lt.odds_line({**row, "gate": None}) == "T1 39 % · stop 42 % · coin flip · 229 sh"
    assert lt.odds_line(None) == ""

class FakeR:
    def __init__(self, items, rows): self.items = list(items); self.rows = rows; self.pushed = []
    def lpop(self, k): return self.items.pop(0) if self.items else None
    def rpush(self, k, v): self.pushed.append(v)
    def expire(self, *a): pass
    def get(self, k): return self.rows.get(k)

def test_flush_deferred_appends_the_line_or_waits_or_gives_up():
    now = dt.datetime(2026, 10, 28, 10, 20, 25, tzinfo=IST)
    item = lambda ref, age_s: json.dumps({"ref": ref, "text": "<b>x</b>", "ts": (now - dt.timedelta(seconds=age_s)).isoformat()})
    R = FakeR([item("alert:7", 10), item("alert:8", 10), item("alert:9", 120), item("alert:10", 1900)],
              {"trades:2026-10-28:alert:7": json.dumps({"odds": None, "size": {"shares": 3}, "gate": "PASS", "gate_reason": ""})})
    sent = []
    assert lt.flush_deferred(R, dt.date(2026, 10, 28), sent.append, now) == 2
    assert sent == ["<b>x</b>\nno history · 3 sh · PASS", "<b>x</b>"]          # 7 with the line, 9 too old to wait, 10 dropped
    assert json.loads(R.pushed[0])["ref"] == "alert:8"                             # 8 waits for the next minute
