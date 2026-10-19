"""Patterns for the API: detection over a day's frames (shared by the backtest CLI,
the day preparation job and the live loop), stored rows → view at a minute,
today's rows on demand, and per-kind statistics."""
import os, json, datetime as dt
import pandas as pd
from core.patterns import detect_day, as_of, bars5m, DEFAULTS, detector_hash, summarise, checks_for, state_at, CHECKS, INTERNAL, confidence
from core.backtest import grade_pattern
from replay import IST, load_day, load_refs

COLS = ["day", "symbol", "kind", "tf", "side", "formed_at", "triggered_at", "failed_at", "abandoned_at", "superseded_at",
        "neckline", "entry", "stop", "target", "points", "checks", "score", "outcome", "facts", "source", "detector"]
MUTABLE = ["triggered_at", "entry", "failed_at", "abandoned_at", "superseded_at", "checks", "score", "facts"]
UPSERT_SQL = (f"INSERT INTO patterns({','.join(COLS)}) VALUES({','.join(['%s'] * len(COLS))}) "
              "ON CONFLICT (day, symbol, kind, formed_at) DO UPDATE SET "
              + ", ".join(f"{c}=EXCLUDED.{c}" for c in MUTABLE)
              + ", outcome=COALESCE(EXCLUDED.outcome, patterns.outcome)")
SELECT_COLS = ["id"] + COLS
_iso = lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)   # noqa: E731


def _py(ts):
    return None if ts is None else ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts


def row_to_record(day, symbol: str, p: dict, source: str, detector: str) -> tuple:
    points = [{**q, "ts": _iso(q["ts"])} for q in p["points"]]
    return (day, symbol, p["kind"], "5m", p["side"], _py(p["formed_at"]), _py(p.get("triggered_at")), _py(p.get("failed_at")),
            _py(p.get("abandoned_at")), _py(p.get("superseded_at")), p["neckline"], p.get("entry"), p["stop"], p["target"],
            json.dumps(points, default=float), json.dumps(p.get("checks")), p.get("score"),
            json.dumps(p.get("outcome"), default=float) if p.get("outcome") is not None else None,
            json.dumps(p.get("facts"), default=float) if p.get("facts") is not None else None, source, detector)


def to_json_rows(rows: list[dict]) -> list[dict]:
    """JSON-safe rows with ISO timestamps in every path (today and stored must match)."""
    return json.loads(json.dumps(rows, default=_iso))


# ---------- detection over a day's frames ----------
def sector_pct_map(frames: dict, refs: dict, at) -> dict:
    """sector -> mean % change vs prev close of the last complete minute before `at`."""
    acc: dict = {}
    for s, df in frames.items():
        ref = refs.get(s)
        if not ref or not ref.get("prev_close"):
            continue
        n = int(df.ts.searchsorted(at, side="left"))
        if not n:
            continue
        acc.setdefault(ref.get("sector", "OTHERS"), []).append((float(df.close.iloc[n - 1]) / float(ref["prev_close"]) - 1) * 100)
    return {k: sum(v) / len(v) for k, v in acc.items()}


def _clean(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in INTERNAL or k == "facts"}


