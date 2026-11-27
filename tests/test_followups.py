"""Follow-ups for digest alarms (api/followups.py): for the starred rows a note near the target and the exit, for the
group one quiet day result at flat-by; each sent once, through the deferred Telegram queue."""
import datetime as dt
import json
import followups as fu

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 11, 27)
SCANS = {"Inside open long": {"name": "Inside open long", "side": "long", "trade": {"stop": {"series": "day_low"}, "target": {"series": "prev_day_high"}}},
         "Inside open short": {"name": "Inside open short", "side": "short", "trade": {"stop": {"series": "day_high"}, "target": {"series": "prev_day_low"}}}}


def T(hhmm: str) -> dt.datetime:
    h, m = hhmm.split(":")
    return dt.datetime(2026, 11, 27, int(h), int(m), tzinfo=IST)


def meta(id, side="long", star=True, group="Inside open"):
    return {"id": id, "scan": f"Inside open {side}", "symbol": f"S{id}", "side": side, "source": "live",
            "digest": {"group": group, "star": star, "rank": 1, "of": 9, "strength": 0.3, "breadth": 48.0, "market": "mixed"}}


def row(id, side="long", entry=5659.0, stop=5624.5, target=5700.0, outcome=None, sym=None):
    return {"id": id, "ref": f"alert:{id}", "symbol": sym or f"S{id}", "side": side, "entry": entry, "stop": stop, "target": target, "outcome": outcome}


def out(hit, px, hhmm):
    return {"hit": hit, "exit_px": px, "settled_at": T(hhmm).isoformat()}


def test_progress_and_percent_read_the_same_for_both_sides():
    assert round(fu.progress(5659.0, 5700.0, 5691.8), 6) == 0.8 and fu.progress(5659.0, 5700.0, 5659.0) == 0.0
    assert round(fu.progress(3478.7, 3457.0, 3461.34), 2) == 0.8                       # a short: the target is below
    assert fu.progress(100.0, 100.0, 101.0) == 0.0                                     # no distance, no progress
    assert round(fu.gain("long", 5659.0, 5700.0), 2) == 0.72 and round(fu.gain("short", 3478.7, 3493.0), 2) == -0.41


def test_a_starred_open_row_gets_one_note_when_it_is_close_to_its_target():
    rows, metas = [row(1), row(2), row(3)], {"alert:1": meta(1), "alert:2": meta(2, star=False), "alert:3": meta(3)}
    items = fu.due(rows, metas, {"S1": 5692.0, "S2": 5699.0, "S3": 5680.0}, T("10:05"), False, "15:20", SCANS)
    assert [i["key"] for i in items] == ["alert:1:near"] and items[0]["silent"] is False     # 2 is not starred, 3 is not close yet
    assert items[0]["text"].splitlines() == ["<b>S1</b> is close to its target", "now 5,692.00 · PDH 5,700.00 · Inside open long", "10:05  ·  scanradar.tech"]


def test_a_starred_row_gets_its_exit_and_a_late_one_is_only_marked():
    rows = [row(1, outcome=out("target", 5700.0, "10:13")), row(2, side="short", entry=3478.7, stop=3493.0, target=3457.0, outcome=out("stop", 3493.0, "10:08"), sym="M&M"),
            row(3, outcome=out("none", 5650.0, "15:20")), row(4, outcome=out("target", 5700.0, "09:40"))]
    metas = {"alert:1": meta(1), "alert:2": meta(2, side="short"), "alert:3": meta(3), "alert:4": meta(4)}
    items = {i["key"]: i for i in fu.due(rows, metas, {}, T("10:14"), False, "15:20", SCANS)}
    assert set(items) == {"alert:1:exit", "alert:2:exit", "alert:4:exit"}              # flat at the close is in the day result, not a message
    assert items["alert:1:exit"]["text"].splitlines() == ["<b>TARGET</b> · <b>S1</b>", "PDH 5,700.00 reached at 10:13 · +0.72 %", "Inside open long  ·  scanradar.tech"]
    assert items["alert:2:exit"]["text"].splitlines() == ["<b>STOP</b> · <b>M&amp;M</b>", "3,493.00 hit at 10:08 · −0.41 %", "Inside open short  ·  scanradar.tech"]
    assert not items["alert:1:exit"].get("skip") and items["alert:4:exit"]["skip"]     # 34 minutes old: the API was away, do not ring late


