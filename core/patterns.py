"""Double / triple bottoms and tops on 5-minute bars.

Every fact a pattern carries is tagged with the close minute of the 5m bar that
made it knowable (`close_at = bar.ts + 5 min`), and `as_of(at)` only shows facts
with minute <= at. That is what makes replay, today's view and the live loop
agree, and makes repainting impossible by construction.
"""
import hashlib, json, statistics
import numpy as np, pandas as pd
from .rules import resample

VERSION = "patterns-v4"   # v2: later second point supersedes; v3: clock completeness + frozen facts; v4: volume ticks, breakout-volume gate, retest label, pivot targets
# Tuned on 10 stored days (18 Oct 2026, docs/superpowers/notes/patterns-backtest-2026-10-18.md):
# a 0.8% bounce between the points, >= 6 bars apart, on a day already moved >= 1%.
DEFAULTS = {"tol": 0.004, "min_sep": 6, "max_sep": 36, "min_retrace": 0.008, "k": 2, "fail_bars": 3,
            "min_extension": 1.0,    # min |day % at the second point| for a pattern to count (0 = off)
            "breakout_vol": 1.5,     # the confirming close needs volume >= this x the median of the pattern's bars (0 = off)
            "retest_bars": 12,       # a shape re-forming on a failed pattern's levels is a "retest" for this many bars
            "target_mult": 1.5}      # fallback target = neckline + this x pattern height when no pivot sits beyond it
FIVE = pd.Timedelta(minutes=5)
KINDS = {("long", 2): "double_bottom", ("long", 3): "triple_bottom", ("short", 2): "double_top", ("short", 3): "triple_top"}
CHECKS = 9                # 8 scored ticks + the confirming close
BREAKOUT_VOL = 1.5        # the tick's threshold (the gate uses params["breakout_vol"])


