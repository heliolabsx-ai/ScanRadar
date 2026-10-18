import datetime as dt
from live_patterns import new_triggers, message, should_run

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def row(symbol="TATASTEEL", kind="double_bottom", formed="10:00", triggered="10:25", **kw):
    r = {"symbol": symbol, "kind": kind, "side": "long" if "bottom" in kind else "short",
         "formed_at": f"2026-10-19T{formed}:00+05:30", "triggered_at": f"2026-10-19T{triggered}:00+05:30" if triggered else None,
         "state": "triggered" if triggered else "forming", "entry": 174.9, "stop": 172.35, "target": 177.45, "rr": 1.0, "score": 4}
    r.update(kw)
    return r


def test_new_triggers_once():
    seen = set()
    first = new_triggers(seen, [row(), row(symbol="HDFCBANK", triggered=None)])
    assert [r["symbol"] for r in first] == ["TATASTEEL"]
    assert new_triggers(seen, [row()]) == []                                  # same trigger again → nothing
    assert [r["symbol"] for r in new_triggers(seen, [row(symbol="HDFCBANK", triggered="10:30")])] == ["HDFCBANK"]


def test_message_format():
    m = message(row())
    assert m == ("<b>Double bottom · BUY</b>\nTATASTEEL entry ₹174.90 · stop ₹172.35 · target ₹177.45 · R:R 1:1.0 · score 4/5\n"
                 "10:25 · scanradar.tech")
    assert message(row(kind="double_top", triggered="11:05")).startswith("<b>Double top · SELL</b>")


def test_should_run_windows():
    assert should_run(dt.datetime(2026, 10, 18, 10, 0, tzinfo=IST)) is False     # Sunday
    assert should_run(dt.datetime(2026, 10, 19, 9, 10, tzinfo=IST)) is False     # before 09:20
    assert should_run(dt.datetime(2026, 10, 19, 10, 0, tzinfo=IST)) is True
    assert should_run(dt.datetime(2026, 10, 19, 15, 25, tzinfo=IST)) is False    # after 15:20
