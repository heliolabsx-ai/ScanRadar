"""Kite historical_data with pacing and back-off, shared by the 08:55 ref load and the backfill."""
import time, logging
from kiteconnect.exceptions import NetworkException

log = logging.getLogger("hist")
HIST_PACE = 0.4   # seconds between calls; Kite allows 3 req/s per API key


def hist(kite, token, frm, to, interval, oi=False, tries=4):
    """Kite answers HTTP 429 with a JSON NetworkException("Too many requests")
    when the 3 req/s budget is exceeded; back off and retry a few times."""
    for attempt in range(1, tries + 1):
        try:
            out = kite.historical_data(int(token), frm, to, interval, oi=oi)
            time.sleep(HIST_PACE)
            return out
        except NetworkException as e:
            if attempt == tries:
                raise
            log.warning("historical %s throttled (%s); retry %d in %ds", token, e, attempt, 2 * attempt)
            time.sleep(2 * attempt)
