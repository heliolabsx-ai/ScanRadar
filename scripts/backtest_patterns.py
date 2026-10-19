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
from core.patterns import summarise                       # noqa: E402  (re-exported for tests)
from patterns import UPSERT_SQL, row_to_record, patterns_for_day, upsert_patterns   # noqa: E402,F401  api/patterns.py


def fmt_table(summary: dict) -> str:
    def f(v, d=1, suf=""):
        return "—" if v is None else f"{v:.{d}f}{suf}"
    lines = ["| kind | triggers | forming only | hit rate | failed ≤3 bars | avg r30 | avg r60 | median min to target | score≥5 triggers / hit | score<5 triggers / hit | breakout vol ✓ / hit | breakout vol ✗ / hit |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, s in summary.items():
        hi, lo = s["by_score"][">=5"], s["by_score"]["<5"]
        w, wo = s["by_volume"]["with"], s["by_volume"]["without"]
        lines.append(f"| {k} | {s['triggers']} | {s['forming_only']} | {f(s['hit_rate'],0,'%')} | {f(s['fail_rate'],0,'%')} | {f(s['avg_r30'],2,'%')} | "
                     f"{f(s['avg_r60'],2,'%')} | {f(s['median_tt_min'],0)} | {hi['triggers']} / {f(hi['hit_rate'],0,'%')} | {lo['triggers']} / {f(lo['hit_rate'],0,'%')} | "
                     f"{w['triggers']} / {f(w['hit_rate'],0,'%')} | {wo['triggers']} / {f(wo['hit_rate'],0,'%')} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--params", default="", help="JSON overriding DEFAULTS, for example {\"min_retrace\": 0.008}")
    args = ap.parse_args(argv)
    import psycopg
    from core.patterns import DEFAULTS, detector_hash
    params = {**DEFAULTS, **(json.loads(args.params) if args.params else {})}
    det = detector_hash(params)
    DB = os.environ["DATABASE_URL"]
    with psycopg.connect(DB) as con, con.cursor() as cur:
        cur.execute("SELECT DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::date AS d FROM candles_1m WHERE symbol='RELIANCE' ORDER BY d DESC LIMIT %s", (args.days + 1,))
        from replay import day_is_settled
        now = dt.datetime.now(IST)
        days = [r[0] for r in cur.fetchall() if day_is_settled(r[0], now)][: args.days][::-1]
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
