"""Watch board: one call returns everything a card needs for a handful of symbols at a
minute — the analytics row (same keys as the Scanner's), rank when the symbol is in the
ranking lists, the last hour of closes for the sparkline, previous-day levels, lot and sector."""
import datetime as dt
import pandas as pd
from core.analytics import analyse
from core.rules import resample
from replay import load_refs, session_bounds, COLS as CANDLE_COLS

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SPARK_MIN = 60
CANDLES_5M = 24          # two hours of 5-minute candles on the card
MAX_SYMBOLS = 30


def load_symbol(con, day, symbol: str, upto: dt.datetime) -> pd.DataFrame:
    start, end = session_bounds(day)
    with con.cursor() as cur:
        cur.execute("SELECT symbol, ts, open, high, low, close, volume, oi, delta FROM candles_1m WHERE symbol=%s AND ts>=%s AND ts<%s AND ts<=%s ORDER BY ts",
                    (symbol, start, end, upto))
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=CANDLE_COLS)
    if not df.empty:
        df["ts"] = pd.to_datetime(df.ts, utc=True).dt.tz_convert("Asia/Kolkata")
    return df.drop(columns="symbol")


def rank_fields(snap: dict | None, symbol: str) -> dict:
    for side in ("bulls", "bears"):
        for a in (snap or {}).get(side, []):
            if a["symbol"] == symbol:
                return {k: a.get(k) for k in ("rank", "pctile", "conv", "score", "side_rank", "picked")}
    return {}


def watch_row(df: pd.DataFrame, ref: dict | None, symbol: str, snap: dict | None, lot: int | None) -> dict | None:
    if df is None or df.empty or not ref or not ref.get("prev_close"):
        return None
    a = analyse(symbol, df, ref)
    if not a:
        return None
    tail = df.tail(SPARK_MIN)
    a.update(rank_fields(snap, symbol))
    a.update({"prev_close": ref.get("prev_close"), "prev_high": ref.get("prev_high"), "prev_low": ref.get("prev_low"), "avg_vol_1m": ref.get("avg_vol_1m"),
              "lot_size": lot, "last_ts": df.ts.iloc[-1].isoformat(), "n_min": int(len(df)),
              "spark": [round(float(c), 2) for c in tail.close], "spark_from": tail.ts.iloc[0].strftime("%H:%M"),
              "bars5": candles5m(df),
              "delta_available": bool((df.delta != 0).any())})
    return a


def candles5m(df: pd.DataFrame, n: int = CANDLES_5M) -> list[dict]:
    """The last n 5-minute candles (the forming one included), compact for the card."""
    src = df if "delta" in df else df.assign(delta=0)
    b = resample(src, "5m").tail(n)
    return [{"t": r.ts.strftime("%H:%M"), "o": round(float(r.open), 2), "h": round(float(r.high), 2), "l": round(float(r.low), 2),
             "c": round(float(r.close), 2), "v": int(r.volume)} for r in b.itertuples()]


def watch_rows(con, day, at: dt.datetime, symbols: list[str], snap: dict | None) -> dict:
    refs = load_refs(con, day)
    with con.cursor() as cur:
        cur.execute("SELECT symbol, lot_size FROM instruments WHERE symbol = ANY(%s)", (symbols,))
        lots = {s: l for s, l in cur.fetchall()}
    out = {}
    for sym in symbols[:MAX_SYMBOLS]:
        row = watch_row(load_symbol(con, day, sym, at), refs.get(sym), sym, snap, lots.get(sym))
        if row:
            out[sym] = row
    return out
