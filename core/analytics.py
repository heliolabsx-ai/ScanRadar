"""Per-symbol analytics on a 1-minute candle frame. Same formulas as the demo."""
import numpy as np, pandas as pd


def oi_label(oi_pct: float, trend: float, thresh: float = 0.5):
    if oi_pct >= thresh:
        return ("LONG BUILD-UP", "lb") if trend >= 0 else ("SHORT BUILD-UP", "sb")
    if oi_pct <= -thresh:
        return ("SHORT COVERING", "sc") if trend >= 0 else ("LONG UNWINDING", "lu")
    return ("NEUTRAL", "nt")


def analyse(sym: str, df: pd.DataFrame, ref: dict) -> dict | None:
    """df: 1-min candles for today, ascending. ref: prev_close/high/low/prev_oi/avg_vol_1m/sector."""
    if df.empty or not ref.get("prev_close"):
        return None
    c = float(df.close.iloc[-1]); pc = float(ref["prev_close"])
    ph, pl = float(ref["prev_high"]), float(ref["prev_low"])
    pct = (c / pc - 1) * 100
    tp = (df.high + df.low + df.close) / 3
    cum_v = df.volume.cumsum().replace(0, np.nan)
    vwap = float((tp * df.volume).cumsum().iloc[-1] / cum_v.iloc[-1]) if cum_v.iloc[-1] else c
    oi_now = int(df.oi.iloc[-1]) if df.oi.iloc[-1] else int(ref.get("prev_oi") or 0)
    prev_oi = int(ref.get("prev_oi") or 0)
    oi_pct = (oi_now / prev_oi - 1) * 100 if prev_oi else 0.0
    look = min(30, len(df) - 1)
    trend = c - float(df.open.iloc[-1 - look])
    label, cls = oi_label(oi_pct, trend)
    P = (ph + pl + pc) / 3
    R1, S1 = 2 * P - pl, 2 * P - ph
    R2, S2 = P + (ph - pl), P - (ph - pl)
    orng = df.iloc[:15]
    orH, orL = float(orng.high.max()), float(orng.low.min())
    avg1 = float(ref.get("avg_vol_1m") or 0)
    rvol = float(df.volume.sum() / (avg1 * len(df))) if avg1 else 1.0
    delta = int(df.delta.sum())
    # order block: origin candle of the largest 5-bar impulse
    ob = None
    if len(df) > 6:
        mv = (df.close.values[5:] - df.open.values[:-5])
        k = int(np.argmax(np.abs(mv)))
        o = df.iloc[k]
        ob = {"lo": float(min(o.open, o.close)), "hi": float(max(o.open, o.close)), "dir": int(np.sign(mv[k]))}
    d = 1 if pct >= 0 else -1
    stop = min(S1, ob["lo"]) if d > 0 and ob else (S1 if d > 0 else (max(R1, ob["hi"]) if ob else R1))
    target = R2 if d > 0 else S2
    risk, reward = abs(c - stop), abs(target - c)
    rr = reward / risk if risk > 0 else 0.0
    return {"symbol": sym, "sector": ref.get("sector", "OTHERS"), "price": c, "chg": c - pc, "pct": pct,
            "vwap": vwap, "above_vwap": c > vwap, "oi": oi_now, "oi_pct": oi_pct, "oi_label": label, "oi_cls": cls,
            "delta": delta, "rvol": rvol, "P": P, "R1": R1, "R2": R2, "S1": S1, "S2": S2, "orH": orH, "orL": orL,
            "ob": ob, "dir": d, "stop": stop, "target": target, "rr": rr, "risk_pct": risk / c * 100}


def score(a: dict, sector_pct: float, relative: bool = True) -> float:
    move = a["pct"] - sector_pct if relative else a["pct"]
    s = abs(move) * 18
    if (a["oi_cls"] == "lb" and a["dir"] > 0) or (a["oi_cls"] == "sb" and a["dir"] < 0):
        s += min(abs(a["oi_pct"]), 15) * 2
    if (a["above_vwap"] and a["dir"] > 0) or (not a["above_vwap"] and a["dir"] < 0):
        s += 12
    s += min(a["rvol"], 3) * 8
    s += min(a["rr"], 4) * 4
    return s