def detector_hash(params: dict = DEFAULTS) -> str:
    return hashlib.sha256((VERSION + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:12]


def bars5m(df1m: pd.DataFrame, prev_close: float | None = None, at=None) -> pd.DataFrame:
    """Complete 5m bars only, with close_at, running avg_vol and day_pct.

    Completeness is clock-based when `at` is given (a bar is complete once its close
    minute `ts + 5 min` <= at, whether or not its last minute traded), which is the
    live view; without `at` (a stored, finished day) a bar is complete once a minute
    at or after its fifth minute exists in the frame."""
    cols = ["ts", "open", "high", "low", "close", "volume", "oi", "delta", "vwap", "close_at", "avg_vol", "day_pct"]
    if df1m is None or df1m.empty:
        return pd.DataFrame(columns=cols)
    src = df1m if "delta" in df1m else df1m.assign(delta=0)
    b = resample(src, "5m")
    if at is not None:
        b = b[b.ts + FIVE <= pd.Timestamp(at)].reset_index(drop=True)
    else:
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


def pivots(ref: dict | None) -> dict:
    """Classic floor pivots from the previous day's H/L/C; empty when any is missing."""
    if not ref:
        return {}
    try:
        h, l, c = float(ref["prev_high"]), float(ref["prev_low"]), float(ref["prev_close"])
    except (KeyError, TypeError, ValueError):
        return {}
    if not (h and l and c):
        return {}
    p = (h + l + c) / 3
    return {"P": p, "R1": 2 * p - l, "S1": 2 * p - h, "R2": p + (h - l), "S2": p - (h - l)}


def target_for(side: str, neck: float, height: float, ref: dict | None, params: dict) -> tuple[float, str]:
    """Nearest pivot beyond the neckline that is at least one pattern height away
    (so the measured move is still the floor), else `target_mult` x height."""
    pv = pivots(ref)
    if side == "long":
        cands = sorted((x, n) for n, x in pv.items() if x > neck and x - neck >= height)
    else:
        cands = sorted(((x, n) for n, x in pv.items() if x < neck and neck - x >= height), reverse=True)
    if cands:
        return float(cands[0][0]), cands[0][1]
    mult = params.get("target_mult", 1.5)
    return (neck + mult * height if side == "long" else neck - mult * height), f"{mult:g}x"


def _new_pattern(side, pts, b, i, params, ref=None, retest=None):
    lo, hi, vol = b.low.to_numpy(float), b.high.to_numpy(float), b.volume.to_numpy(float)
    first, last = pts[0], pts[-1]
    k = params["k"]
    if side == "long":
        neck = float(hi[first + 1:last].max()); stop = float(min(lo[j] for j in pts))
        prices = [float(lo[j]) for j in pts]
    else:
        neck = float(lo[first + 1:last].min()); stop = float(max(hi[j] for j in pts))
        prices = [float(hi[j]) for j in pts]
    height = abs(neck - stop)
    target, basis = target_for(side, neck, height, ref, params)
    oi = b.oi.to_numpy(float)
    win = lambda j: float(vol[max(0, j - k): j + k + 1].sum())   # noqa: E731  the swing's own bars (k either side)
    facts = {"day_pct_p2": float(b.day_pct.iloc[last]) if not np.isnan(b.day_pct.iloc[last]) else None,
             "oi_p1": float(oi[first]), "oi_p2": float(oi[last]),
             "vol_ratio_p2": float(b.volume.iloc[last] / b.avg_vol.iloc[last]) if b.avg_vol.iloc[last] else 0.0,
             "vol_p1": win(first), "vol_p2": win(last),
             "vol_ratio_trigger": None, "vol_ratio_pat_trigger": None, "delta_trigger": None, "weak_closes": 0,
             "vwap_ok_formed": bool(b.close.iloc[i] > b.vwap.iloc[i]) if side == "long" else bool(b.close.iloc[i] < b.vwap.iloc[i]),
             "vwap_ok_trigger": None, "sector_formed": None, "sector_trigger": None,
             "target_basis": basis, "height": height}
    if retest is not None:
        facts["retest_of"] = retest["formed_at"].isoformat() if hasattr(retest["formed_at"], "isoformat") else str(retest["formed_at"])
        # clock-based (bars after the failure), so the live view and the stored day agree
        facts["retest_until"] = (b.close_at.iloc[retest["failed_idx"]] + params.get("retest_bars", 12) * FIVE).isoformat()
    return {"kind": KINDS[(side, len(pts))], "side": side,
            "points": [{"idx": int(j), "ts": b.ts.iloc[j], "price": p, "oi": float(oi[j])} for j, p in zip(pts, prices)],
            "neckline": neck, "stop": stop, "target": target, "formed_at": b.close_at.iloc[i], "formed_idx": int(i),
            "triggered_at": None, "trigger_idx": None, "entry": None, "failed_at": None, "failed_idx": None,
            "abandoned_at": None, "superseded_at": None, "target_touched": False, "facts": facts}


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


def _retest_source(pats, side, stop, neck, i, params):
    """A pattern that failed within the last `retest_bars` bars on the same stop and neckline."""
    tol, bars = params["tol"], params.get("retest_bars", 12)
    for p in reversed(pats):
        if p["side"] != side or p["failed_idx"] is None or i - p["failed_idx"] > bars:
            continue
        if abs(p["stop"] - stop) / stop <= tol and abs(p["neckline"] - neck) / neck <= tol:
            return p
    return None


def detect_day(df1m: pd.DataFrame, prev_close: float, params: dict = DEFAULTS, at=None, ref: dict | None = None) -> list[dict]:
    b = bars5m(df1m, prev_close, at=at)
    n = len(b)
    if n < 2 * params["k"] + 1:
        return []
    close, hi, lo, vol, avg, vwap, delta = (b[c].to_numpy(float) for c in ("close", "high", "low", "volume", "avg_vol", "vwap", "delta"))
    gate = params.get("breakout_vol", 0.0) or 0.0
    sw = swings(b, params["k"])
    by_conf: dict[int, list] = {}
    for s in sw:
        by_conf.setdefault(s[2], []).append(s)
    confirmed: list = []
    pats: list[dict] = []

    def spawn(side, pts, i):
        cand = _new_pattern(side, pts, b, i, params, ref)
        src = _retest_source(pats, side, cand["stop"], cand["neckline"], i, params)
        return _new_pattern(side, pts, b, i, params, ref, retest=src) if src is not None else cand

    for i in range(n):
        t = b.close_at.iloc[i]
        # 1. lifecycle of existing patterns on this bar's close
        for p in pats:
            if p["abandoned_at"] is not None or p["superseded_at"] is not None or i <= p["formed_idx"]:
                continue
            long = p["side"] == "long"
            if p["triggered_at"] is None:
                reached = bool(hi[i] >= p["target"]) if long else bool(lo[i] <= p["target"])
                through = (long and close[i] > p["neckline"]) or (not long and close[i] < p["neckline"])
                if (long and close[i] < p["stop"] * (1 - params["tol"])) or (not long and close[i] > p["stop"] * (1 + params["tol"])):
                    p["abandoned_at"] = t
                elif through:
                    first, last = p["points"][0]["idx"], p["points"][-1]["idx"]
                    med = float(np.median(vol[first:last + 1])) if last >= first else 0.0
                    ratio = float(vol[i] / med) if med else 0.0
                    if gate and ratio < gate:
                        p["facts"]["weak_closes"] += 1              # closed through without volume: still forming
                        if reached:
                            p["abandoned_at"] = t                   # the move happened without a confirming bar
                        continue
                    p["triggered_at"], p["trigger_idx"], p["entry"] = t, i, float(close[i])
                    p["facts"]["vol_ratio_trigger"] = float(vol[i] / avg[i]) if avg[i] else 0.0
                    p["facts"]["vol_ratio_pat_trigger"] = ratio
                    p["facts"]["delta_trigger"] = float(delta[i])
                    p["facts"]["vwap_ok_trigger"] = bool(close[i] > vwap[i]) if long else bool(close[i] < vwap[i])
                    p["target_touched"] = reached
                elif reached:
                    p["abandoned_at"] = t
            elif p["failed_at"] is None and i <= p["trigger_idx"] + params["fail_bars"]:
                if not p["target_touched"]:
                    p["target_touched"] = bool(hi[i] >= p["target"]) if long else bool(lo[i] <= p["target"])
                if not p["target_touched"] and ((long and close[i] < p["neckline"]) or (not long and close[i] > p["neckline"])):
                    p["failed_at"], p["failed_idx"] = t, i
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
                pats.append(spawn(side, [q["idx"] for q in ext["points"]] + [j], i))
                continue
            # (b) an open pattern already starts at this partner: the later second point is the current shape
            same_first = next((p for p in open_same if p["points"][0]["idx"] == jp), None)
            if same_first is not None:
                same_first["superseded_at"] = t
                pts = [q["idx"] for q in same_first["points"]] + [j] if (len(same_first["points"]) == 2 and within(same_first)) else [jp, j]
                pats.append(spawn(side, pts, i))
            else:
                pats.append(spawn(side, [jp, j], i))
    return pats


def checks_for(f: dict, side: str, state: str) -> list[bool]:
    """The nine checks from a pattern's facts alone, so live, replay and stored rows agree:
    extended day, OI lower at the second point, volume vs the day's average, sector agrees,
    VWAP side, exhaustion (second point's bars traded less than the first's), breakout volume
    (confirming close >= 1.5x the median of the pattern's bars), breakout delta (tick-rule
    delta with the direction), and the confirming close itself. Forming rows use the facts
    frozen at formation; triggered/failed rows those at the trigger."""
    long = side == "long"
    trig = state not in ("forming", "retest")
    dp = f.get("day_pct_p2")
    extended = dp is not None and (dp <= -1.0 if long else dp >= 1.0)
    oi_ok = bool(f.get("oi_p1") and f.get("oi_p2") and f["oi_p2"] < f["oi_p1"])
    vol = f.get("vol_ratio_trigger") if trig else f.get("vol_ratio_p2")
    vol_ok = vol is not None and vol >= 1.5
    sec = f.get("sector_trigger") if trig else f.get("sector_formed")
    sec_ok = sec is not None and (sec >= 0 if long else sec <= 0)
    vwap_ok = bool(f.get("vwap_ok_trigger")) if trig else bool(f.get("vwap_ok_formed"))
    exhaustion = f.get("vol_p1") is not None and f.get("vol_p2") is not None and f["vol_p2"] < f["vol_p1"]
    bv = f.get("vol_ratio_pat_trigger")
    breakout_vol = trig and bv is not None and bv >= BREAKOUT_VOL
    d = f.get("delta_trigger")
    delta_ok = trig and d is not None and (d > 0 if long else d < 0)
    return [bool(extended), oi_ok, bool(vol_ok), bool(sec_ok), vwap_ok, bool(exhaustion), bool(breakout_vol), bool(delta_ok), trig]


def state_at(p: dict, at) -> str | None:
    """forming | retest | triggered | failed, or None when the pattern is not visible at `at`."""
    if p["formed_at"] > at or (p.get("abandoned_at") is not None and p["abandoned_at"] <= at) \
       or (p.get("superseded_at") is not None and p["superseded_at"] <= at):
        return None
    if p.get("failed_at") is not None and p["failed_at"] <= at:
        return "failed"
    if p.get("triggered_at") is not None and p["triggered_at"] <= at:
        return "triggered"
    until = (p.get("facts") or {}).get("retest_until")
    if until and pd.Timestamp(at) < pd.Timestamp(until):
        return "retest"
    return "forming"


def as_of(patterns: list[dict], at, ctx: dict) -> list[dict]:
    """Rows visible at `at` with state, checks (9), score (first 8), age and R:R.
    ctx: {"sector_pct": float|None} or {"sector_at": callable(minute) -> float|None}."""
    at = pd.Timestamp(at)
    sector_at = ctx.get("sector_at")
    const = ctx.get("sector_pct")
    rows = []
    for p in patterns:
        state = state_at(p, at)
        if state is None:
            continue
        trig = state in ("triggered", "failed")
        f = dict(p["facts"])
        if f.get("sector_formed") is None:
            f["sector_formed"] = sector_at(p["formed_at"]) if sector_at else const
        if trig and f.get("sector_trigger") is None:
            f["sector_trigger"] = sector_at(p["triggered_at"]) if sector_at else const
        checks = checks_for(f, p["side"], state)
        ref = p["entry"] if trig else p["neckline"]
        risk = abs(ref - p["stop"])
        since = p["triggered_at"] if trig else p["formed_at"]
        rows.append({**{k: v for k, v in p.items() if k not in ("facts", "target_touched", "formed_idx", "trigger_idx", "failed_idx")},
                     "facts": f, "state": state, "checks": checks, "score": sum(checks[:CHECKS - 1]),
                     "rr": abs(p["target"] - ref) / risk if risk else 0.0,
                     "entry": p["entry"] if trig else None, "triggered_at": p["triggered_at"] if trig else None,
                     "failed_at": p["failed_at"] if state == "failed" else None,
                     "age_min": int((at - since).total_seconds() // 60)})
    return rows


SCORE_SPLIT = 5           # of 8 scored ticks


def summarise(rows: list[dict]) -> dict:
    """Per kind: triggers, forming_only, hit_rate, fail_rate, avg_r30/r60, median_tt_min,
    by_score (>=5 / <5 of 8) and by_volume (breakout-volume tick present / absent)."""
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

    def bv(r):
        v = (r.get("facts") or {}).get("vol_ratio_pat_trigger")
        return None if v is None else v >= BREAKOUT_VOL

    out = {}
    for kind in sorted({r["kind"] for r in rows}):
        rs = [r for r in rows if r["kind"] == kind]
        s = agg(rs)
        s["by_score"] = {f">={SCORE_SPLIT}": agg([r for r in rs if (r.get("score") or 0) >= SCORE_SPLIT]),
                         f"<{SCORE_SPLIT}": agg([r for r in rs if (r.get("score") or 0) < SCORE_SPLIT])}
        s["by_volume"] = {"with": agg([r for r in rs if bv(r) is True]), "without": agg([r for r in rs if bv(r) is False]),
                          "unknown": sum(1 for r in rs if r.get("triggered_at") and bv(r) is None)}
        out[kind] = s
    return out
