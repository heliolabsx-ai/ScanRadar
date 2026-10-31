"""The Time Machine: the odds of a trade plan from the stock's own past sessions.

From the trigger bar to `flat_by`, `monte_carlo` rebuilds the rest of the session
`n` times by drawing each future 5-minute bar (as h/o, l/o, c/o ratios) from a
random past session that has that slot, and records which level each path touches
first. `past_sessions` runs the same plan, at the same percentage distances, on
every past session from its own close at the trigger bar. Both use one first-touch
rule: a bar touching both levels counts as the stop.

Pure functions, no I/O. Bars are 5-minute, anchored at 09:15, keyed by bar start
"HH:MM". `replay` only ever borrows sessions strictly before the plan's day.
"""
import numpy as np
import pandas as pd

N_PATHS = 4000
SEED = 19
SESSIONS = 60
FLAT_BY = "15:20"
IST = "Asia/Kolkata"
OPEN_MIN = 9 * 60 + 15
LAST_N = 12


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def trigger_bar(ts: pd.Timestamp) -> str:
    """The 5-minute bar (start HH:MM) whose close is the trigger at `ts`."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(IST)
    t = (ts - pd.Timedelta(minutes=1)).floor("min")
    m = t.hour * 60 + t.minute
    return _hhmm(OPEN_MIN + (m - OPEN_MIN) // 5 * 5)


def bars5(df1m: pd.DataFrame) -> dict[str, tuple[float, float, float, float]]:
    """1-minute candles → {bar start: (o, h, l, c)} of 5-minute bars, in time order."""
    if df1m.empty:
        return {}
    df = df1m.sort_values("ts")
    ts = df.ts.dt.tz_convert(IST) if df.ts.dt.tz is not None else df.ts
    m = (ts.dt.hour * 60 + ts.dt.minute).to_numpy()
    keep = m >= OPEN_MIN
    df = df[keep]
    slot = OPEN_MIN + (m[keep] - OPEN_MIN) // 5 * 5
    g = df.groupby(slot, sort=True).agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"))
    return {_hhmm(int(s)): (float(o), float(h), float(l), float(c))
            for s, o, h, l, c in zip(g.index, g.o, g.h, g.l, g.c)}


def history(frames: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """{"YYYY-MM-DD": bars5(df)} sorted by day; sessions without bars are left out."""
    out = {}
    for day in sorted(frames):
        b = bars5(frames[day])
        if b:
            out[day] = b
    return out


def _slots(at: str, flat_by: str) -> list[str]:
    """Today's future bar starts: strictly after `at`, strictly before `flat_by`."""
    a, f = _min(at), _min(flat_by)
    return [_hhmm(s) for s in range(OPEN_MIN, f, 5) if s > a]


def _rr(entry: float, stop: float, target: float) -> tuple[float, float]:
    risk = abs(entry - stop)
    return risk, (abs(target - entry) / risk if risk else 0.0)


def monte_carlo(hist, at: str, side: str, entry: float, stop: float, target: float, *,
                flat_by=FLAT_BY, n=N_PATHS, seed=SEED) -> dict:
    """Odds of target first / stop first / neither by `flat_by` over `n` seeded paths."""
    long = side == "long"
    days = sorted(hist)
    risk, rr = _rr(entry, stop, target)
    slots = _slots(at, flat_by)
    rng = np.random.default_rng(seed)
    price = np.full(n, float(entry))
    state = np.zeros(n, dtype=np.int8)                    # 0 open, 1 target, 2 stop
    tt = np.zeros(n, dtype=np.int64)
    for k, s in enumerate(slots):
        bars = [hist[d][s] for d in days if s in hist[d]]
        if not bars:
            continue                                      # nobody has this slot: the running price carries over
        a = np.asarray(bars, dtype=float)
        ratios = np.column_stack([a[:, 1] / a[:, 0], a[:, 2] / a[:, 0], a[:, 3] / a[:, 0]])
        r = ratios[rng.integers(0, len(ratios), n)]
        h, l, c = price * r[:, 0], price * r[:, 1], price * r[:, 2]
        hit_stop = l <= stop if long else h >= stop
        hit_target = h >= target if long else l <= target
        live = state == 0
        s_now = live & hit_stop
        t_now = live & ~hit_stop & hit_target
        state[s_now] = 2
        state[t_now] = 1
        tt[t_now] = (k + 1) * 5
        price = c
    if risk:
        none_r = ((price - entry) if long else (entry - price)) / risk
        rmult = np.where(state == 1, rr, np.where(state == 2, -1.0, none_r))
    else:
        rmult = np.zeros(n)
    p10, p50, p90 = np.percentile(rmult, [10, 50, 90])
    tgt = tt[state == 1]
    return {"target_pct": round(float((state == 1).mean() * 100), 1),
            "stop_pct": round(float((state == 2).mean() * 100), 1),
            "none_pct": round(float((state == 0).mean() * 100), 1),
            "avg_r": round(float(rmult.mean()), 2),
            "close_r": {"p10": round(float(p10), 2), "p50": round(float(p50), 2), "p90": round(float(p90), 2)},
            "median_tt_min": int(round(float(np.median(tgt)))) if len(tgt) else None,
            "paths": n, "sessions": len(days), "bars_left": len(slots)}


