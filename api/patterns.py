"""Patterns for the API: stored rows → view at a minute, today's rows on demand,
persistence shared with the backtest CLI, and per-kind statistics."""
import json, datetime as dt
import pandas as pd
from core.patterns import detect_day, as_of, bars5m, DEFAULTS, detector_hash, summarise
from core.backtest import grade_pattern
from replay import IST, load_day, load_refs

COLS = ["day", "symbol", "kind", "tf", "side", "formed_at", "triggered_at", "failed_at", "abandoned_at", "superseded_at",
        "neckline", "entry", "stop", "target", "points", "checks", "score", "outcome", "source", "detector"]
UPSERT_SQL = (f"INSERT INTO patterns({','.join(COLS)}) VALUES({','.join(['%s'] * len(COLS))}) "
              "ON CONFLICT (day, symbol, kind, formed_at) DO NOTHING")
SELECT_COLS = ["id"] + COLS


def _py(ts):
    return None if ts is None else ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts


def row_to_record(day, symbol: str, p: dict, source: str, detector: str) -> tuple:
    points = [{**q, "ts": q["ts"].isoformat() if hasattr(q["ts"], "isoformat") else q["ts"]} for q in p["points"]]
    return (day, symbol, p["kind"], "5m", p["side"], _py(p["formed_at"]), _py(p.get("triggered_at")), _py(p.get("failed_at")),
            _py(p.get("abandoned_at")), _py(p.get("superseded_at")), p["neckline"], p.get("entry"), p["stop"], p["target"],
            json.dumps(points, default=float), json.dumps(p.get("checks")), p.get("score"),
            json.dumps(p.get("outcome"), default=float) if p.get("outcome") is not None else None, source, detector)


# ---------- detection over a day's frames ----------
def sector_pct_map(frames: dict, refs: dict, at) -> dict:
    """sector -> mean % change vs prev close of the last close at or before `at`."""
    acc: dict = {}
    for s, df in frames.items():
        ref = refs.get(s)
        if not ref or not ref.get("prev_close"):
            continue
        n = int(df.ts.searchsorted(at, side="right"))
        if not n:
            continue
        acc.setdefault(ref.get("sector", "OTHERS"), []).append((float(df.close.iloc[n - 1]) / float(ref["prev_close"]) - 1) * 100)
    return {k: sum(v) / len(v) for k, v in acc.items()}


def _clean(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in ("facts", "target_touched", "formed_idx", "trigger_idx")}


def patterns_for_day(con, day, params: dict | None = None) -> list[dict]:
    """Detect, score at the knowable minute (trigger, else formation) and grade every
    pattern of a stored day. Used by the backtest CLI and the day preparation job."""
    params = params or DEFAULTS
    frames, refs = load_day(con, day), load_refs(con, day)
    out = []
    sec_cache: dict = {}
    for sym, df in frames.items():
        ref = refs.get(sym)
        if not ref or not ref.get("prev_close"):
            continue
        pats = detect_day(df, float(ref["prev_close"]), params)
        if not pats:
            continue
        bars = bars5m(df, float(ref["prev_close"]))
        for p in pats:
            at = p["triggered_at"] or p["formed_at"]
            if at not in sec_cache:
                sec_cache[at] = sector_pct_map(frames, refs, at)
            rows = as_of([p], at, {"sector_pct": sec_cache[at].get(ref.get("sector", "OTHERS")), "bars": bars})
            r = rows[0] if rows else None
            out.append({**_clean(p), "symbol": sym, "checks": r["checks"] if r else None, "score": r["score"] if r else None,
                        "outcome": grade_pattern(df, p)})
    return out


def upsert_patterns(cur, day, rows: list[dict], source: str, detector: str) -> int:
    if source == "replay":
        cur.execute("DELETE FROM patterns WHERE day=%s AND source='replay' AND detector<>%s", (day, detector))
    recs = [row_to_record(day, r["symbol"], r, source, detector) for r in rows]
    if not recs:
        return 0
    cur.executemany(UPSERT_SQL, recs)
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def patterns_needed(con, day, detector: str) -> bool:
    with con.cursor() as cur:
        cur.execute("SELECT 1 FROM patterns WHERE day=%s AND detector=%s LIMIT 1", (day, detector))
        return cur.fetchone() is None


