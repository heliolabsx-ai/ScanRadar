import datetime as dt
import pandas as pd
from alerts import ist_day_bounds, merge_live_replay, ensure_replay_alerts, ensure_outcomes, side_levels, SETTLED
from conftest import IST as IST_NAME


def T(hhmm: str) -> dt.datetime:
    h, m = hhmm.split(":")
    return dt.datetime(2026, 10, 16, int(h), int(m), tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))


def test_ist_day_bounds():
    a, b = ist_day_bounds(dt.date(2026, 10, 16))
    assert a.isoformat() == "2026-10-16T00:00:00+05:30" and (b - a) == dt.timedelta(days=1)


def test_merge_live_replay_prefers_live_within_one_minute():
    rows = [{"scan": "s", "symbol": "X", "ts": T("10:05"), "source": "live"},
            {"scan": "s", "symbol": "X", "ts": T("10:06"), "source": "replay"},
            {"scan": "s", "symbol": "X", "ts": T("10:40"), "source": "replay"},
            {"scan": "s", "symbol": "Y", "ts": T("10:06"), "source": "replay"}]
    assert [(r["symbol"], r["ts"]) for r in merge_live_replay(rows)] == [("X", T("10:05")), ("X", T("10:40")), ("Y", T("10:06"))]


class RecordingCursor:
    def __init__(self): self.executed = []
    def execute(self, sql, params=None): self.executed.append(sql)
    def executemany(self, sql, rows): self.executed.append(sql)
    def fetchall(self): return []
    def fetchone(self): return (0,)
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeCon:
    def __init__(self): self.cur = RecordingCursor(); self.commits = 0
    def cursor(self): return self.cur
    def commit(self): self.commits += 1


def test_ensure_replay_alerts_skips_open_day():
    con = FakeCon()
    today = dt.date(2026, 10, 19)
    ensure_replay_alerts(con, today, frames={}, refs={}, scans=[], now=dt.datetime.combine(today, dt.time(11, 0), tzinfo=T("09:15").tzinfo))
    assert con.cur.executed == [] and con.commits == 0


def test_ensure_outcomes_skips_open_day():
    con = FakeCon()
    today = dt.date(2026, 10, 19)
    ensure_outcomes(con, today, frames={}, refs={}, scans=[], now=dt.datetime.combine(today, dt.time(11, 0), tzinfo=T("09:15").tzinfo))
    assert con.cur.executed == []


def test_settled_time_is_1535():
    assert SETTLED == dt.time(15, 35)


def test_side_levels_follow_scan_side_not_analysis_direction():
    a = {"dir": 1, "stop": 95.0, "target": 110.0, "S1": 96.0, "S2": 92.0, "R1": 104.0, "R2": 108.0,
         "ob": {"lo": 97.0, "hi": 99.0, "dir": 1}, "price": 100.0}
    assert side_levels(a, "long") == (95.0, 110.0)             # agrees with analysis: keep its levels
    assert side_levels(a, "short") == (104.0, 92.0)            # short: stop max(R1, ob.hi), target S2
    a2 = dict(a, dir=-1, stop=104.0, target=92.0)
    assert side_levels(a2, "long") == (96.0, 108.0)            # long against a bearish analysis: min(S1, ob.lo), R2
