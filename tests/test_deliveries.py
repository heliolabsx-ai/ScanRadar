# tests/test_deliveries.py
import datetime as dt
import deliveries

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class FakeCon:
    """Cursor that records execute(sql, params) and answers fetchall() from `rows`."""
    def __init__(self, rows=()):
        self.rows, self.executed = list(rows), []

    def cursor(self):
        con = self

        class C:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params=None): con.executed.append((sql, params))
            def fetchall(self): return con.rows
        return C()


def test_record_inserts_one_row():
    con = FakeCon()
    day, at = dt.date(2026, 11, 3), dt.datetime(2026, 11, 3, 9, 35, tzinfo=IST)
    deliveries.record(con, day, "alert:7", "telegram", False, "not configured", True, at)
    assert len(con.executed) == 1
    sql, params = con.executed[0]
    assert "INSERT INTO deliveries" in sql
    assert tuple(params) == (day, "alert:7", "telegram", at, False, "not configured", True)


def test_for_day_latest_telegram_only():
    t = lambda m: dt.datetime(2026, 11, 3, 9, m, tzinfo=IST)
    # the query is ordered by sent_at, so the later row overwrites the earlier one
    con = FakeCon([("a", t(35), True, None, False), ("a", t(36), False, "timeout", True)])
    out = deliveries.for_day(con, dt.date(2026, 11, 3))
    sql, params = con.executed[0]
    assert "channel = 'telegram'" in sql and "ORDER BY" in sql and tuple(params) == (dt.date(2026, 11, 3),)
    assert out == {"a": {"sent_at": t(36).isoformat(), "ok": False, "error": "timeout", "silent": True}}
