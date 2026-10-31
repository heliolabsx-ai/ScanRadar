#!/usr/bin/env python
"""Calibrate the Time Machine against graded outcomes: for every trigger of the stored days,
compare the predicted chance of the target with how often the trade really hit it.

    docker compose run --rm -T api python scripts/backtest_odds.py --days 42 --note /app/notes/odds-calibration-2026-10-31.md
    docker compose run --rm -T api python scripts/backtest_odds.py --days 42 --write      # also persist the rows

Runs inside the api image. Default is a dry run: rows are built in memory and graded with
core.gate.settle. With --write the rows are stored by trades.backfill_day and read back.
Both modes need migration 005 (the settings are read from trade_settings). The pure helpers
calibration() and fmt_note() need no database.
"""
import os, sys, argparse, datetime as dt, time

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "api"), "/app"):
    if p not in sys.path:
        sys.path.insert(0, p)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BUCKETS = [f"{i}-{i + 10}" for i in range(0, 100, 10)]


def _bucket(pct: float) -> str:
    return BUCKETS[min(max(int(pct // 10), 0), 9)]


def _hit(row: dict) -> str:
    return ((row.get("outcome") or {}).get("hit")) or "none"


def calibration(rows: list[dict]) -> dict:
    """Predicted target chance against the realised share, per decile and per verdict word.
    Rows with odds but no outcome are `ungraded` and left out of the verdict table."""
    groups: dict[str, list[dict]] = {b: [] for b in BUCKETS}
    words: dict[str, list[dict]] = {}
    no_odds = ungraded = 0
    for r in rows:
        odds = r.get("odds")
        if not odds:
            no_odds += 1
            continue
        groups[_bucket(float(odds["mc"]["target_pct"]))].append(r)
        if r.get("outcome") is None:
            ungraded += 1
            continue
        words.setdefault(odds["verdict"], []).append(r)
    deciles = []
    for b in BUCKETS:
        g = groups[b]
        decided = [r for r in g if _hit(r) in ("target", "stop")]
        deciles.append({"bucket": b, "n": len(g),
                        "predicted": round(sum(r["odds"]["mc"]["target_pct"] for r in g) / len(g), 1) if g else None,
                        "realised": round(100 * sum(_hit(r) == "target" for r in decided) / len(decided), 1) if decided else None})
    by_verdict = {}
    for w, g in words.items():
        share = lambda h: round(100 * sum(_hit(r) == h for r in g) / len(g), 1)
        by_verdict[w] = {"n": len(g), "target_pct": share("target"), "stop_pct": share("stop"), "none_pct": share("none")}
    return {"deciles": deciles, "by_verdict": by_verdict, "no_odds": no_odds, "ungraded": ungraded}


def fmt_note(cal: dict, days: int, n_rows: int) -> str:
    f = lambda v: "—" if v is None else f"{v:.1f}"
    out = [f"# Time Machine calibration · {dt.datetime.now(IST):%Y-%m-%d %H:%M} IST", "",
           f"{days} trading days, {n_rows} trigger rows ({cal.get('no_odds', 0)} without odds, left out of both tables; "
           f"{cal.get('ungraded', 0)} ungraded, left out of the verdict table). "
           "Realised = share of rows that hit the target among those that hit target or stop; the rest ran to the flat-by time.", "",
           "## Predicted against realised target chance", "",
           "| predicted bucket % | rows | predicted % | realised % |", "|---|---|---|---|"]
    out += [f"| {d['bucket']} | {d['n']} | {f(d['predicted'])} | {f(d['realised'])} |" for d in cal["deciles"]]
    out += ["", "## By verdict", "", "| verdict | rows | target % | stop % | neither % |", "|---|---|---|---|---|"]
    out += [f"| {w} | {v['n']} | {f(v['target_pct'])} | {f(v['stop_pct'])} | {f(v['none_pct'])} |" for w, v in sorted(cal["by_verdict"].items())]
    return "\n".join(out) + "\n"


def _graded_rows(con, day, settings, pat_stats, scans, trades, load_day) -> list[dict]:
    """Rows of the day built in memory (odds only, no gate) and graded on the day's candles."""
    from replay import session_bounds
    from core.gate import settle
    _, end = session_bounds(day)
    frames = load_day(con, day)
    rows = []
    for cand in trades.candidates(con, day, end, include_replay=True, max_age=None, pat_stats=pat_stats, scans=scans):
        row = trades.build_row(cand, settings, {}, trades.load_history(con, cand["symbol"], day), apply_rules=False)
        df = frames.get(row["symbol"])
        if df is not None:
            row["outcome"] = settle({"ts": cand["ts"].isoformat(), "side": row["side"], "entry": row["entry"], "stop": row["stop"],
                                     "target": row["target"], "size": row["size"]}, df, settings["flat_by"], final=True)
        rows.append(row)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=42)
    ap.add_argument("--note", default="")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="persist the rows via backfill_day")
    mode.add_argument("--no-write", action="store_true", help="dry run, the default")
    args = ap.parse_args(argv)
    import psycopg
    import trades
    import alerts as al
    import live_desk
    from replay import load_day
    DB = os.environ["DATABASE_URL"]
    today = dt.datetime.now(IST).date()
    with psycopg.connect(DB) as con, con.cursor() as cur:
        cur.execute("SELECT DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::date AS d FROM candles_1m WHERE symbol='RELIANCE' ORDER BY d DESC LIMIT %s", (args.days + 1,))
        days = [r[0] for r in cur.fetchall() if r[0] < today][: args.days][::-1]
    if not days:
        print("no stored days older than today")
        return 1
    import patterns as pt
    from core.patterns import detector_hash
    all_rows, t0 = [], time.time()
    for day in days:
        try:
            with psycopg.connect(DB) as con:
                if pt.patterns_needed(con, day, detector_hash()):
                    print(f"warning: {day} has no stored pattern rows under this detector (or ungraded ones); only its alert rows count. "
                          f"Run scripts/backtest_patterns.py first.", file=sys.stderr, flush=True)
                settings = trades.get_settings(con)
                if args.write:
                    n = trades.backfill_day(con, day, settings)
                    rows = trades.rows_for_day(con, day)
                    print(f"{day}: {len(rows)} rows, {n} inserted", flush=True)
                else:
                    pat_stats, _ = live_desk.setup_stats(con, dt.datetime.now(IST))
                    scans = {s["name"]: s for s, _ in al.scan_files()}
                    rows = _graded_rows(con, day, settings, pat_stats, scans, trades, load_day)
                    print(f"{day}: {len(rows)} rows", flush=True)
        except psycopg.errors.UndefinedTable as e:
            print(f"trade_rows or trade_settings is missing ({e}); apply migration 005 (dry runs need it too: the settings are read from trade_settings)", file=sys.stderr)
            return 2
        all_rows += rows
    note = fmt_note(calibration(all_rows), len(days), len(all_rows))
    print(f"\n{len(days)} days in {time.time() - t0:.0f}s\n\n{note}")
    if args.note:
        with open(args.note, "w") as f:
            f.write(note)
        print(f"note written: {args.note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
