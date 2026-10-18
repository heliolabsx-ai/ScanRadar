#!/usr/bin/env python
"""Backtest the pattern detector over stored days, persist rows with outcomes, print
per-kind statistics and write a markdown note.

    docker compose run --rm -T api python scripts/backtest_patterns.py --days 60 --note /app/notes/patterns-backtest-2026-10-18.md
    docker compose run --rm -T api python scripts/backtest_patterns.py --days 5 --no-write

Runs inside the api image (core/ + replay.py on the path). Idempotent: rows are keyed
by (day, symbol, kind, formed_at); replay rows from an older detector hash are replaced.
"""
import os, sys, json, argparse, datetime as dt, statistics, time

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "api"), "/app"):
    if p not in sys.path:
        sys.path.insert(0, p)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
COLS = ["day", "symbol", "kind", "tf", "side", "formed_at", "triggered_at", "failed_at", "abandoned_at", "superseded_at",
        "neckline", "entry", "stop", "target", "points", "checks", "score", "outcome", "source", "detector"]
UPSERT_SQL = (f"INSERT INTO patterns({','.join(COLS)}) VALUES({','.join(['%s'] * len(COLS))}) "
              "ON CONFLICT (day, symbol, kind, formed_at) DO NOTHING")


def _py(ts):
    return None if ts is None else ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts


def row_to_record(day, symbol: str, p: dict, source: str, detector: str) -> tuple:
    points = [{**q, "ts": q["ts"].isoformat()} for q in p["points"]]
    return (day, symbol, p["kind"], "5m", p["side"], _py(p["formed_at"]), _py(p.get("triggered_at")), _py(p.get("failed_at")),
            _py(p.get("abandoned_at")), _py(p.get("superseded_at")), p["neckline"], p.get("entry"), p["stop"], p["target"],
            json.dumps(points, default=float), json.dumps(p.get("checks")), p.get("score"),
            json.dumps(p.get("outcome"), default=float) if p.get("outcome") is not None else None, source, detector)


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


def sector_pct_at(frames: dict, refs: dict, members: list, at) -> float | None:
    vals = []
    for s in members:
        df, ref = frames.get(s), refs.get(s)
        if df is None or not ref or not ref.get("prev_close"):
            continue
        n = int(df.ts.searchsorted(at, side="right"))
        if n:
            vals.append((float(df.close.iloc[n - 1]) / float(ref["prev_close"]) - 1) * 100)
    return sum(vals) / len(vals) if vals else None


def patterns_for_day(con, day, params=None) -> list[dict]:
    """Detect, score at the knowable minute, and grade every pattern of a stored day."""
    from core.patterns import detect_day, as_of, bars5m, DEFAULTS
    from core.backtest import grade_pattern
    import replay as rp
    params = params or DEFAULTS
    frames, refs = rp.load_day(con, day), rp.load_refs(con, day)
    by_sector: dict = {}
    for s, r in refs.items():
        by_sector.setdefault(r.get("sector", "OTHERS"), []).append(s)
    out = []
    for sym, df in frames.items():
        ref = refs.get(sym)
        if not ref or not ref.get("prev_close"):
            continue
        pats = detect_day(df, float(ref["prev_close"]), params)
        if not pats:
            continue
        bars = bars5m(df, float(ref["prev_close"]))
        members = by_sector.get(ref.get("sector", "OTHERS"), [sym])
        for p in pats:
            at = p["triggered_at"] or p["formed_at"]
            rows = as_of([p], at, {"sector_pct": sector_pct_at(frames, refs, members, at), "bars": bars})
            r = rows[0] if rows else None
            rec = {**p, "symbol": sym, "checks": r["checks"] if r else None, "score": r["score"] if r else None,
                   "outcome": grade_pattern(df, p)}
            rec.pop("facts", None); rec.pop("target_touched", None); rec.pop("formed_idx", None); rec.pop("trigger_idx", None)
            out.append(rec)
    return out


def upsert_patterns(cur, day, rows: list[dict], source: str, detector: str) -> int:
    if source == "replay":
        cur.execute("DELETE FROM patterns WHERE day=%s AND source='replay' AND detector<>%s", (day, detector))
    recs = [row_to_record(day, r["symbol"], r, source, detector) for r in rows]
    if not recs:
        return 0
    cur.executemany(UPSERT_SQL, recs)
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def fmt_table(summary: dict) -> str:
    def f(v, d=1, suf=""):
        return "—" if v is None else f"{v:.{d}f}{suf}"
    lines = ["| kind | triggers | forming only | hit rate | failed ≤3 bars | avg r30 | avg r60 | median min to target | score≥3 triggers / hit | score<3 triggers / hit |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for k, s in summary.items():
        hi, lo = s["by_score"][">=3"], s["by_score"]["<3"]
        lines.append(f"| {k} | {s['triggers']} | {s['forming_only']} | {f(s['hit_rate'],0,'%')} | {f(s['fail_rate'],0,'%')} | {f(s['avg_r30'],2,'%')} | "
                     f"{f(s['avg_r60'],2,'%')} | {f(s['median_tt_min'],0)} | {hi['triggers']} / {f(hi['hit_rate'],0,'%')} | {lo['triggers']} / {f(lo['hit_rate'],0,'%')} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--params", default="", help="JSON overriding DEFAULTS, e.g. '{"min_retrace": 0.008}'")
    args = ap.parse_args(argv)
    import psycopg
    from core.patterns import DEFAULTS, detector_hash
    params = {**DEFAULTS, **(json.loads(args.params) if args.params else {})}
    det = detector_hash(params)
    DB = os.environ["DATABASE_URL"]
    with psycopg.connect(DB) as con, con.cursor() as cur:
        cur.execute("SELECT DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::date AS d FROM candles_1m WHERE symbol='RELIANCE' ORDER BY d DESC LIMIT %s", (args.days,))
        days = [r[0] for r in cur.fetchall()][::-1]
    all_rows, per_day = [], []
    t0 = time.time()
    for day in days:
        with psycopg.connect(DB) as con:
            rows = patterns_for_day(con, day, params)
            n = 0
            if not args.no_write:
                with con.cursor() as cur:
                    n = upsert_patterns(cur, day, rows, "replay", det)
                con.commit()
        trig = sum(1 for r in rows if r.get("triggered_at"))
        per_day.append((day, len(rows), trig, n))
        print(f"{day}: {len(rows)} patterns, {trig} triggered, {n} rows inserted", flush=True)
        all_rows += rows
    summary = summarise(all_rows)
    table = fmt_table(summary)
    print(f"\ndetector {det} params {json.dumps(params)}  ·  {len(days)} days in {time.time() - t0:.0f}s\n")
    print(table)
    if args.note:
        with open(args.note, "w") as f:
            f.write(f"# Pattern backtest · {dt.datetime.now(IST):%Y-%m-%d %H:%M} IST\n\n")
            f.write(f"Detector `{det}`, params `{json.dumps(params)}`, {len(days)} trading days "
                    f"({days[0]} .. {days[-1]}), {len(all_rows)} patterns, rows {'written' if not args.no_write else 'not written'}.\n\n")
            f.write("Hit rate = target touched before stop, among graded triggers. Failed = closed back through the neckline "
                    "within 3 bars before the target. Returns are signed in the trade's favour.\n\n")
            f.write(table + "\n\n## Per day\n\n| day | patterns | triggered | inserted |\n|---|---|---|---|\n")
            for d, n, t, ins in per_day:
                f.write(f"| {d} | {n} | {t} | {ins} |\n")
        print(f"\nnote written: {args.note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
