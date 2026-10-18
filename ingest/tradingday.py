"""Is this a day the scheduler should log in and stream? Weekends never; NSE holidays
when listed (HOLIDAYS below or the NSE_HOLIDAYS env var, comma-separated YYYY-MM-DD)."""
import os, datetime as dt

HOLIDAYS: set = set()


def _env_holidays() -> set:
    raw = os.environ.get("NSE_HOLIDAYS", "")
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                out.add(dt.date.fromisoformat(part))
            except ValueError:
                pass
    return out


def is_trading_day(day: dt.date, holidays: set | None = None) -> bool:
    if day.weekday() >= 5:
        return False
    hol = HOLIDAYS | _env_holidays() if holidays is None else holidays
    return day not in hol
