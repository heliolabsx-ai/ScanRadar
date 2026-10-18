"""Double / triple bottoms and tops on 5-minute bars.

Every fact a pattern carries is tagged with the close minute of the 5m bar that
made it knowable (`close_at = bar.ts + 5 min`), and `as_of(at)` only shows facts
with minute <= at. That is what makes replay, today's view and the live loop
agree, and makes repainting impossible by construction.
"""
import hashlib, json, statistics
import numpy as np, pandas as pd
from .rules import resample

VERSION = "patterns-v2"   # v2: a later second point supersedes the earlier double on the same first point
# Tuned on 10 stored days (18 Oct 2026, docs/superpowers/notes/patterns-backtest-2026-10-18.md):
# a 0.8% bounce between the points, >= 6 bars apart, on a day already moved >= 1%.
DEFAULTS = {"tol": 0.004, "min_sep": 6, "max_sep": 36, "min_retrace": 0.008, "k": 2, "fail_bars": 3,
            "min_extension": 1.0}   # min |day % at the second point| for a pattern to count (0 = off)
FIVE = pd.Timedelta(minutes=5)
KINDS = {("long", 2): "double_bottom", ("long", 3): "triple_bottom", ("short", 2): "double_top", ("short", 3): "triple_top"}


def detector_hash(params: dict = DEFAULTS) -> str:
    return hashlib.sha256((VERSION + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:12]


def bars5m(df1m: pd.DataFrame, prev_close: float | None = None) -> pd.DataFrame:
    """Complete 5m bars only (the window's last minute <= the last 1m bar), with
    close_at, running avg_vol and day_pct."""
    cols = ["ts", "open", "high", "low", "close", "volume", "oi", "vwap", "close_at", "avg_vol", "day_pct"]
    if df1m is None or df1m.empty:
        return pd.DataFrame(columns=cols)
    b = resample(df1m, "5m")
    last = df1m.ts.iloc[-1]
    b = b[b.ts + pd.Timedelta(minutes=4) <= last].reset_index(drop=True)
    b["close_at"] = b.ts + FIVE
    b["avg_vol"] = b.volume.expanding().mean()
    b["day_pct"] = (b.close / prev_close - 1) * 100 if prev_close else np.nan
    return b[cols]


def swings(b: pd.DataFrame, k: int = 2) -> list[tuple[int, str, int]]:
    """(bar_index, 'low'|'high', confirmed_at_index), ordered by confirmation then index."""
    lo, hi, n, out = b.low.to_numpy(float), b.high.to_numpy(float), len(b), []
    for i in range(k, n - k):
        if lo[i] < lo[i - k:i].min() and lo[i] < lo[i + 1:i + k + 1].min():
            out.append((i, "low", i + k))
        if hi[i] > hi[i - k:i].max() and hi[i] > hi[i + 1:i + k + 1].max():
            out.append((i, "high", i + k))
    return sorted(out, key=lambda x: (x[2], x[0]))


def _new_pattern(side, pts, b, i, params):
    lo, hi = b.low.to_numpy(float), b.high.to_numpy(float)
    first, last = pts[0], pts[-1]
    if side == "long":
        neck = float(hi[first + 1:last].max()); stop = float(min(lo[j] for j in pts)); target = neck + (neck - stop)
        prices = [float(lo[j]) for j in pts]
    else:
        neck = float(lo[first + 1:last].min()); stop = float(max(hi[j] for j in pts)); target = neck - (stop - neck)
        prices = [float(hi[j]) for j in pts]
    oi = b.oi.to_numpy(float)
    return {"kind": KINDS[(side, len(pts))], "side": side,
            "points": [{"idx": int(j), "ts": b.ts.iloc[j], "price": p, "oi": float(oi[j])} for j, p in zip(pts, prices)],
            "neckline": neck, "stop": stop, "target": target, "formed_at": b.close_at.iloc[i], "formed_idx": int(i),
            "triggered_at": None, "trigger_idx": None, "entry": None, "failed_at": None, "abandoned_at": None,
            "superseded_at": None, "target_touched": False,
            "facts": {"day_pct_p2": float(b.day_pct.iloc[last]) if not np.isnan(b.day_pct.iloc[last]) else None,
                      "oi_p1": float(oi[first]), "oi_p2": float(oi[last]),
                      "vol_ratio_p2": float(b.volume.iloc[last] / b.avg_vol.iloc[last]) if b.avg_vol.iloc[last] else 0.0,
                      "vol_ratio_trigger": None, "vwap_ok_trigger": None}}


def _partner(side, j, confirmed, b, params):
    """Most recent earlier qualifying swing of the same kind, or None."""
    lo, hi, close = b.low.to_numpy(float), b.high.to_numpy(float), b.close.to_numpy(float)
    px = lo if side == "long" else hi
    opp = [x for x in confirmed if x[1] == ("high" if side == "long" else "low")]
    for jp, kind, _ in reversed([x for x in confirmed if x[1] == ("low" if side == "long" else "high") and x[0] < j]):
        sep = j - jp
        if sep > params["max_sep"]:
            return None
        if side == "long" and close[jp + 1:j + 1].min() < px[jp] * (1 - params["tol"]):
            continue             # price closed below this low since: it cannot anchor a bottom (older lows may)
        if side == "short" and close[jp + 1:j + 1].max() > px[jp] * (1 + params["tol"]):
            continue
        if sep < params["min_sep"] or abs(px[j] - px[jp]) / px[jp] > params["tol"]:
            continue
        if not any(jp < h < j for h, _, _ in opp):
            continue
        retr = (hi[jp + 1:j].max() / px[jp] - 1) if side == "long" else (1 - lo[jp + 1:j].min() / px[jp])
        if retr < params["min_retrace"]:
            continue
        return jp
    return None


def detect_day(df1m: pd.DataFrame, prev_close: float, params: dict = DEFAULTS) -> list[dict]:
    b = bars5m(df1m, prev_close)
    n = len(b)
    if n < 2 * params["k"] + 1:
        return []
    close, hi, lo, vol, avg, vwap = (b[c].to_numpy(float) for c in ("close", "high", "low", "volume", "avg_vol", "vwap"))
    sw = swings(b, params["k"])
    by_conf: dict[int, list] = {}
    for s in sw:
        by_conf.setdefault(s[2], []).append(s)
    confirmed: list = []
    pats: list[dict] = []
    for i in range(n):
        t = b.close_at.iloc[i]
        # 1. lifecycle of existing patterns on this bar's close
        for p in pats:
            if p["abandoned_at"] is not None or p["superseded_at"] is not None or i <= p["formed_idx"]:
                continue
            long = p["side"] == "long"
            if p["triggered_at"] is None:
                if (long and close[i] < p["stop"] * (1 - params["tol"])) or (not long and close[i] > p["stop"] * (1 + params["tol"])):
                    p["abandoned_at"] = t
                elif (long and close[i] > p["neckline"]) or (not long and close[i] < p["neckline"]):
                    p["triggered_at"], p["trigger_idx"], p["entry"] = t, i, float(close[i])
                    p["facts"]["vol_ratio_trigger"] = float(vol[i] / avg[i]) if avg[i] else 0.0
                    p["facts"]["vwap_ok_trigger"] = bool(close[i] > vwap[i]) if long else bool(close[i] < vwap[i])
                    p["target_touched"] = bool(hi[i] >= p["target"]) if long else bool(lo[i] <= p["target"])
            elif p["failed_at"] is None and i <= p["trigger_idx"] + params["fail_bars"]:
                if not p["target_touched"]:
                    p["target_touched"] = bool(hi[i] >= p["target"]) if long else bool(lo[i] <= p["target"])
                if not p["target_touched"] and ((long and close[i] < p["neckline"]) or (not long and close[i] > p["neckline"])):
                    p["failed_at"] = t
        # 2. swings confirmed by this bar
        for s in by_conf.get(i, []):
            confirmed.append(s)
            j, kind, _ = s
            side = "long" if kind == "low" else "short"
            jp = _partner(side, j, confirmed, b, params)
            if jp is None:
                continue
            ext_req = params.get("min_extension", 0.0)
            dp = b.day_pct.iloc[j]
            if ext_req and (np.isnan(dp) or (dp > -ext_req if side == "long" else dp < ext_req)):
                continue
            px = lo if side == "long" else hi
            open_same = [p for p in pats if p["side"] == side and p["triggered_at"] is None and p["abandoned_at"] is None
                         and p["superseded_at"] is None]
            within = lambda p: all(abs(px[j] - q["price"]) / q["price"] <= params["tol"] for q in p["points"])
            # (a) the new swing extends an open double whose last point is the partner → triple
            ext = next((p for p in open_same if len(p["points"]) == 2 and p["points"][-1]["idx"] == jp and within(p)), None)
            if ext is not None:
                ext["superseded_at"] = t
                pats.append(_new_pattern(side, [q["idx"] for q in ext["points"]] + [j], b, i, params))
                continue
            # (b) an open pattern already starts at this partner: the later second point is the current shape
            same_first = next((p for p in open_same if p["points"][0]["idx"] == jp), None)
            if same_first is not None:
                same_first["superseded_at"] = t
                pts = [q["idx"] for q in same_first["points"]] + [j] if (len(same_first["points"]) == 2 and within(same_first)) else [jp, j]
                pats.append(_new_pattern(side, pts, b, i, params))
            else:
                pats.append(_new_pattern(side, [jp, j], b, i, params))
    return pats


def as_of(patterns: list[dict], at, ctx: dict) -> list[dict]:
    """Rows visible at `at` with state, checks (6), score (first 5), age and R:R."""
    at = pd.Timestamp(at)
    bars = ctx.get("bars")
    sector_pct = ctx.get("sector_pct")
    last_bar = None
    if bars is not None and len(bars):
        vis = bars[bars.close_at <= at]
        last_bar = vis.iloc[-1] if len(vis) else None
    rows = []
    for p in patterns:
        if p["formed_at"] > at or (p["abandoned_at"] is not None and p["abandoned_at"] <= at) \
           or (p["superseded_at"] is not None and p["superseded_at"] <= at):
            continue
        long = p["side"] == "long"
        f = p["facts"]
        if p["failed_at"] is not None and p["failed_at"] <= at:
            state = "failed"
        elif p["triggered_at"] is not None and p["triggered_at"] <= at:
            state = "triggered"
        else:
            state = "forming"
        trig = state != "forming"
        extended = f["day_pct_p2"] is not None and (f["day_pct_p2"] <= -1.0 if long else f["day_pct_p2"] >= 1.0)
        oi_ok = bool(f["oi_p1"] and f["oi_p2"] and f["oi_p2"] < f["oi_p1"])
        vol_ok = (f["vol_ratio_trigger"] if trig else f["vol_ratio_p2"]) is not None and \
                 (f["vol_ratio_trigger"] if trig else f["vol_ratio_p2"]) >= 1.5
        sec_ok = sector_pct is not None and (sector_pct >= 0 if long else sector_pct <= 0)
        if trig:
            vwap_ok = bool(f["vwap_ok_trigger"])
        elif last_bar is not None:
            vwap_ok = bool(last_bar.close > last_bar.vwap) if long else bool(last_bar.close < last_bar.vwap)
        else:
            vwap_ok = False
        checks = [bool(extended), oi_ok, bool(vol_ok), bool(sec_ok), vwap_ok, trig]
        ref = p["entry"] if trig else p["neckline"]
        risk = abs(ref - p["stop"])
        rr = abs(p["target"] - ref) / risk if risk else 0.0
        since = p["triggered_at"] if trig else p["formed_at"]
        rows.append({**{k: v for k, v in p.items() if k not in ("facts", "target_touched", "formed_idx", "trigger_idx")},
                     "state": state, "checks": checks, "score": sum(checks[:5]), "rr": rr,
                     "age_min": int((at - since).total_seconds() // 60)})
    return rows


def summarise(rows: list[dict]) -> dict:
    """Per kind: triggers, forming_only, hit_rate, fail_rate, avg_r30/r60, median_tt_min, by_score."""
    def agg(rs):
        trig = [r for r in rs if r.get("triggered_at")]
        outs = [r["outcome"] for r in trig if r.get("outcome")]
        hits = sum(1 for o in outs if o.get("hit") == "target"); stops = sum(1 for o in outs if o.get("hit") == "stop")
        r30 = [o["r30"] for o in outs if o.get("r30") is not None]; r60 = [o["r60"] for o in outs if o.get("r60") is not None]
        tts = [o["tt_min"] for o in outs if o.get("tt_min") is not None]
        return {"triggers": len(trig), "forming_only": len(rs) - len(trig),
                "hit_rate": hits / (hits + stops) * 100 if hits + stops else None,
                "fail_rate": sum(1 for r in trig if r.get("failed_at")) / len(trig) * 100 if trig else None,
                "avg_r30": sum(r30) / len(r30) if r30 else None, "avg_r60": sum(r60) / len(r60) if r60 else None,
                "median_tt_min": statistics.median(tts) if tts else None}
    out = {}
    for kind in sorted({r["kind"] for r in rows}):
        rs = [r for r in rows if r["kind"] == kind]
        s = agg(rs)
        s["by_score"] = {">=3": agg([r for r in rs if (r.get("score") or 0) >= 3]), "<3": agg([r for r in rs if (r.get("score") or 0) < 3])}
        out[kind] = s
    return out
