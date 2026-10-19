"""Intraday chart patterns on 5-minute bars.

Reversal shapes: double / triple bottoms and tops, head & shoulders and its inverse.
Continuation shapes: bull / bear flags, rectangles (range break), ascending / descending
triangles, opening-range breaks. Every shape produces the same pattern record (points,
neckline = the level a close must take out, stop, target, facts) and shares one
lifecycle: forming -> triggered (first close through the neckline carrying volume)
-> failed (close back through within `fail_bars`), or abandoned.

Every fact a pattern carries is tagged with the close minute of the 5m bar that
made it knowable (`close_at = bar.ts + 5 min`), and `as_of(at)` only shows facts
with minute <= at. That is what makes replay, today's view and the live loop
agree, and makes repainting impossible by construction.
"""
import hashlib, json, statistics
import numpy as np, pandas as pd
from .rules import resample

VERSION = "patterns-v5"   # v4: volume ticks, breakout gate, retest, pivot targets; v5: head & shoulders, flags, ranges, triangles, opening-range breaks
# Tuned on 10 stored days (18 Oct 2026, docs/superpowers/notes/patterns-backtest-2026-10-18.md):
# a 0.8% bounce between the points, >= 6 bars apart, on a day already moved >= 1%.
DEFAULTS = {"tol": 0.004, "min_sep": 6, "max_sep": 36, "min_retrace": 0.008, "k": 2, "fail_bars": 3,
            "min_extension": 1.0,    # min |day % at the second point| for a reversal pattern to count (0 = off)
            "breakout_vol": 1.5,     # the confirming close needs volume >= this x the median of the pattern's bars (0 = off)
            "retest_bars": 12,       # a shape re-forming on a failed pattern's levels is a "retest" for this many bars
            "target_mult": 1.5,      # fallback target = neckline + this x pattern height when no pivot sits beyond it
            "hs_head": 0.004,        # head & shoulders: head beyond both shoulders by at least this
            "shoulder_tol": 0.006,   # shoulders within this of each other
            "pole_min": 0.012,       # flags: pole of at least this much within `pole_bars`
            "pole_bars": 6, "flag_min": 3, "flag_max": 10,
            "range_bars": 12,        # rectangles / triangles: structure spans at least this many bars, with three touches on one side
            "range_min": 0.006, "range_max": 0.02,
            "orb_bars": 3, "orb_until": "10:30", "orb_min": 0.0025, "orb_max": 0.02}
FIVE = pd.Timedelta(minutes=5)
KINDS = {("long", 2): "double_bottom", ("long", 3): "triple_bottom", ("short", 2): "double_top", ("short", 3): "triple_top"}
LABELS = {"double_bottom": "Double bottom", "triple_bottom": "Triple bottom", "double_top": "Double top", "triple_top": "Triple top",
          "inv_head_shoulders": "Inverse head & shoulders", "head_shoulders": "Head & shoulders",
          "bull_flag": "Bull flag", "bear_flag": "Bear flag", "range_break_long": "Range breakout", "range_break_short": "Range breakdown",
          "asc_triangle": "Ascending triangle", "desc_triangle": "Descending triangle",
          "orb_long": "Opening range break up", "orb_short": "Opening range break down"}
CONTINUATION = {"bull_flag", "bear_flag", "range_break_long", "range_break_short", "asc_triangle", "desc_triangle", "orb_long", "orb_short"}
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