def test_the_day_result_counts_the_whole_group_and_names_the_starred():
    rows = [row(1, outcome=out("target", 5700.0, "10:13")), row(2, outcome=out("stop", 5624.5, "09:33")),
            row(3, outcome=out("none", 5670.0, "15:20")), row(4, outcome=out("stop", 5624.5, "11:00")), row(5)]
    metas = {f"alert:{i}": meta(i, star=i in (1, 2)) for i in range(1, 6)}
    items = [i for i in fu.due(rows, metas, {}, T("15:20"), True, "15:20", SCANS) if i["key"].startswith("group:")]
    assert len(items) == 1 and items[0]["key"] == "group:Inside open:result" and items[0]["silent"] is True and not items[0].get("skip")
    # gross: +0.72, -0.61, +0.19, -0.61 -> mean -0.0755, less 0.10 costs
    assert items[0]["text"].splitlines() == ["<b>Inside open · day result</b>",
                                             "5 alarms · 1 target · 2 stopped · 1 flat at 15:20 · 1 still open",
                                             "average −0.18 % a trade after costs",
                                             "★ strongest 2: 1 target · 1 stopped · −0.04 %"]
    assert fu.due(rows, metas, {}, T("15:19"), False, "15:20", SCANS)[-1]["key"] != "group:Inside open:result"
    late = [i for i in fu.due(rows, metas, {}, T("15:50"), True, "15:20", SCANS) if i["key"].startswith("group:")]
    assert late[0]["skip"]


class FakeR:
    def __init__(self, fail=0): self.kv, self.q, self.fail = {}, [], fail
    def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True
    def rpush(self, k, v):
        if self.fail:
            self.fail -= 1
            raise ConnectionError("redis hiccup")
        self.q.append((k, json.loads(v)))
    def expire(self, *a): pass
    def delete(self, k): self.kv.pop(k, None)


def test_send_once_queues_with_an_empty_odds_key_and_never_twice():
    R = FakeR()
    items = [{"key": "alert:1:exit", "text": "<b>TARGET</b>", "silent": False}, {"key": "alert:4:exit", "text": "old", "silent": False, "skip": True},
             {"key": "group:Inside open:result", "text": "<b>day</b>", "silent": True}]
    assert fu.send_once(R, DAY, T("10:14"), items) == 2
    assert [(k, v["ref"], v["text"], v["silent"]) for k, v in R.q] == [("tg:defer:2026-11-27", "followup:alert:1:exit", "<b>TARGET</b>", False),
                                                                          ("tg:defer:2026-11-27", "followup:group:Inside open:result", "<b>day</b>", True)]
    assert R.kv["trades:2026-11-27:followup:alert:1:exit"] == ""                       # the flush sends it at once, plain
    assert "fu:2026-11-27:alert:4:exit" in R.kv                                        # the late one is marked, so it is never looked at again
    assert fu.send_once(R, DAY, T("10:15"), items) == 0 and len(R.q) == 2


def test_a_message_that_could_not_be_queued_is_tried_again_the_next_minute():
    R = FakeR(fail=1)
    item = [{"key": "alert:1:exit", "text": "<b>TARGET</b>", "silent": False}]
    assert fu.send_once(R, DAY, T("10:14"), item) == 0 and "fu:2026-11-27:alert:1:exit" not in R.kv      # the mark is taken back
    assert fu.send_once(R, DAY, T("10:15"), item) == 1 and len(R.q) == 1
    assert fu.send_once(R, DAY, T("10:16"), item) == 0


def test_run_reads_live_digest_alerts_and_ignores_a_bar_from_before_the_alarm(monkeypatch):
    import alerts as al
    import trades
    alerts = [dict(meta(1), ts="2026-11-27T09:20:02+05:30"), dict(meta(2, star=False), ts="2026-11-27T09:20:02+05:30"),
              dict(meta(3), source="replay"), {"id": 4, "scan": "B0101", "symbol": "S4", "source": "live", "digest": None}]
    rows = [dict(row(1), ts="2026-11-27T09:20:00+05:30"), dict(row(2), ts="2026-11-27T09:20:00+05:30"), dict(row(3), ts="2026-11-27T09:20:00+05:30"),
            dict(row(4), ts="2026-11-27T09:20:00+05:30")]
    monkeypatch.setattr(al, "alerts_for_day", lambda con, day: alerts)
    monkeypatch.setattr(trades, "rows_for_day", lambda con, day, upto=None: rows)
    bars = {}
    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params): self.symbols = params[0]
        def fetchall(self): return [(s, *bars[s]) for s in self.symbols if s in bars]
    class Con:
        def cursor(self): return Cur()
    # 09:20:20: the only closed bar is 09:19, from before the alarm, and it happens to sit near the target: no note
    bars["S1"] = (T("09:19"), 5695.0)
    R = FakeR()
    assert fu.run(Con(), R, DAY, T("09:20"), "15:20", SCANS) == 0 and R.q == []
    # 09:26: the last bar is after the alarm and 80 % of the way: one note, for the starred live row only
    bars["S1"] = (T("09:25"), 5692.0)
    assert fu.run(Con(), R, DAY, T("09:26"), "15:20", SCANS) == 1
    assert [v["ref"] for _, v in R.q] == ["followup:alert:1:near"] and "now 5,692.00" in R.q[0][1]["text"]
    assert fu.run(Con(), R, DAY, T("09:27"), "15:20", SCANS) == 0                    # once
    monkeypatch.setattr(al, "alerts_for_day", lambda con, day: [a for a in alerts if not a.get("digest")])
    assert fu.run(Con(), FakeR(), DAY, T("09:27"), "15:20", SCANS) == 0              # a day without digest alerts costs one query
