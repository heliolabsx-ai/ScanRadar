"""Daily-timeframe screens (Chartink-style): a set of conditions on daily, weekly and
monthly bars that a symbol passes or fails at a minute of a session. The current day,
week and month are *forming* bars built from the session so far, as Chartink does.

Screen JSON:
{"name": "S0101", "description": "...", "match": "all"|"any",
 "rules": [{"lhs": SIDE, "op": "<", "rhs": SIDE}, ...]}
SIDE: {"number": x}
      {"d": N, "field": F}          N days ago (0 = today, forming); F = open|high|low|close|volume|range
      {"w": N, "field": F}          N weeks ago (0 = this week, forming)
      {"m": N, "field": F}          N months ago (0 = this month, forming)
      + optional "sma": n (simple moving average of the field over n bars ending at that bar)
      + optional "mul": x (multiply the resolved value)
"""
import operator as op
import pandas as pd

OPS = {">": op.gt, "<": op.lt, ">=": op.ge, "<=": op.le, "==": op.eq, "!=": op.ne}
FIELDS = ("open", "high", "low", "close", "volume", "range")


def with_range(df: pd.DataFrame) -> pd.DataFrame:
    if "range" not in df:
        df = df.assign(range=df.high - df.low)
    return df


def weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Daily bars (index = date) → weekly bars ending Friday; the last one is the forming week."""
    return _period(daily, "W-FRI")


def monthly(daily: pd.DataFrame) -> pd.DataFrame:
    return _period(daily, "MS")


def _period(daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    if daily.empty:
        return daily
    d = daily.copy()
    d.index = pd.to_datetime(d.index)
    g = d.resample(rule, label="right" if rule.startswith("W") else "left", closed="right" if rule.startswith("W") else "left")
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum")).dropna(subset=["open"])
    return with_range(out)


def _resolve(side: dict, frames: dict):
    if "number" in side:
        return float(side["number"])
    key = "d" if "d" in side else "w" if "w" in side else "m" if "m" in side else None
    if key is None:
        raise KeyError("side needs d/w/m or number")
    df = frames[key]
    back = int(side[key])
    i = len(df) - 1 - back
    if i < 0:
        raise IndexError
    f = side["field"]
    if f not in FIELDS:
        raise KeyError(f)
    if "sma" in side:
        n = int(side["sma"])
        if i + 1 < n:
            raise IndexError
        v = float(df[f].iloc[i + 1 - n: i + 1].mean())
    else:
        v = float(df[f].iloc[i])
    if "mul" in side:
        v *= float(side["mul"])
    return v


def evaluate(screen: dict, daily: pd.DataFrame) -> bool:
    """`daily`: bars indexed by date, ascending, the last row the forming day (may be partial)."""
    if daily is None or daily.empty:
        return False
    daily = with_range(daily)
    frames = {"d": daily, "w": weekly(daily), "m": monthly(daily)}
    try:
        res = [OPS[r["op"]](_resolve(r["lhs"], frames), _resolve(r["rhs"], frames)) for r in screen["rules"]]
    except (IndexError, KeyError, TypeError, ValueError):
        return False
    return all(res) if screen.get("match", "all") == "all" else any(res)


def lookback_days(screen: dict) -> int:
    """How many daily bars a screen needs (so the loader fetches enough history)."""
    need = 10
    for r in screen["rules"]:
        for s in (r["lhs"], r["rhs"]):
            if "d" in s:
                need = max(need, int(s["d"]) + int(s.get("sma", 1)) + 1)
            if "w" in s:
                need = max(need, (int(s["w"]) + int(s.get("sma", 1)) + 1) * 5 + 5)
            if "m" in s:
                need = max(need, (int(s["m"]) + int(s.get("sma", 1)) + 1) * 23 + 5)
    return need