class _Day:
    """Arrays and helpers shared by the shape finders for one symbol-day."""

    def __init__(self, b: pd.DataFrame, params: dict, ref: dict | None):
        self.b, self.params, self.ref = b, params, ref
        self.close, self.hi, self.lo, self.vol, self.avg, self.vwap, self.delta, self.oi = (
            b[c].to_numpy(float) for c in ("close", "high", "low", "volume", "avg_vol", "vwap", "delta", "oi"))
        self.n = len(b)

    def make(self, kind: str, side: str, pts: list[tuple[int, float]], i: int, neck: float, stop: float, height: float,
             vol_p1: float, vol_p2: float, p2_idx: int, retest=None, extra: dict | None = None) -> dict:
        """One pattern record formed at bar i. `pts` = (bar index, price) for drawing; `p2_idx`
        is the bar whose facts (day %, OI, volume ratio) describe the pattern's second point."""
        b, p = self.b, self.params
        target, basis = target_for(side, neck, height, self.ref, p)
        first = pts[0][0]
        facts = {"continuation": kind in CONTINUATION,
                 "day_pct_p2": float(b.day_pct.iloc[p2_idx]) if not np.isnan(b.day_pct.iloc[p2_idx]) else None,
                 "oi_p1": float(self.oi[first]), "oi_p2": float(self.oi[p2_idx]),
                 "vol_ratio_p2": float(self.vol[p2_idx] / self.avg[p2_idx]) if self.avg[p2_idx] else 0.0,
                 "vol_p1": float(vol_p1), "vol_p2": float(vol_p2),
                 "vol_ratio_trigger": None, "vol_ratio_pat_trigger": None, "delta_trigger": None, "weak_closes": 0,
                 "vwap_ok_formed": bool(self.close[i] > self.vwap[i]) if side == "long" else bool(self.close[i] < self.vwap[i]),
                 "vwap_ok_trigger": None, "sector_formed": None, "sector_trigger": None,
                 "target_basis": basis, "height": float(height), **(extra or {})}
        if retest is not None:
            fa = retest["formed_at"]
            facts["retest_of"] = fa.isoformat() if hasattr(fa, "isoformat") else str(fa)
            # clock-based (bars after the failure), so the live view and the stored day agree
            facts["retest_until"] = (b.close_at.iloc[retest["failed_idx"]] + p.get("retest_bars", 12) * FIVE).isoformat()
        return {"kind": kind, "side": side,
                "points": [{"idx": int(j), "ts": b.ts.iloc[j], "price": float(px), "oi": float(self.oi[j])} for j, px in pts],
                "neckline": float(neck), "stop": float(stop), "target": target, "formed_at": b.close_at.iloc[i], "formed_idx": int(i),
                "triggered_at": None, "trigger_idx": None, "entry": None, "failed_at": None, "failed_idx": None,
                "abandoned_at": None, "superseded_at": None, "target_touched": False, "expires_idx": None,
                "abandon_level": float(stop),            # a close beyond this while forming abandons the shape (boxes: the far side)
                "pat_first": int(first), "pat_last": int(p2_idx), "facts": facts}

    def window_vol(self, j: int) -> float:
        k = self.params["k"]
        return float(self.vol[max(0, j - k): j + k + 1].sum())


# ---------- shape finders ----------
def _double(D: _Day, side, pts, i, retest=None):
    lo, hi = D.lo, D.hi
    first, last = pts[0], pts[-1]
    if side == "long":
        neck = float(hi[first + 1:last].max()); stop = float(min(lo[j] for j in pts)); prices = [float(lo[j]) for j in pts]
    else:
        neck = float(lo[first + 1:last].min()); stop = float(max(hi[j] for j in pts)); prices = [float(hi[j]) for j in pts]
    return D.make(KINDS[(side, len(pts))], side, list(zip(pts, prices)), i, neck, stop, abs(neck - stop),
                  D.window_vol(first), D.window_vol(last), last, retest)


def _partner(side, j, confirmed, D: _Day):
    """Most recent earlier qualifying swing of the same kind, or None."""
    params, lo, hi, close = D.params, D.lo, D.hi, D.close
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


