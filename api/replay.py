"""Replay: the ranking snapshot for a stored day as of any minute, built with the
same core/ code the live engine uses (see core.asof for the as-of definition)."""
import datetime as dt
import pandas as pd
from core.asof import view_at
from core.ranking import rank

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
OPEN, CLOSE = dt.time(9, 15), dt.time(15, 30)
COLS = ["symbol", "ts", "open", "high", "low", "close", "volume", "oi", "delta"]


SETTLED = dt.time(15, 35)            # a day's replay may be cached/prepared for good from this time on


def day_is_settled(day: dt.date, now: dt.datetime) -> bool:
    n = now.astimezone(IST)
    return day < n.date() or (day == n.date() and n.timetz().replace(tzinfo=None) >= SETTLED)


class ReplayError(Exception):
    """Client error (mapped to HTTP by the route). Kept free of FastAPI so the
    helpers import in plain unit tests."""
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def parse_replay_args(day: str, at: str) -> tuple[dt.date, dt.datetime]:
    try:
        d = dt.date.fromisoformat(day)
    except (TypeError, ValueError):
        raise ReplayError(400, "day must be YYYY-MM-DD")
    if d.weekday() > 4:
        raise ReplayError(400, f"{d} is a weekend; pick a trading day")
    try:
        t = dt.datetime.strptime(at, "%H:%M").time()
    except (TypeError, ValueError):
        raise ReplayError(400, "at must be HH:MM")
    if not (OPEN <= t <= CLOSE):
        raise ReplayError(400, "at must be between 09:15 and 15:30")
    return d, dt.datetime.combine(d, t, IST)


def session_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, OPEN, IST)
    return start, start + dt.timedelta(hours=6, minutes=16)     # 09:15 .. 15:31


def load_day(con, day: dt.date, upto: dt.datetime | None = None) -> dict[str, pd.DataFrame]:
    """1-minute candles of every symbol for the IST session, optionally only ts <= upto."""
    start, end = session_bounds(day)
    sql = "SELECT symbol, ts, open, high, low, close, volume, oi, delta FROM candles_1m WHERE ts >= %s AND ts < %s"
    params: list = [start, end]
    if upto is not None:
        sql += " AND ts <= %s"
        params.append(upto)
    with con.cursor() as cur:
        cur.execute(sql + " ORDER BY symbol, ts", params)
        rows = cur.fetchall()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=COLS)
    df["ts"] = pd.to_datetime(df.ts, utc=True).dt.tz_convert("Asia/Kolkata")
    return {s: g.drop(columns="symbol").reset_index(drop=True) for s, g in df.groupby("symbol")}


def load_refs(con, day: dt.date) -> dict[str, dict]:
    with con.cursor() as cur:
        cur.execute("""SELECT r.symbol, r.prev_close, r.prev_high, r.prev_low, r.prev_oi, r.avg_vol_1m,
                              COALESCE(i.sector, 'OTHERS')
                       FROM daily_ref r LEFT JOIN instruments i USING (symbol) WHERE r.day = %s""", (day,))
        return {s: {"prev_close": pc, "prev_high": ph, "prev_low": pl, "prev_oi": po or 0, "avg_vol_1m": av or 0,
                    "sector": sec} for s, pc, ph, pl, po, av, sec in cur.fetchall()}


def snapshot_from_frames(frames: dict[str, pd.DataFrame], refs: dict[str, dict], at: pd.Timestamp) -> dict | None:
    at = pd.Timestamp(at).tz_convert("Asia/Kolkata")
    views = {s: view_at(df, at) for s, df in frames.items()}
    views = {s: v for s, v in views.items() if not v.empty}
    if not views:
        return None
    snap = rank(views, refs, at.to_pydatetime(), mode="replay")
    if snap:
        # equities stop printing at 15:14 in the 2026 calendar; tell the UI where the data ends
        snap["last_bar"] = max(v.ts.iloc[-1] for v in views.values()).strftime("%H:%M")
    return snap


def snapshot_at(con, day: dt.date, at: dt.datetime) -> dict:
    """Raises ReplayError(404) with the specific reason when nothing can be ranked."""
    frames = load_day(con, day, upto=at)
    if not frames:
        raise ReplayError(404, f"no candles for {day}")
    refs = load_refs(con, day)
    if not refs:
        raise ReplayError(404, f"no daily references for {day} yet (the backfill writes them when it finishes)")
    snap = snapshot_from_frames(frames, refs, pd.Timestamp(at))
    if snap is None:
        raise ReplayError(404, f"no rankable symbols for {day} at {at:%H:%M}")
    return snap


FLUSH_GRACE = dt.timedelta(seconds=90)   # the live ingest flushes a closed minute within ~60 s


def assert_not_future(at: dt.datetime, now: dt.datetime) -> None:
    if at > now.astimezone(IST):
        raise ReplayError(400, f"{at:%H:%M} has not happened yet")


def cache_ttl(day: dt.date, at: dt.datetime, now: dt.datetime) -> int | None:
    """How long a replay frame may be cached. None = do not cache: on a live day the
    `at` bar may still be forming, so the frame would be missing its stub (or, worse,
    be a view of the past dressed as a later minute)."""
    if day_is_settled(day, now):
        return 7 * 86400
    if at + FLUSH_GRACE > now.astimezone(IST):
        return None
    return 300
