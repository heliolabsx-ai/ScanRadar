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
  {"off": N, "field": "close"}          N candles back (0 = forming), field open/high/low/close/volume/vwap/oi
  {"off": N, "field": "close", "sma": 13}   simple moving average of the field over 13 bars ending at that bar
  {"off": N, "field": "close", "ema": 20}   exponential moving average (standard 2/(n+1) smoothing) ending at that bar
Any side may carry "mul": x to scale the value (Chartink's `Sma(Volume, 20) * 3`).
Optional "trade": {"stop": SIDE | {"pct": x}, "target": SIDE | {"pct": x} | {"rr": r}} — the scan's own levels (see trade_levels).
"""
import operator as op
import pandas as pd

OPS = {">": op.gt, "<": op.lt, ">=": op.ge, "<=": op.le, "==": op.eq, "!=": op.ne}


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


DAY_SERIES = {"day_low": ("low", "min"), "day_high": ("high", "max")}


def continuous(scan: dict) -> bool:
    return bool(scan.get("continuous"))


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
        series = bars[side["field"]].iloc[: len(bars) + i + 1]
        if len(series) < n:
            raise IndexError
        if "sma" in side:
            return float(series.iloc[-n:].mean())
        return float(series.ewm(span=n, adjust=False).mean().iloc[-1])
    return float(bars.iloc[i][side["field"]])


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
    if target is None or (target <= entry if side == "long" else target >= entry):
        target = entry + 1.5 * risk if side == "long" else entry - 1.5 * risk
        basis = "scan+rr"
    return {"stop": stop, "target": target, "basis": basis}