def _head_shoulders(D: _Day, side, j, confirmed, i):
    """Right shoulder just confirmed at j: left shoulder l, head m with l < m < j."""
    p = D.params
    same = "high" if side == "short" else "low"
    px = D.hi if side == "short" else D.lo
    beyond = (lambda a, c: a > c * (1 + p["hs_head"])) if side == "short" else (lambda a, c: a < c * (1 - p["hs_head"]))
    cands = [x[0] for x in confirmed if x[1] == same and x[0] < j]
    for m in reversed(cands):
        if j - m < 2 or j - m > p["max_sep"]:
            continue
        if not beyond(px[m], px[j]):
            continue
        for l in reversed([x for x in cands if x < m]):
            if j - l > p["max_sep"]:
                break
            if m - l < 2 or abs(px[l] - px[j]) / px[j] > p["shoulder_tol"] or not beyond(px[m], px[l]):
                continue
            if side == "short":
                t1, t2 = float(D.lo[l + 1:m].min()), float(D.lo[m + 1:j].min())
                neck, stop = t2, float(px[m])
                if D.close[m + 1:i + 1].max() > stop:       # head taken out since: not a top
                    return None
            else:
                t1, t2 = float(D.hi[l + 1:m].max()), float(D.hi[m + 1:j].max())
                neck, stop = t2, float(px[m])
                if D.close[m + 1:i + 1].min() < stop:
                    return None
            if abs(t1 - t2) / t2 > 2 * p["tol"]:            # neckline too sloped to call a level
                continue
            height = abs(stop - neck)
            if height / neck < p["range_min"]:
                continue
            kind = "head_shoulders" if side == "short" else "inv_head_shoulders"
            return D.make(kind, side, [(l, float(px[l])), (m, float(px[m])), (j, float(px[j]))], i, neck, stop, height,
                          D.window_vol(l), D.window_vol(j), j, extra={"head_idx": int(m)})
        return None
    return None


def _ranges(D: _Day, j, confirmed, i) -> list[dict]:
    """Rectangles and ascending / descending triangles from the last three swings of each kind."""
    p, tol = D.params, D.params["tol"]
    lo_sw = [x[0] for x in confirmed if x[1] == "low" and j - x[0] <= p["max_sep"]][-3:]
    hi_sw = [x[0] for x in confirmed if x[1] == "high" and j - x[0] <= p["max_sep"]][-3:]
    if len(lo_sw) < 2 or len(hi_sw) < 2 or max(len(lo_sw), len(hi_sw)) < 3:
        return []
    first = min(lo_sw + hi_sw)
    if j - first < p["range_bars"]:
        return []
    H = [float(D.hi[x]) for x in hi_sw]; L = [float(D.lo[x]) for x in lo_sw]
    mh, ml = sum(H) / len(H), sum(L) / len(L)
    flat_top = (max(H) - min(H)) / mh <= tol
    flat_bot = (max(L) - min(L)) / ml <= tol
    rising = all(b > a * (1 + tol / 2) for a, b in zip(L, L[1:])) and L[-1] / L[0] - 1 >= 0.003
    falling = all(b < a * (1 - tol / 2) for a, b in zip(H, H[1:])) and 1 - H[-1] / H[0] >= 0.003
    height = mh - ml
    if not (p["range_min"] <= height / ml <= p["range_max"]):
        return []
    if D.close[first:i + 1].max() > max(H) * (1 + tol) or D.close[first:i + 1].min() < min(L) * (1 - tol):
        return []                                            # already broken out: not a live structure
    pts = sorted([(x, float(D.hi[x])) for x in hi_sw] + [(x, float(D.lo[x])) for x in lo_sw])
    pole_vol = float(D.vol[first - p["k"]:first + 1].mean()) if first >= p["k"] else float(D.vol[:first + 1].mean())
    box_vol = float(D.vol[first:j + 1].mean())
    out = []
    box = {"box_top": max(H), "box_bottom": min(L)}
    if flat_top and flat_bot:
        mid = (mh + ml) / 2
        out.append(D.make("range_break_long", "long", pts, i, max(H), mid, height, pole_vol, box_vol, j, extra=box))
        out.append(D.make("range_break_short", "short", pts, i, min(L), mid, height, pole_vol, box_vol, j, extra=box))
    elif flat_top and rising:
        out.append(D.make("asc_triangle", "long", pts, i, max(H), L[-1], mh - L[0], pole_vol, box_vol, j, extra=box))
    elif flat_bot and falling:
        out.append(D.make("desc_triangle", "short", pts, i, min(L), H[-1], H[0] - ml, pole_vol, box_vol, j, extra=box))
    for rec in out:                                          # price may wander inside the box; only leaving it the other way abandons
        rec["abandon_level"] = min(L) if rec["side"] == "long" else max(H)
    return out


