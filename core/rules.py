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
  {"off": N, "field": "close"}          N candles back (0 = forming), field open/high/low/close/volume/vwap/oi
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


def _resolve(side: dict, bars: pd.DataFrame, ctx: dict):
    if "number" in side:
        return float(side["number"])
    if "series" in side:
        return float(ctx[side["series"]])
    i = -1 - int(side.get("off", 0))
    if abs(i) > len(bars):
        raise IndexError
    return float(bars.iloc[i][side["field"]])


def evaluate(scan: dict, bars: pd.DataFrame, ctx: dict) -> bool:
    try:
        res = [OPS[r["op"]](_resolve(r["lhs"], bars, ctx), _resolve(r["rhs"], bars, ctx)) for r in scan["rules"]]
    except (IndexError, KeyError, TypeError):
        return False
    return all(res) if scan.get("match", "all") == "all" else any(res)
