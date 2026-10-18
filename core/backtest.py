"""Replay a rule scan over a stored day with exactly the view the live engine had
at every minute (see core.asof), plus the outcome of a trade taken at an alert.

`fires_naive` is the definition: resample(view_at(df, ts)) and evaluate, for every
minute. `fires` is the fast equivalent used by the API; the test suite proves
they agree.
"""
import pandas as pd
from .asof import view_at
from .rules import resample, evaluate

TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
FIELDS = ("open", "high", "low", "close", "volume", "oi", "vwap")


def max_offset(scan: dict) -> int:
    return max((int(side.get("off", 0)) for r in scan["rules"] for side in (r["lhs"], r["rhs"]) if "field" in side),
               default=0)


def fires_naive(scan: dict, df1m: pd.DataFrame, ctx: dict, cooldown_min: int) -> list[pd.Timestamp]:
    tf = scan.get("timeframe", "5m")
    cd = pd.Timedelta(minutes=cooldown_min)
    out: list[pd.Timestamp] = []
    for ts in df1m.ts:
        if out and ts - out[-1] < cd:
            continue
        if evaluate(scan, resample(view_at(df1m, ts), tf), ctx):
            out.append(ts)
    return out


def fires(scan: dict, df1m: pd.DataFrame, ctx: dict, cooldown_min: int) -> list[pd.Timestamp]:
    if df1m.empty:
        return []
    tf = scan.get("timeframe", "5m")
    tfm = TF_MIN[tf]
    k = max_offset(scan) + 1          # bars the rule can see
    cd = pd.Timedelta(minutes=cooldown_min)

    ts = df1m.ts.reset_index(drop=True)
    anchor = ts.iloc[0].normalize() + pd.Timedelta(hours=9, minutes=15)
    win = ((ts - anchor) // pd.Timedelta(minutes=tfm)).to_numpy()
    o, h, l, c = (df1m[x].to_numpy(dtype=float) for x in ("open", "high", "low", "close"))
    v, oi = df1m.volume.to_numpy(dtype=float), df1m.oi.to_numpy(dtype=float)

    # completed windows: identical to resample() over the whole day, consumed in order
    full = resample(df1m, tf)
    full_win = ((full.ts - anchor) // pd.Timedelta(minutes=tfm)).to_numpy()
    full_rows = {f: full[f].to_numpy(dtype=float) for f in FIELDS}
    full_cum_num = ((full.high + full.low + full.close) / 3 * full.volume).cumsum().to_numpy(dtype=float)
    full_cum_vol = full.volume.cumsum().to_numpy(dtype=float)

    out: list[pd.Timestamp] = []
    done = 0                       # number of completed windows (rows of `full`) before the current minute
    f_open = f_high = f_low = f_vol = 0.0
    cur_win = None
    for i in range(len(ts)):
        if win[i] != cur_win:      # new window: previous one is now complete
            while done < len(full_win) and full_win[done] < win[i]:
                done += 1
            cur_win, f_open, f_high, f_low, f_vol = win[i], None, None, None, 0.0
        stub_open = o[i]
        stub_oi = oi[i - 1] if i else oi[i]
        fo = stub_open if f_open is None else f_open
        fh = stub_open if f_high is None else max(f_high, stub_open)
        fl = stub_open if f_low is None else min(f_low, stub_open)
        tp = (fh + fl + stub_open) / 3
        num = (full_cum_num[done - 1] if done else 0.0) + tp * f_vol
        cv = (full_cum_vol[done - 1] if done else 0.0) + f_vol
        forming = {"open": fo, "high": fh, "low": fl, "close": stub_open, "volume": f_vol, "oi": stub_oi,
                   "vwap": num / (cv if cv > 0 else 1.0)}
        lo = max(0, done - (k - 1))
        bars = pd.DataFrame({f: list(full_rows[f][lo:done]) + [forming[f]] for f in FIELDS})
        if (not out or ts.iloc[i] - out[-1] >= cd) and evaluate(scan, bars, ctx):
            out.append(ts.iloc[i])
        # fold this complete minute into the forming window for the next iteration
        f_open = o[i] if f_open is None else f_open
        f_high = h[i] if f_high is None else max(f_high, h[i])
        f_low = l[i] if f_low is None else min(f_low, l[i])
        f_vol += v[i]
    return out


def outcome(df1m: pd.DataFrame, at: pd.Timestamp, side: str, entry: float, stop: float, target: float) -> dict:
    """What a trade taken at `entry` at minute `at` did: signed % returns after
    15/30/60 minutes (favourable = positive) and which of target/stop was touched
    first before the close ("target" | "stop" | "none"). A bar touching both
    counts as "stop"."""
    long = side == "long"
    after = df1m[df1m.ts > at]

    def ret(n: int):
        upto = after[after.ts <= at + pd.Timedelta(minutes=n)]
        if upto.empty or df1m.ts.iloc[-1] < at + pd.Timedelta(minutes=n):
            return None
        close = float(upto.close.iloc[-1])
        return ((close / entry - 1) if long else (entry / close - 1)) * 100

    hit = "none"
    for hi, lo in zip(after.high.to_numpy(dtype=float), after.low.to_numpy(dtype=float)):
        hit_stop = lo <= stop if long else hi >= stop
        hit_target = hi >= target if long else lo <= target
        if hit_stop:
            hit = "stop"; break
        if hit_target:
            hit = "target"; break
    return {"r15": ret(15), "r30": ret(30), "r60": ret(60), "hit": hit}


def time_to_target_min(df1m: pd.DataFrame, at: pd.Timestamp, side: str, target: float) -> int | None:
    """Minutes from `at` to the first later bar that touches `target`; None if never."""
    after = df1m[df1m.ts > at]
    touched = after.high >= target if side == "long" else after.low <= target
    if not touched.any():
        return None
    first = after.ts[touched].iloc[0]
    return int((first - at).total_seconds() // 60)


def grade_pattern(df1m: pd.DataFrame, p: dict) -> dict | None:
    """outcome() for a triggered pattern plus minutes to target; None when not triggered."""
    if not p.get("triggered_at"):
        return None
    at = pd.Timestamp(p["triggered_at"])
    o = outcome(df1m, at, p["side"], float(p["entry"]), float(p["stop"]), float(p["target"]))
    o["tt_min"] = time_to_target_min(df1m, at, p["side"], float(p["target"]))
    return o
