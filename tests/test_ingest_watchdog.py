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


def test_a_mid_session_restart_counts_silence_from_its_own_connect():
    # process restarted at 14:15, socket up at 14:18, no tick yet: not silent until 14:28
    assert feed_state(T(14, 18), "connected", None, ws_since=T(14, 18)) is None
    assert feed_state(T(14, 27), "connected", None, ws_since=T(14, 18)) is None
    assert feed_state(T(14, 28), "connected", None, ws_since=T(14, 18)) == "silent"
    assert feed_state(T(14, 40), "connected", T(14, 35), ws_since=T(14, 18)) is None      # ticks since


def test_watchdog_ignores_a_stale_connected_key_before_this_process_connects():
    # main.py passes ws=None until on_connect has run in this process; feed_state must then stay quiet
    assert feed_state(T(14, 18), None, None, ws_since=None) is None
