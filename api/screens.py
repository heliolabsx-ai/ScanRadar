"""Screens for the API: load every symbol's daily history plus the forming day at a minute,
evaluate a screen, cache the passing set."""
import os, glob, json, hashlib, datetime as dt
import pandas as pd
from core.screens import evaluate, lookback_days
from replay import session_bounds

SCREEN_DIR = os.environ.get("SCREEN_DIR", "/app/screens")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def screen_files() -> list[tuple[dict, str]]:
    out = []
    for p in sorted(glob.glob(os.path.join(SCREEN_DIR, "*.json"))):
        with open(p, "rb") as f:
            b = f.read()
        try:
            out.append((json.loads(b), hashlib.sha256(b).hexdigest()[:12]))
        except ValueError:
            continue
    return out


def load_daily(con, day: dt.date, days: int) -> dict[str, pd.DataFrame]:
    """Daily bars strictly before `day` for every symbol, `days` rows each, indexed by date."""
    with con.cursor() as cur:
        cur.execute("""SELECT symbol, day, open, high, low, close, volume FROM (
                         SELECT symbol, day, open, high, low, close, volume,
                                row_number() OVER (PARTITION BY symbol ORDER BY day DESC) AS rn
                         FROM candles_1d WHERE day < %s) t WHERE rn <= %s ORDER BY symbol, day""", (day, days))
        rows = cur.fetchall()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["symbol", "day", "open", "high", "low", "close", "volume"])
    return {s: g.drop(columns="symbol").set_index("day") for s, g in df.groupby("symbol")}


def forming_day(con, day: dt.date, at: dt.datetime) -> dict[str, dict]:
    """Today's open/high/low/close/volume so far (candles up to `at`) per symbol."""
    start, end = session_bounds(day)
    with con.cursor() as cur:
        cur.execute("""SELECT symbol, (array_agg(open ORDER BY ts))[1], max(high), min(low), (array_agg(close ORDER BY ts DESC))[1], sum(volume)
                       FROM candles_1m WHERE ts >= %s AND ts < %s AND ts <= %s GROUP BY symbol""", (start, end, at))
        return {s: {"open": o, "high": h, "low": l, "close": c, "volume": int(v or 0)} for s, o, h, l, c, v in cur.fetchall()}


def recent_days(con, after: dt.date, before: dt.date) -> dict[str, pd.DataFrame]:
    """Sessions after the last backfilled daily bar and before `before`, aggregated from the
    minute candles, so a screen never skips yesterday because the nightly backfill has not run."""
    with con.cursor() as cur:
        cur.execute("""SELECT symbol, (ts AT TIME ZONE 'Asia/Kolkata')::date AS d,
                              (array_agg(open ORDER BY ts))[1], max(high), min(low), (array_agg(close ORDER BY ts DESC))[1], sum(volume)
                       FROM candles_1m WHERE ts >= %s AND ts < %s GROUP BY symbol, d ORDER BY symbol, d""",
                    (dt.datetime.combine(after + dt.timedelta(days=1), dt.time(0), IST), dt.datetime.combine(before, dt.time(0), IST)))
        rows = cur.fetchall()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["symbol", "day", "open", "high", "low", "close", "volume"])
    df["volume"] = df.volume.fillna(0).astype(int)
    return {s: g.drop(columns="symbol").set_index("day") for s, g in df.groupby("symbol")}


def basis(screen: dict) -> str:
    return "intraday" if screen.get("basis") == "intraday" else "eod"


def passing(con, screen: dict, day: dt.date, at: dt.datetime) -> list[str]:
    """Symbols passing the screen for session `day`: on the eod basis from the candles of the
    sessions before it (the list a trader prepares the evening before), on the intraday basis
    with `day`'s forming bar through minute `at`."""
    hist = load_daily(con, day, lookback_days(screen) + 5)
    last_daily = max((d.index[-1] for d in hist.values()), default=None)
    if last_daily is not None and last_daily < day - dt.timedelta(days=1):
        for sym, g in recent_days(con, last_daily, day).items():
            if sym in hist:
                hist[sym] = pd.concat([hist[sym], g[g.index > hist[sym].index[-1]]])
    intraday = basis(screen) == "intraday"
    today = forming_day(con, day, at) if intraday else {}
    start = dt.datetime.combine(day, dt.time(9, 15), IST)
    elapsed = min(1.0, max(1 / 375, (at.astimezone(IST) - start).total_seconds() / 60 / 375)) if intraday else 1.0
    out = []
    for sym, d in hist.items():
        t = today.get(sym)
        if t and t["open"] is not None:
            d = pd.concat([d, pd.DataFrame([t], index=[day])])
        if evaluate(screen, d, elapsed):
            out.append(sym)
    return sorted(out)


def as_of_label(con, screen: dict, day: dt.date, minute: dt.datetime) -> str:
    if basis(screen) == "intraday":
        return minute.strftime("%H:%M")
    with con.cursor() as cur:
        cur.execute("SELECT max(d) FROM (SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date AS d FROM candles_1m WHERE symbol='RELIANCE' AND ts < %s UNION SELECT max(day) FROM candles_1d WHERE day < %s) x",
                    (dt.datetime.combine(day, dt.time(0), IST), day))
        last = cur.fetchone()[0]
    return f"close of {last.strftime('%a %d %b')}" if last else "previous close"


# ---------- rule scans as screens: the stocks a scan fired on, by the as-of minute ----------
def fired_symbols(rows, minute: dt.datetime) -> list[str]:
    """Distinct symbols of alert rows (ts, symbol) at or before `minute` (minute start), sorted."""
    cut = minute.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    return sorted({sym for ts, sym in rows if ts < cut})


def fired(con, scan_name: str, day: dt.date, minute: dt.datetime) -> list[str]:
    start = dt.datetime.combine(day, dt.time(0), IST)
    with con.cursor() as cur:
        cur.execute("SELECT ts, symbol FROM alerts WHERE scan=%s AND ts >= %s AND ts < %s",
                    (scan_name, start, start + dt.timedelta(days=1)))
        return fired_symbols(cur.fetchall(), minute)