def _flag(D: _Day, side, i):
    """A pole of >= pole_min within pole_bars, then flag_min..flag_max bars of shallow drift."""
    p = D.params
    hi, lo, vol = D.hi, D.lo, D.vol
    for pole_end in range(i - p["flag_min"], max(p["pole_bars"], i - p["flag_max"]) - 1, -1):
        if pole_end < p["pole_bars"]:
            break
        base_lo, base_hi = pole_end - p["pole_bars"], pole_end
        if side == "long":
            top = float(hi[pole_end]); base = float(lo[base_lo:base_hi + 1].min())
            ph = top - base
            if ph / base < p["pole_min"] or hi[pole_end + 1:i + 1].max() > top or hi[base_lo:pole_end].max() > top:
                continue
            cons_hi, cons_lo = float(hi[pole_end + 1:i + 1].max()), float(lo[pole_end + 1:i + 1].min())
            if cons_lo < top - 0.5 * ph or cons_hi - cons_lo > 0.5 * ph:
                continue
            base_idx = base_lo + int(np.argmin(lo[base_lo:base_hi + 1]))
            pts = [(base_idx, base), (pole_end, top)]
            neck, stop = cons_hi, cons_lo
        else:
            bottom = float(lo[pole_end]); base = float(hi[base_lo:base_hi + 1].max())
            ph = base - bottom
            if ph / base < p["pole_min"] or lo[pole_end + 1:i + 1].min() < bottom or lo[base_lo:pole_end].min() < bottom:
                continue
            cons_hi, cons_lo = float(hi[pole_end + 1:i + 1].max()), float(lo[pole_end + 1:i + 1].min())
            if cons_hi > bottom + 0.5 * ph or cons_hi - cons_lo > 0.5 * ph:
                continue
            base_idx = base_lo + int(np.argmax(hi[base_lo:base_hi + 1]))
            pts = [(base_idx, base), (pole_end, bottom)]
            neck, stop = cons_lo, cons_hi
        pole_vol = float(vol[base_idx:pole_end + 1].mean()); cons_vol = float(vol[pole_end + 1:i + 1].mean())
        rec = D.make("bull_flag" if side == "long" else "bear_flag", side, pts, i, neck, stop, ph, pole_vol, cons_vol, i,
                     extra={"pole_idx": int(pole_end), "pole_pct": ph / base * 100})
        rec["expires_idx"] = pole_end + p["flag_max"]
        rec["pat_first"], rec["pat_last"] = pole_end + 1, i      # the flag's own bars set the breakout-volume median
        return rec
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


def _same_open(pats, kind, neck, stop, tol):
    return any(p["kind"] == kind and p["triggered_at"] is None and p["abandoned_at"] is None and p["superseded_at"] is None
               and abs(p["neckline"] - neck) / neck <= tol and abs(p["stop"] - stop) / stop <= tol for p in pats)


