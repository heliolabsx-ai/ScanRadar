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


def passing(con, screen: dict, day: dt.date, at: dt.datetime) -> list[str]:
    hist = load_daily(con, day, lookback_days(screen) + 5)
    today = forming_day(con, day, at)
    out = []
    for sym, d in hist.items():
        t = today.get(sym)
        if t and t["open"] is not None:
            d = pd.concat([d, pd.DataFrame([t], index=[day])])
        if evaluate(screen, d):
            out.append(sym)
    return sorted(out)