def past_sessions(hist, at, side, entry, stop, target, *, flat_by=FLAT_BY) -> dict:
    """The same plan on every past session with a bar at `at`: entry at that bar's close,
    stop and target at the plan's percentage distances, first touch to `flat_by`.
    `last` = the last 12 sessions as [day, result, minutes from the trigger close to exit]."""
    long = side == "long"
    slots = _slots(at, flat_by)
    counts = {"target": 0, "stop": 0, "none": 0}
    rows = []
    for day in sorted(hist):
        bars = hist[day]
        if at not in bars:
            continue
        e = bars[at][3]
        st, tg = e * stop / entry, e * target / entry
        result, minutes = "none", 0
        for k, s in enumerate(slots):
            if s not in bars:
                continue
            _, h, l, _ = bars[s]
            minutes = (k + 1) * 5
            if (l <= st) if long else (h >= st):
                result = "stop"; break
            if (h >= tg) if long else (l <= tg):
                result = "target"; break
        counts[result] += 1
        rows.append([day, result, minutes])
    return {**counts, "last": rows[-LAST_N:]}


def verdict(mc: dict, rr: float, *, flat_by=FLAT_BY) -> tuple[str, str]:
    """One word (flat | coin | edge | bad) and its sentence, checked in that order."""
    t, s, none, avg = mc["target_pct"], mc["stop_pct"], mc["none_pct"], mc["avg_r"]
    if none >= 75:
        return "flat", (f"Will not resolve before the close: {none:.1f} % of replays reach {flat_by} "
                        f"with neither level touched.")
    if abs(t - s) < 8:
        return "coin", f"Close to a coin flip: T1 first {t:.1f} %, stop first {s:.1f} %, average {avg:.2f} R."
    if t > s:
        return "edge", f"Odds favour T1: {t:.1f} % against {s:.1f} %, average {avg:.2f} R."
    return "bad", f"Odds favour the stop: {s:.1f} % against {t:.1f} %."


def replay(plan: dict, hist: dict) -> dict | None:
    """Monte Carlo + past sessions + verdict for `plan`, from the last SESSIONS sessions
    strictly before plan["day"]; None when there is none. `plan["flat_by"]` is optional."""
    flat_by = plan.get("flat_by") or FLAT_BY
    day = str(plan["day"])
    keep = sorted(d for d in hist if d < day)[-SESSIONS:]
    if not keep:
        return None
    past = {d: hist[d] for d in keep}
    at, side = plan["at"], plan["side"]
    entry, stop, target = float(plan["entry"]), float(plan["stop"]), float(plan["target"])
    _, rr = _rr(entry, stop, target)
    mc = monte_carlo(past, at, side, entry, stop, target, flat_by=flat_by)
    word, sentence = verdict(mc, rr, flat_by=flat_by)
    return {"mc": mc, "past": past_sessions(past, at, side, entry, stop, target, flat_by=flat_by),
            "verdict": word, "sentence": sentence, "rr": round(rr, 2), "flat_by": flat_by}