def detect_day(df1m: pd.DataFrame, prev_close: float, params: dict = DEFAULTS, at=None, ref: dict | None = None) -> list[dict]:
    params = {**DEFAULTS, **(params or {})}             # callers may pass a partial override
    b = bars5m(df1m, prev_close, at=at)
    n = len(b)
    if n == 0:
        return []                                        # swing shapes need 2k+1 bars; the opening-range break only orb_bars+1
    D = _Day(b, params, ref)
    close, hi, lo, vol, avg, vwap, delta = D.close, D.hi, D.lo, D.vol, D.avg, D.vwap, D.delta
    gate = params.get("breakout_vol", 0.0) or 0.0
    tol = params["tol"]
    sw = swings(b, params["k"])
    by_conf: dict[int, list] = {}
    for s in sw:
        by_conf.setdefault(s[2], []).append(s)
    confirmed: list = []
    pats: list[dict] = []
    orb_until = pd.Timestamp(f"{b.ts.iloc[0].date()} {params['orb_until']}", tz=b.ts.iloc[0].tz)
    orb_done = {"long": False, "short": False}

    def add(rec):
        if rec is None:
            return
        src = _retest_source(pats, rec["side"], rec["stop"], rec["neckline"], rec["formed_idx"], params)
        if src is not None:
            fa = src["formed_at"]
            rec["facts"]["retest_of"] = fa.isoformat() if hasattr(fa, "isoformat") else str(fa)
            rec["facts"]["retest_until"] = (b.close_at.iloc[src["failed_idx"]] + params.get("retest_bars", 12) * FIVE).isoformat()
        pats.append(rec)

    def trigger(p, i, t):
        """Close through the neckline at bar i: trigger if the bar carries volume, else count a weak close."""
        first, last = p["pat_first"], p["pat_last"]
        med = float(np.median(vol[first:last + 1])) if last >= first else 0.0
        ratio = float(vol[i] / med) if med else 0.0
        if gate and ratio < gate:
            p["facts"]["weak_closes"] += 1
            return False
        long = p["side"] == "long"
        p["triggered_at"], p["trigger_idx"], p["entry"] = t, i, float(close[i])
        p["facts"]["vol_ratio_trigger"] = float(vol[i] / avg[i]) if avg[i] else 0.0
        p["facts"]["vol_ratio_pat_trigger"] = ratio
        p["facts"]["delta_trigger"] = float(delta[i])
        p["facts"]["vwap_ok_trigger"] = bool(close[i] > vwap[i]) if long else bool(close[i] < vwap[i])
        p["target_touched"] = bool(hi[i] >= p["target"]) if long else bool(lo[i] <= p["target"])
        return True

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
                lvl = p.get("abandon_level", p["stop"])
                if (long and close[i] < lvl * (1 - tol)) or (not long and close[i] > lvl * (1 + tol)):
                    p["abandoned_at"] = t
                elif through:
                    if not trigger(p, i, t) and reached:
                        p["abandoned_at"] = t                   # the move happened without a confirming bar
                elif reached or (p["expires_idx"] is not None and i > p["expires_idx"]):
                    p["abandoned_at"] = t
            elif p["failed_at"] is None and i <= p["trigger_idx"] + params["fail_bars"]:
                if not p["target_touched"]:
                    p["target_touched"] = bool(hi[i] >= p["target"]) if long else bool(lo[i] <= p["target"])
                if not p["target_touched"] and ((long and close[i] < p["neckline"]) or (not long and close[i] > p["neckline"])):
                    p["failed_at"], p["failed_idx"] = t, i
        # 2. swings confirmed by this bar: doubles / triples, head & shoulders, ranges and triangles
        for s in by_conf.get(i, []):
            confirmed.append(s)
            j, kind, _ = s
            side = "long" if kind == "low" else "short"
            ext_req = params.get("min_extension", 0.0)
            dp = b.day_pct.iloc[j]
            extended = not ext_req or not (np.isnan(dp) or (dp > -ext_req if side == "long" else dp < ext_req))
            jp = _partner(side, j, confirmed, D)
            if jp is not None and extended:
                px = lo if side == "long" else hi
                open_same = [p for p in pats if p["side"] == side and p["kind"] in KINDS.values() and p["triggered_at"] is None
                             and p["abandoned_at"] is None and p["superseded_at"] is None]
                within = lambda p: all(abs(px[j] - q["price"]) / q["price"] <= tol for q in p["points"])
                ext = next((p for p in open_same if len(p["points"]) == 2 and p["points"][-1]["idx"] == jp and within(p)), None)
                if ext is not None:                      # (a) extends an open double whose last point is the partner → triple
                    ext["superseded_at"] = t
                    add(_double(D, side, [q["idx"] for q in ext["points"]] + [j], i))
                else:
                    same_first = next((p for p in open_same if p["points"][0]["idx"] == jp), None)
                    if same_first is not None:           # (b) the later second point is the current shape
                        same_first["superseded_at"] = t
                        pts = [q["idx"] for q in same_first["points"]] + [j] if (len(same_first["points"]) == 2 and within(same_first)) else [jp, j]
                        add(_double(D, side, pts, i))
                    else:
                        add(_double(D, side, [jp, j], i))
            if extended:
                hs = _head_shoulders(D, side, j, confirmed, i)
                if hs is not None:
                    for p in pats:                       # a later right shoulder on the same head supersedes
                        if p["kind"] == hs["kind"] and p["triggered_at"] is None and p["abandoned_at"] is None and p["superseded_at"] is None \
                           and p["facts"].get("head_idx") == hs["facts"]["head_idx"]:
                            p["superseded_at"] = t
                    add(hs)
            for rec in _ranges(D, j, confirmed, i):
                if not _same_open(pats, rec["kind"], rec["neckline"], rec["stop"], tol):
                    add(rec)
        # 3. per-bar shapes: flags and the opening-range break
        for side in ("long", "short"):
            fl = _flag(D, side, i)
            if fl is not None and not any(p["kind"] == fl["kind"] and p["facts"].get("pole_idx") == fl["facts"]["pole_idx"] for p in pats):
                add(fl)
        ob = params["orb_bars"]
        if i >= ob and t <= orb_until:
            orh, orl = float(hi[:ob].max()), float(lo[:ob].min())
            orh_i, orl_i = int(np.argmax(hi[:ob])), int(np.argmin(lo[:ob]))
            if params["orb_min"] <= (orh - orl) / orl <= params["orb_max"]:
                for side, broke in (("long", close[i] > orh), ("short", close[i] < orl)):
                    if orb_done[side] or not broke:
                        continue
                    neck, stop = (orh, (orh + orl) / 2) if side == "long" else (orl, (orh + orl) / 2)
                    rec = D.make("orb_long" if side == "long" else "orb_short", side, [(orl_i, orl), (orh_i, orh)], i, neck, stop, orh - orl,
                                 float(vol[:ob].mean()), float(vol[ob:i + 1].mean()) if i > ob else float(vol[i]), i,
                                 extra={"or_high": orh, "or_low": orl})
                    rec["pat_first"], rec["pat_last"] = 0, ob - 1
                    if trigger(rec, i, t):                   # created already triggered: the break is the event
                        orb_done[side] = True
                        add(rec)
    return pats


