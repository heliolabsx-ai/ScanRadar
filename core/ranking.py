"""Ranking snapshot: the sector-average / score / rank / picked logic the live
engine runs every minute, as one function so replay produces identical output."""
import datetime as dt
import pandas as pd
from .analytics import analyse, score

TOP_N = 3
MIN_RR = 1.5
PER_SIDE = 40


def rank(frames: dict[str, pd.DataFrame], refs: dict[str, dict], ts: dt.datetime, mode: str = "live") -> dict | None:
    rows = []
    for s, df in frames.items():
        a = analyse(s, df, refs.get(s, {}))
        if a:
            rows.append(a)
    if not rows:
        return None
    sec: dict[str, list[float]] = {}
    for a in rows:
        sec.setdefault(a["sector"], []).append(a["pct"])
    sec_avg = {k: sum(v) / len(v) for k, v in sec.items()}
    for a in rows:
        a["score"] = score(a, sec_avg[a["sector"]])
    rows.sort(key=lambda a: -a["score"])
    n = len(rows)
    for i, a in enumerate(rows):
        a["rank"], a["pctile"] = i + 1, (1 - i / n) * 100
        a["conv"] = round(min(99, 30 + a["pctile"] * 0.7))
    bulls = [a for a in rows if a["dir"] > 0]
    bears = [a for a in rows if a["dir"] < 0]
    for lst in (bulls, bears):
        for i, a in enumerate(lst):
            a["side_rank"] = i + 1
            a["picked"] = i < TOP_N and a["rr"] >= MIN_RR
    delta_available = any(bool((f.delta != 0).any()) for f in frames.values() if "delta" in f)
    return {"ts": ts.isoformat(), "mode": mode, "delta_available": delta_available, "sectors": sec_avg,
            "n_bull": len(bulls), "n_bear": len(bears), "bulls": bulls[:PER_SIDE], "bears": bears[:PER_SIDE]}
