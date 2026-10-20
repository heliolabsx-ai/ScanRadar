import datetime as dt
from feed import feed_state

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T = lambda h, m: dt.datetime(2026, 10, 20, h, m, tzinfo=IST)   # noqa: E731


def test_feed_is_silent_only_when_connected_and_quiet_inside_market_hours():
    assert feed_state(T(9, 24), "connected", None) is None            # 9 minutes after the open: not yet
    assert feed_state(T(9, 25), "connected", None) == "silent"        # ten minutes of nothing
    assert feed_state(T(11, 0), "connected", T(10, 55)) is None        # ticked five minutes ago
    assert feed_state(T(11, 0), "connected", T(10, 45)) == "silent"    # fifteen minutes of nothing
    assert feed_state(T(11, 0), "disconnected", None) is None          # a closed socket is a different alarm
    assert feed_state(T(8, 30), "connected", None) is None             # pre-open
    assert feed_state(T(15, 40), "connected", T(15, 10)) is None       # after the close