def checks_for(f: dict, side: str, state: str) -> list[bool]:
    """The nine checks from a pattern's facts alone, so live, replay and stored rows agree:
    extended day (with the reversal, or with the trend for continuation shapes), OI confirming
    (lower at the second point for reversals, higher for continuation), volume vs the day's
    average, sector agrees, VWAP side, exhaustion (second point's / consolidation bars traded
    less than the first's / the pole), breakout volume (confirming close >= 1.5x the median of
    the pattern's bars), breakout delta (tick-rule delta with the direction), and the confirming
    close itself. Forming rows use the facts frozen at formation; triggered/failed rows those at
    the trigger."""
    long = side == "long"
    cont = bool(f.get("continuation"))
    trig = state not in ("forming", "retest")
    dp = f.get("day_pct_p2")
    if cont:
        extended = dp is not None and (dp >= 1.0 if long else dp <= -1.0)
    else:
        extended = dp is not None and (dp <= -1.0 if long else dp >= 1.0)
    o1, o2 = f.get("oi_p1"), f.get("oi_p2")
    oi_ok = bool(o1 and o2 and ((o2 > o1) if cont else (o2 < o1)))
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


INTERNAL = ("facts", "target_touched", "formed_idx", "trigger_idx", "failed_idx", "expires_idx", "pat_first", "pat_last", "abandon_level")


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
        rows.append({**{k: v for k, v in p.items() if k not in INTERNAL},
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
                "hit_rate": hits / (hits + stops) * 100 if hits + stops else None, "decided": hits + stops,
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


def confidence(stats: dict, kind: str, score: int | None, state: str) -> dict | None:
    """Recorded hit rate for this kind, in the row's score bucket once triggered (forming rows
    use the kind's overall record), with the sample it rests on. None without history."""
    s = (stats or {}).get(kind)
    if not s:
        return None
    if state in ("triggered", "failed") and score is not None:
        bucket = s.get("by_score", {}).get(f">={SCORE_SPLIT}" if score >= SCORE_SPLIT else f"<{SCORE_SPLIT}")
        if bucket and bucket.get("decided"):
            return {"hit_rate": round(bucket["hit_rate"], 1), "n": bucket["decided"], "basis": f"score {'>=' if score >= SCORE_SPLIT else '<'}{SCORE_SPLIT}"}
    if s.get("decided"):
        return {"hit_rate": round(s["hit_rate"], 1), "n": s["decided"], "basis": "all triggers"}
    return None