# ---------- views ----------
def pattern_state(r: dict, at) -> str:
    if r.get("failed_at") is not None and r["failed_at"] <= at:
        return "failed"
    if r.get("triggered_at") is not None and r["triggered_at"] <= at:
        return "triggered"
    return "forming"


def rows_at(stored: list[dict], at) -> list[dict]:
    """State of stored rows at minute `at`; nothing after `at` leaks into the row."""
    out = []
    for r in stored:
        if r["formed_at"] > at:
            continue
        if r.get("abandoned_at") is not None and r["abandoned_at"] <= at:
            continue
        if r.get("superseded_at") is not None and r["superseded_at"] <= at:
            continue
        state = pattern_state(r, at)
        trig = state != "forming"
        checks = list(r.get("checks") or [False] * 6)
        checks[5] = trig
        ref = r["entry"] if trig and r.get("entry") is not None else r["neckline"]
        risk = abs(ref - r["stop"])
        since = r["triggered_at"] if trig else r["formed_at"]
        row = {k: v for k, v in r.items() if k not in ("detector",)}
        row.update({"state": state, "checks": checks, "score": sum(1 for c in checks[:5] if c),
                    "entry": r["entry"] if trig else None, "triggered_at": r["triggered_at"] if trig else None,
                    "failed_at": r["failed_at"] if state == "failed" else None,
                    "outcome": r.get("outcome") if trig else None,
                    "rr": abs(r["target"] - ref) / risk if risk else 0.0,
                    "age_min": int((at - since).total_seconds() // 60)})
        out.append(row)
    return out


def stored_rows(con, day) -> list[dict]:
    with con.cursor() as cur:
        cur.execute(f"SELECT {','.join(SELECT_COLS)} FROM patterns WHERE day=%s ORDER BY formed_at, symbol", (day,))
        rows = []
        for rec in cur.fetchall():
            r = dict(zip(SELECT_COLS, rec))
            for k in ("formed_at", "triggered_at", "failed_at", "abandoned_at", "superseded_at"):
                if r[k] is not None:
                    r[k] = r[k].astimezone(IST)
            rows.append(r)
    return rows


def today_rows_from_frames(frames: dict, refs: dict, at, params: dict | None = None) -> list[dict]:
    params = params or DEFAULTS
    at = pd.Timestamp(at)
    secs = sector_pct_map(frames, refs, at)
    out = []
    for sym, df in frames.items():
        ref = refs.get(sym)
        if not ref or not ref.get("prev_close"):
            continue
        df = df[df.ts < at]
        if len(df) < 5 * (2 * params["k"] + 1):
            continue
        pats = detect_day(df, float(ref["prev_close"]), params)
        if not pats:
            continue
        rows = as_of(pats, at, {"sector_pct": secs.get(ref.get("sector", "OTHERS")), "bars": bars5m(df, float(ref["prev_close"]))})
        out += [{**r, "symbol": sym, "source": "live"} for r in rows]
    return out


def today_rows(con, at: dt.datetime) -> list[dict]:
    frames = load_day(con, at.date(), upto=at)
    if not frames:
        return []
    return today_rows_from_frames(frames, load_refs(con, at.date()), pd.Timestamp(at))


def stats(con, days: int) -> dict:
    since = dt.datetime.now(IST).date() - dt.timedelta(days=int(days * 7 / 5) + 3)
    with con.cursor() as cur:
        cur.execute("SELECT kind, score, triggered_at, failed_at, outcome, source FROM patterns WHERE day >= %s AND superseded_at IS NULL", (since,))
        rows = [{"kind": k, "score": sc, "triggered_at": t, "failed_at": f, "outcome": o, "source": s} for k, sc, t, f, o, s in cur.fetchall()]
    return summarise(rows)