def detect_frames(frames: dict, refs: dict, at=None, params: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Run the detector over every symbol. Returns (records, rows):
    records = every pattern (incl. abandoned/superseded) with checks/score as at its
    knowable minute, facts and outcome, for persistence; rows = the as-of view at `at`
    (when given). Frames must already be cut to ts < at for the live view."""
    params = params or DEFAULTS
    sec_cache: dict = {}

    def sector_at(minute):
        minute = pd.Timestamp(minute)
        if minute not in sec_cache:
            sec_cache[minute] = sector_pct_map(frames, refs, minute)
        return sec_cache[minute]

    records, rows = [], []
    for sym, df in frames.items():
        ref = refs.get(sym)
        if not ref or not ref.get("prev_close") or df.empty:
            continue
        pats = detect_day(df, float(ref["prev_close"]), params, at=at, ref=ref)
        if not pats:
            continue
        sector = ref.get("sector", "OTHERS")
        ctx = {"sector_at": lambda m, s=sector: sector_at(m).get(s)}
        for p in pats:
            know = p["triggered_at"] or p["formed_at"]
            v = as_of([p], know, ctx)
            rec = _clean(p)
            if v:
                rec["facts"], rec["checks"], rec["score"] = v[0]["facts"], v[0]["checks"], v[0]["score"]
            rec["symbol"], rec["outcome"] = sym, grade_pattern(df, p)
            records.append(rec)
        if at is not None:
            for r in as_of(pats, at, ctx):
                r["symbol"] = sym
                r["outcome"] = grade_pattern(df, r) if r["state"] != "forming" else None
                rows.append(r)
    return records, rows


def patterns_for_day(con, day, params: dict | None = None) -> list[dict]:
    frames, refs = load_day(con, day), load_refs(con, day)
    return detect_frames(frames, refs, None, params)[0]


def upsert_patterns(cur, day, rows: list[dict], source: str, detector: str) -> int:
    if source == "replay":
        cur.execute("DELETE FROM patterns WHERE day=%s AND detector<>%s", (day, detector))
    recs = [row_to_record(day, r["symbol"], r, source, detector) for r in rows]
    if not recs:
        return 0
    cur.executemany(UPSERT_SQL, recs)
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def patterns_needed(con, day, detector: str) -> bool:
    """A settled day needs preparation when it has no replay rows under this detector,
    or when a (live) triggered row is still ungraded."""
    with con.cursor() as cur:
        cur.execute("SELECT 1 FROM patterns WHERE day=%s AND detector=%s AND source='replay' LIMIT 1", (day, detector))
        has_replay = cur.fetchone() is not None
        cur.execute("SELECT 1 FROM patterns WHERE day=%s AND triggered_at IS NOT NULL AND outcome IS NULL LIMIT 1", (day,))
        ungraded = cur.fetchone() is not None
    return (not has_replay) or ungraded


# ---------- views ----------
def pattern_state(r: dict, at) -> str:
    return state_at(r, at) or "forming"


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
        trig = state in ("triggered", "failed")
        if r.get("facts"):
            checks = checks_for(r["facts"], r["side"], state)
        else:
            checks = (list(r.get("checks") or []) + [False] * CHECKS)[:CHECKS]; checks[CHECKS - 1] = trig
        ref = r["entry"] if trig and r.get("entry") is not None else r["neckline"]
        risk = abs(ref - r["stop"])
        since = r["triggered_at"] if trig else r["formed_at"]
        row = {k: v for k, v in r.items() if k not in ("detector",)}
        row.update({"state": state, "checks": checks, "score": sum(1 for c in checks[:CHECKS - 1] if c),
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
    at = pd.Timestamp(at)
    cut = {s: df[df.ts < at] for s, df in frames.items()}
    cut = {s: df for s, df in cut.items() if len(df) >= 5 * (2 * (params or DEFAULTS)["k"] + 1)}
    if not cut:
        return []
    return [{**r, "source": "live"} for r in detect_frames(cut, refs, at, params)[1]]


def today_rows(con, at: dt.datetime) -> list[dict]:
    frames = load_day(con, at.date(), upto=at)
    if not frames:
        return []
    return today_rows_from_frames(frames, load_refs(con, at.date()), pd.Timestamp(at))


def today_rows_json(at_iso: str) -> list[dict]:
    """Process-pool entry point: own connection, JSON-safe result."""
    import psycopg
    at = dt.datetime.fromisoformat(at_iso)
    with psycopg.connect(os.environ["DATABASE_URL"]) as con:
        return to_json_rows(today_rows(con, at))


confidence = confidence   # re-exported for the live loops


def with_confidence(rows: list[dict], st: dict | None) -> list[dict]:
    """Attach the recorded hit rate (kind, score bucket) to each row."""
    for r in rows:
        r["confidence"] = confidence(st, r["kind"], r.get("score"), r.get("state", "forming"))
    return rows


def stats(con, days: int) -> dict:
    since = dt.datetime.now(IST).date() - dt.timedelta(days=int(days * 7 / 5) + 3)
    with con.cursor() as cur:
        cur.execute("SELECT kind, score, triggered_at, failed_at, outcome, source, facts FROM patterns WHERE day >= %s AND superseded_at IS NULL", (since,))
        rows = [{"kind": k, "score": sc, "triggered_at": t, "failed_at": f, "outcome": o, "source": s, "facts": fc} for k, sc, t, f, o, s, fc in cur.fetchall()]
    return summarise(rows)
