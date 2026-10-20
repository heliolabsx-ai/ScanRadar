"""Feed watchdog decision (pure, no I/O): when is a connected socket "silent"?"""
import datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MARKET_OPEN, MARKET_CLOSE = dt.time(9, 15), dt.time(15, 30)
SILENT_AFTER_MIN = 10               # minutes of silence after the open before the feed is called silent


def feed_state(now: dt.datetime, ws_status: str | None, last_tick, silent_after: int = SILENT_AFTER_MIN) -> str | None:
    """'silent' when the socket says connected but no tick has arrived for `silent_after`
    minutes inside market hours (an NSE holiday, or a dead feed); None otherwise."""
    if ws_status != "connected" or not (MARKET_OPEN <= now.time() < MARKET_CLOSE):
        return None
    since = last_tick or dt.datetime.combine(now.date(), MARKET_OPEN, IST)
    return "silent" if (now - since).total_seconds() >= silent_after * 60 else None
