"""
Chartink-style rule engine.

Scan JSON:
{
  "name": "...", "timeframe": "5m", "match": "all"|"any",
  "rules": [ {"lhs": SIDE, "op": ">", "rhs": SIDE}, ... ],
  "alert": {"telegram": true, "cooldown_min": 30}
}
SIDE is one of:
  {"number": 1000}
  {"series": "prev_day_close"}          (prev_day_close/high/low, vwap)
  {"series": "day_low"}                 today's low (day_high) over the bars seen so far, forming bar included
  {"off": N, "field": "close"}          N candles back (0 = forming), field open/high/low/close/volume/vwap/oi,
                                        or a derived candle field: range (high - low), body (|close - open|),
                                        upper_wick (high - max(open, close)), lower_wick (min(open, close) - low)
  {"off": N, "field": "close", "sma": 13}   simple moving average of the field over 13 bars ending at that bar
  {"off": N, "field": "close", "ema": 20}   exponential moving average (standard 2/(n+1) smoothing) ending at that bar
Any side may carry "mul": x to scale the value (Chartink's `Sma(Volume, 20) * 3`).
Optional "trade": {"stop": SIDE | {"pct": x}, "target": SIDE | {"pct": x} | {"rr": r}} — the scan's own levels (see trade_levels).
Optional "hours": {"from": "HH:MM", "to": "HH:MM"} — the scan only fires when the fire minute (bar close, IST) is
  from <= t < to, both zero-padded "HH:MM"; no block = always in hours; a malformed block raises ValueError
  (see hours_window / in_hours).
Optional "min_rr": x — the scan's own R:R floor (0 = no floor) instead of the system default (see min_rr).
Optional "story": {"line": "prev_day_high" | "prev_day_low", "template": "..."} — the facts behind the alert's
  one-line story (first close through the line today, the run since; see story).
"""
import datetime as dt
import html
import operator as op
import re
import pandas as pd

OPS = {">": op.gt, "<": op.lt, ">=": op.ge, "<=": op.le, "==": op.eq, "!=": op.ne}

DERIVED = {
    "range": lambda b: b["high"] - b["low"],
    "body": lambda b: (b["close"] - b["open"]).abs(),
    "upper_wick": lambda b: b["high"] - b[["open", "close"]].max(axis=1),
    "lower_wick": lambda b: b[["open", "close"]].min(axis=1) - b["low"],
}


def _field(bars: pd.DataFrame, field: str) -> pd.Series:
    """A candle column, or one of the derived candle fields (KeyError for an unknown name, as for a column)."""
    if field in DERIVED:
        return DERIVED[field](bars)
    return bars[field]


