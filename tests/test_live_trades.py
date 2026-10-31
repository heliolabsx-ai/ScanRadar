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


def test_tick_gates_on_the_minute_so_15_30_runs(monkeypatch):
    class Empty:
        def lpop(self, k): return None
    monkeypatch.setattr(lt.live_desk, "desk_redis", lambda: Empty())
    opened = []
    def connect():
        opened.append(1)
        raise RuntimeError("stop after the gate")
    lt.tick(connect, dt.datetime(2026, 10, 28, 15, 30, 20, tzinfo=IST), lambda t: None)
    assert opened == [1]                                                           # the 15:30 minute runs at ~:20 s
    lt.tick(connect, dt.datetime(2026, 10, 28, 15, 31, 0, tzinfo=IST), lambda t: None)
    assert opened == [1]                                                           # 15:31 is outside the window


class FlakyR(FakeR):
    def __init__(self, items, rows, bad_ref=None, lpop_fails_after=None):
        super().__init__(items, rows); self.bad_ref = bad_ref; self.lpop_fails_after = lpop_fails_after; self.lpops = 0
    def lpop(self, k):
        self.lpops += 1
        if self.lpop_fails_after is not None and self.lpops > self.lpop_fails_after:
            raise ConnectionError("redis gone")
        return super().lpop(k)
    def get(self, k):
        if self.bad_ref and k.endswith(":" + self.bad_ref):
            raise ConnectionError("redis hiccup")
        return super().get(k)


def test_flush_deferred_sends_plain_when_the_row_lookup_fails():
    now = dt.datetime(2026, 10, 28, 10, 20, 25, tzinfo=IST)
    item = lambda ref, age_s, text: json.dumps({"ref": ref, "text": text, "ts": (now - dt.timedelta(seconds=age_s)).isoformat()})
    R = FlakyR([item("alert:1", 10, "a"), item("alert:2", 10, "b"), item("alert:3", 10, "c")],
               {"trades:2026-10-28:alert:2": json.dumps({"odds": None, "size": {"shares": 3}, "gate": None, "gate_reason": None})}, bad_ref="alert:1")
    sent = []
    assert lt.flush_deferred(R, dt.date(2026, 10, 28), sent.append, now) == 2
    assert sent == ["a", "b\nno history · 3 sh"]                                  # 1 plain after the failed lookup, 2 with its line
    assert [json.loads(p)["ref"] for p in R.pushed] == ["alert:3"]                 # 3 still waits


def test_flush_deferred_pushes_waiting_items_back_even_when_redis_fails_mid_flush():
    import pytest
    now = dt.datetime(2026, 10, 28, 10, 20, 25, tzinfo=IST)
    item = lambda ref: json.dumps({"ref": ref, "text": "t", "ts": (now - dt.timedelta(seconds=10)).isoformat()})
    R = FlakyR([item("alert:1"), item("alert:2"), item("alert:3")], {}, lpop_fails_after=2)
    with pytest.raises(ConnectionError):
        lt.flush_deferred(R, dt.date(2026, 10, 28), lambda t: None, now)
    assert [json.loads(p)["ref"] for p in R.pushed] == ["alert:1", "alert:2"]      # nothing popped is lost


class StoreR:
    def __init__(self, fail_set=False): self.kv = {}; self.pub = []; self.fail_set = fail_set
    def set(self, k, v, ex=None):
        if self.fail_set:
            raise ConnectionError("redis hiccup")
        self.kv[k] = (v, ex)
    def exists(self, k): return int(k in self.kv)
    def publish(self, c, m): self.pub.append((c, m))


class FakeCon:
    def cursor(self):
        class C:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return C()
    def commit(self): pass
    def rollback(self): pass


def test_process_minute_backfills_missing_odds_keys(monkeypatch):
    from core.sizing import DEFAULTS
    day, at = dt.date(2026, 10, 28), dt.datetime(2026, 10, 28, 10, 20, tzinfo=IST)
    stored = {"alert:1": {"id": 1, "odds": None, "size": {"shares": 4}, "gate": "PASS", "gate_reason": ""}}
    new = {"odds": None, "size": {"shares": 5}, "gate": None, "gate_reason": None}
    monkeypatch.setattr(lt.live_desk, "setup_stats", lambda con, at: ({}, {}))
    monkeypatch.setattr(lt.al, "scan_files", lambda: [])
    monkeypatch.setattr(lt.trades, "candidates", lambda con, day, at, **kw: [{"ref": "alert:1", "symbol": "A"}, {"ref": "alert:2", "symbol": "B"}])
    monkeypatch.setattr(lt.trades, "rows_for_day", lambda con, day, upto=None: [])
    monkeypatch.setattr(lt.trades, "load_history", lambda con, s, d: {})
    monkeypatch.setattr(lt.trades, "build_row", lambda c, s, st, h, apply_rules: dict(new))
    def insert(cur, row):
        stored["alert:2"] = {"id": 2, **row}
        return 2
    monkeypatch.setattr(lt.trades, "insert_row", insert)
    monkeypatch.setattr(lt.trades, "odds_by_ref", lambda con, day: {k: dict(v) for k, v in stored.items()})
    monkeypatch.setattr(lt.trades, "settle_open", lambda con, day, at, flat_by, final: 0)
    R = StoreR()
    assert lt.process_minute(FakeCon(), R, day, at, dict(DEFAULTS)) == {"rows": 1, "settled": 0}
    assert json.loads(R.kv["trades:2026-10-28:alert:2"][0]) == new                 # the new row
    assert json.loads(R.kv["trades:2026-10-28:alert:1"][0]) == {"odds": None, "size": {"shares": 4}, "gate": "PASS", "gate_reason": ""}   # an older row whose key was missing
    assert {ex for _, ex in R.kv.values()} == {86400}
    assert R.pub == [("trades:updated", json.dumps({"at": "10:20", "new": 1}))]
    del stored["alert:2"]
    R2 = StoreR(fail_set=True)
    assert lt.process_minute(FakeCon(), R2, day, at, dict(DEFAULTS))["rows"] == 1  # a failed key write never stops the minute
