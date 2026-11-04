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


class RowsCursor(RecordingCursor):
    def __init__(self, rows): super().__init__(); self.rows = rows
    def fetchall(self): return self.rows


class RowsCon(FakeCon):
    def __init__(self, rows): self.cur = RowsCursor(rows); self.commits = 0


def test_ensure_outcomes_marks_ungradable_rows_null():
    con = RowsCon([(7, T("10:05"), "deleted scan", "X", 100.0, {"tf": "5m"})])
    settled = dt.datetime(2026, 10, 17, 9, 0, tzinfo=T("09:15").tzinfo)
    ensure_outcomes(con, dt.date(2026, 10, 16), frames={"X": object()}, refs={"X": {"prev_close": 1}}, scans=[], now=settled)
    updates = [q for q in con.cur.executed if q.lstrip().startswith("UPDATE")]
    assert len(updates) == 1 and con.commits == 1


def test_ensure_replay_alerts_deletes_rows_of_removed_scans():
    con = RowsCon([("old scan", "abc")])
    settled = dt.datetime(2026, 10, 17, 9, 0, tzinfo=T("09:15").tzinfo)
    ensure_replay_alerts(con, dt.date(2026, 10, 16), frames={"X": object()}, refs={}, scans=[], now=settled)
    assert any(q.lstrip().startswith("DELETE") for q in con.cur.executed)


def test_aggregate_stats_dedups_live_replay_and_tolerates_null():
    from alerts import aggregate_stats
    rows = [
        {"ts": T("10:05"), "scan": "s", "symbol": "X", "source": "live", "outcome": {"hit": "target", "r30": 1.0, "r60": 2.0}},
        {"ts": T("10:05"), "scan": "s", "symbol": "X", "source": "replay", "outcome": {"hit": "target", "r30": 1.0, "r60": 2.0}},
        {"ts": T("11:00"), "scan": "s", "symbol": "Y", "source": "replay", "outcome": {"hit": "stop", "r30": -1.0, "r60": None}},
        {"ts": T("12:00"), "scan": "s", "symbol": "Z", "source": "replay", "outcome": None},
    ]
    st = aggregate_stats(rows)
    assert st == [{"scan": "s", "fires": 3, "hit_rate": 50.0, "avg_r30": 0.0, "avg_r60": 2.0}]


def _scan_dir(tmp_path):
    import json as _json
    (tmp_path / "a_good.json").write_text(_json.dumps({"name": "good", "side": "long", "rules": [], "min_rr": 0}))
    (tmp_path / "b_bad_hours.json").write_text(_json.dumps({"name": "bad hours", "rules": [], "hours": {"from": "11:00", "to": "09:20"}}))
    (tmp_path / "c_bad_rr.json").write_text(_json.dumps({"name": "bad rr", "rules": [], "min_rr": "x"}))
    (tmp_path / "d_not_json.json").write_text("{")
    return tmp_path


def test_scan_files_skips_invalid_scans(tmp_path, monkeypatch, caplog):
    import alerts
    monkeypatch.setattr(alerts, "SCAN_DIR", str(_scan_dir(tmp_path)))
    with caplog.at_level("WARNING", logger="alerts"):
        got = alerts.scan_files()
    assert [s["name"] for s, _ in got] == ["good"]
    assert all(f in caplog.text for f in ("b_bad_hours.json", "c_bad_rr.json", "d_not_json.json"))


def test_scan_hash_covers_the_alert_schema(tmp_path, monkeypatch):
    import alerts
    monkeypatch.setattr(alerts, "SCAN_DIR", str(_scan_dir(tmp_path)))
    h1 = alerts.scan_files()[0][1]
    assert h1 == alerts.scan_files()[0][1]                     # stable for the same build
    monkeypatch.setattr(alerts, "ALERT_SCHEMA", "test-other")
    assert alerts.scan_files()[0][1] != h1                      # a schema bump invalidates every stored replay row


class _Cur:
    def __init__(self, recs): self.recs, self.calls = recs, []
    def execute(self, sql, params=None): self.calls.append((sql, params))
    def fetchall(self): return self.recs
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Con:
    def __init__(self, recs): self.cur = _Cur(recs)
    def cursor(self): return self.cur


def test_symbol_history_counts_trading_sessions_back_from_before_and_merges_replay_twins():
    import alerts as al
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    now = dt.datetime(2026, 11, 4, 22, 0, tzinfo=ist)                      # a Wednesday
    recs = [(9, dt.datetime(2026, 11, 3, 10, 4, tzinfo=ist), "VWAP reclaim long", "JSWENERGY", 503.35, {"side": "long", "outcome": {"hit": "target"}}, "live"),
            (8, dt.datetime(2026, 11, 3, 10, 4, 30, tzinfo=ist), "VWAP reclaim long", "JSWENERGY", 503.3, {"side": "long"}, "replay"),
            (7, dt.datetime(2026, 11, 2, 15, 15, tzinfo=ist), "PDH break", "JSWENERGY", 511.0, {"side": "short", "story": {"line": 510.85}}, "live")]
    con = _Con(recs)
    rows = al.symbol_history(con, "JSWENERGY", 2, now=now)
    sql, params = con.cur.calls[-1]
    assert "symbol=%s" in sql and params[0] == "JSWENERGY"
    assert params[1] == dt.datetime(2026, 11, 2, 0, 0, tzinfo=ist)           # two sessions back: Tue 3 Nov, Mon 2 Nov
    assert params[2] == dt.datetime(2026, 11, 4, 0, 0, tzinfo=ist)           # strictly before today
    assert [r["id"] for r in rows] == [9, 7]                                 # the replay twin merged away
    assert rows[0]["outcome"] == {"hit": "target"} and rows[1]["story"] == {"line": 510.85} and rows[0]["ts"].startswith("2026-11-03T10:04")
    con2 = _Con([])
    assert al.symbol_history(con2, "X", 5, before=dt.date(2026, 11, 2), now=now) == []
    _, params = con2.cur.calls[-1]
    assert params[1] == dt.datetime(2026, 10, 26, 0, 0, tzinfo=ist)          # five sessions before Mon 2 Nov skip the weekend
    assert params[2] == dt.datetime(2026, 11, 2, 0, 0, tzinfo=ist)