def resample(df1m: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "1m":
        out = df1m.copy()
    else:
        rule = {"3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "60m": "60min"}[tf]
        out = df1m.resample(rule, on="ts", origin="start_day", offset="9h15min", label="left", closed="left").agg(
            open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
            volume=("volume", "sum"), oi=("oi", "last"), delta=("delta", "sum")).dropna(subset=["open"]).reset_index()
    tp = (out.high + out.low + out.close) / 3
    cv = out.volume.cumsum()
    out["vwap"] = (tp * out.volume).cumsum() / cv.where(cv > 0, 1)
    return out


def resampler(frames: dict):
    """`get(symbol, tf)` = resample(frames[symbol], tf), computed once per (symbol, tf) for the life of this
    resampler (the engine makes one per run, so every scan on a timeframe shares the symbol's bars).
    The frames it hands out are shared: callers must not mutate them (join_sessions only sets session_start=0)."""
    cache: dict = {}

    def get(symbol: str, tf: str) -> pd.DataFrame:
        key = (symbol, tf)
        if key not in cache:
            cache[key] = resample(frames[symbol], tf)
        return cache[key]
    return get


DAY_SERIES = {"day_low": ("low", "min"), "day_high": ("high", "max")}


def continuous(scan: dict) -> bool:
    return bool(scan.get("continuous"))


def _hhmm(scan: dict, key: str) -> dt.time:
    v = scan["hours"].get(key) if isinstance(scan["hours"], dict) else None
    if not isinstance(v, str) or not re.fullmatch(r"\d\d:\d\d", v):
        raise ValueError(f"scan {scan.get('name', '?')}: hours.{key} must be zero-padded \"HH:MM\"")
    try:
        return dt.datetime.strptime(v, "%H:%M").time()
    except ValueError:
        raise ValueError(f"scan {scan.get('name', '?')}: hours.{key} must be zero-padded \"HH:MM\"") from None


def hours_window(scan: dict) -> tuple[dt.time, dt.time] | None:
    """The scan's parsed (from, to) window, None when it has no `hours` block. Parse once per call, pass to in_hours."""
    if "hours" not in scan:
        return None
    start, end = _hhmm(scan, "from"), _hhmm(scan, "to")
    if end <= start:
        raise ValueError(f"scan {scan.get('name', '?')}: hours.to must be after hours.from")
    return start, end


def in_hours(scan: dict, ts, window=None) -> bool:
    """True when the scan has no `hours` block or from <= ts.time() < to (ts: IST datetime / pd.Timestamp).
    `window` is a pre-parsed hours_window(scan); omit it to parse here."""
    if window is None:
        window = hours_window(scan)
        if window is None:
            return True
    return window[0] <= ts.time() < window[1]


def min_rr(scan: dict, default: float) -> float:
    """The scan's own R:R floor when it sets one (0 allowed), else `default`."""
    return float(scan["min_rr"]) if "min_rr" in scan else float(default)


def validate_scan(scan: dict) -> None:
    """Raise ValueError when a scan cannot run: a malformed `hours` block, or a `min_rr` that is not a
    non-negative number. The engine (load_scans) and the API (scan_files) both drop such a scan at load."""
    if not isinstance(scan, dict):
        raise ValueError("a scan file must hold one JSON object")
    hours_window(scan)
    if "min_rr" in scan:
        x = scan["min_rr"]
        if not (isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0):
            raise ValueError(f"scan {scan.get('name', '?')}: min_rr must be a non-negative number, got {x!r}")


def join_sessions(prev: pd.DataFrame | None, today: pd.DataFrame) -> pd.DataFrame:
    """Previous session's bars in front of today's; `attrs["session_start"]` marks where today begins
    so the day series ignore yesterday. VWAP stays per session (each side was resampled on its own)."""
    if prev is None or len(prev) == 0:
        out = today
        out.attrs["session_start"] = 0
        return out
    out = pd.concat([prev[today.columns.intersection(prev.columns)], today], ignore_index=True)
    out.attrs["session_start"] = len(prev)
    return out


def _resolve(side: dict, bars: pd.DataFrame, ctx: dict):
    return _value(side, bars, ctx) * float(side.get("mul", 1))


def _value(side: dict, bars: pd.DataFrame, ctx: dict):
    if "number" in side:
        return float(side["number"])
    if "series" in side:
        if side["series"] in DAY_SERIES:
            today = bars.iloc[int(bars.attrs.get("session_start", 0)):]
            if len(today) == 0:
                raise IndexError
            col, how = DAY_SERIES[side["series"]]
            return float(getattr(today[col], how)())
        return float(ctx[side["series"]])
    i = -1 - int(side.get("off", 0))
    if abs(i) > len(bars):
        raise IndexError
    if "sma" in side or "ema" in side:
        n = int(side.get("sma") or side.get("ema"))
        series = _field(bars, side["field"]).iloc[: len(bars) + i + 1]
        if len(series) < n:
            raise IndexError
        if "sma" in side:
            return float(series.iloc[-n:].mean())
        return float(series.ewm(span=n, adjust=False).mean().iloc[-1])
    return float(_field(bars, side["field"]).iloc[i])


def evaluate(scan: dict, bars: pd.DataFrame, ctx: dict) -> bool:
    try:
        res = [OPS[r["op"]](_resolve(r["lhs"], bars, ctx), _resolve(r["rhs"], bars, ctx)) for r in scan["rules"]]
    except (IndexError, KeyError, TypeError):
        return False
    return all(res) if scan.get("match", "all") == "all" else any(res)


# ---------- scan-owned trade levels ----------
MIN_RISK = 0.0005        # a stop closer than 0.05 % to the entry is not a stop


def _level(side_spec: dict, bars: pd.DataFrame, ctx: dict, entry: float, side: str):
    if "pct" in side_spec:
        x = float(side_spec["pct"]) / 100
        return entry * (1 - x) if side == "long" else entry * (1 + x)
    return _resolve(side_spec, bars, ctx)


def trade_levels(scan: dict, bars: pd.DataFrame, ctx: dict, entry: float) -> dict | None:
    """Stop and target a scan defines for itself, resolved at the fire minute:

        "trade": {"stop": SIDE | {"pct": 0.5}, "target": SIDE | {"pct": 1.2} | {"rr": 2.0}}

    SIDE is any rule side (`off`/`field`, `series`, `number`, with `sma`/`ema`); `pct` is a
    distance from the entry; `rr` places the target at that multiple of the risk. Returns
    {"stop", "target", "basis"} with basis "scan" when both resolved on the right side of the
    entry, "scan+rr" when the target had to fall back to 1.5x the risk, or None when the stop
    is missing or on the wrong side (the caller then uses the analytics' pivot levels)."""
    t = scan.get("trade")
    side = scan.get("side")
    if not t or side not in ("long", "short") or "stop" not in t:
        return None
    try:
        stop = float(_level(t["stop"], bars, ctx, entry, side))
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    if not stop > 0:                  # a missing ref value (0) is no level
        return None
    risk = (entry - stop) if side == "long" else (stop - entry)
    if not (risk > entry * MIN_RISK):
        return None
    basis = "scan"
    target = None
    tspec = t.get("target") or {"rr": 1.5}
    try:
        if "rr" in tspec:
            target = entry + float(tspec["rr"]) * risk if side == "long" else entry - float(tspec["rr"]) * risk
        else:
            target = float(_level(tspec, bars, ctx, entry, "short" if side == "long" else "long"))   # a pct target lies on the far side
    except (IndexError, KeyError, TypeError, ValueError):
        target = None
    if target is None or not target > 0 or (target <= entry if side == "long" else target >= entry):
        target = entry + 1.5 * risk if side == "long" else entry - 1.5 * risk
        basis = "scan+rr"
    return {"stop": stop, "target": target, "basis": basis}


# ---------- the story behind a fire ----------
def story(scan: dict, bars: pd.DataFrame, ctx: dict) -> dict | None:
    """Facts for a scan's story line, read from the bars the scan saw at the fire minute:

        "story": {"line": "prev_day_high" | "prev_day_low", "template": "..."}

    The last row of `bars` is the forming bar and is left out. `break_at` is the close minute
    (bar ts + timeframe) of the first bar today (from attrs["session_start"]) closing through
    the line in the scan's direction (short: close above, long: close below); `run_px` the
    extreme since that bar (max high short, min low long) up to the bar before the last row;
    `run_pct` its signed distance from the line in percent, 1 dp. None when the scan has no
    story block, no side, the ctx lacks the line, the frame has no `ts`, or no bar broke."""
    st = scan.get("story")
    side = scan.get("side")
    tf = scan.get("timeframe", "5m")
    if not st or side not in ("long", "short") or "ts" not in bars.columns or not tf.endswith("m"):
        return None
    try:
        line = float(ctx[st["line"]])
        mins = int(tf[:-1])
    except (KeyError, TypeError, ValueError):
        return None
    if not line > 0:
        return None
    today = bars.iloc[int(bars.attrs.get("session_start", 0)): len(bars) - 1]
    through = today.close > line if side == "short" else today.close < line
    if not through.any():
        return None
    run = today.iloc[int(through.to_numpy().argmax()):]
    ts = pd.Timestamp(run.ts.iloc[0])
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Asia/Kolkata")
    run_px = float(run.high.max() if side == "short" else run.low.min())
    return {"line": line, "break_at": (ts + pd.Timedelta(minutes=mins)).strftime("%H:%M"),
            "run_px": run_px, "run_pct": round((run_px - line) / line * 100, 1)}


# ---------- the Telegram text of a rule alert (pure) ----------
def alert_text(scan: dict, symbol: str, price: float, lv: dict | None, st: dict | None, now) -> str:
    """The engine's Telegram message (HTML) for one fire. `lv` is trade_levels() or None, `st` story() or None,
    `now` the fire minute. Scan name and symbol are HTML-escaped (Telegram rejects a bare &: M&M, GVT&D).
    With a story: the side word (SELL/BUY) only for a scan with a trade block (a break note carries `side` for
    the chip colour only), and a scan with a trade block gets the entry line; a template that does not format
    raises (KeyError/IndexError/TypeError/ValueError) so the caller can log it and call again with st=None."""
    name, sym = html.escape(scan["name"]), html.escape(symbol)
    foot = f"\n{now:%H:%M}  ·  scanradar.tech"
    rr = (abs(lv["target"] - price) / abs(price - lv["stop"]) if price != lv["stop"] else 0) if lv else 0
    if st:
        line = scan["story"]["template"].format(symbol=sym, at=now.strftime("%H:%M"), **st)
        head = name + ((" · SELL" if scan.get("side") == "short" else " · BUY") if scan.get("trade") else "")
        lvl = ""
        if lv and scan.get("trade"):
            t = scan["trade"]
            stop_why = {"day_high": " (day high)", "day_low": " (day low)"}.get((t.get("stop") or {}).get("series"), "")
            tgt_why = " (prev close)" if lv.get("basis") == "scan" and (t.get("target") or {}).get("series") == "prev_day_close" else ""
            lvl = (f"\nentry ₹{price:,.2f} · stop ₹{lv['stop']:,.2f}{stop_why} · target ₹{lv['target']:,.2f}{tgt_why}"
                   f" · R:R 1:{rr:.1f}")
        return f"<b>{head}</b>\n{line}{lvl}{foot}"
    lvl = f"\nstop ₹{lv['stop']:,.2f} · target ₹{lv['target']:,.2f} · R:R 1:{rr:.1f}" if lv else ""
    return f"<b>{name}</b>\n{sym} @ ₹{price:,.2f}  ({scan.get('timeframe', '5m')}){lvl}{foot}"
