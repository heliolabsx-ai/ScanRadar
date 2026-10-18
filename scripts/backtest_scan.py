#!/usr/bin/env python
"""Backtest one rule scan over stored days and print what it did.

    docker compose run --rm -T api python scripts/backtest_scan.py --scan "Decline below SMA13 short" --days 20

Uses the same replay + grading code as the dashboard (ensure_replay_alerts / ensure_outcomes),
so the numbers match the rail's per-scan stats, and the rows it writes make the scan's
history show up in the dashboard immediately. Idempotent.
"""
import os, sys, argparse, statistics, datetime as dt, time
for p in ("/app", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api")):
    if p not in sys.path:
        sys.path.insert(0, p)
import psycopg
import alerts as al
import replay as rp
from desk import pnl_for

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def stored_days(con, n: int) -> list[dt.date]:
    with con.cursor() as cur:
        cur.execute("SELECT DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::date AS d FROM candles_1m WHERE symbol='RELIANCE' ORDER BY d DESC LIMIT %s", (n,))
        return [r[0] for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True, help="scan name as in the JSON")
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    scans = al.scan_files()
    if a.scan not in {s["name"] for s, _ in scans}:
        sys.exit(f"no scan named {a.scan!r}; have {[s['name'] for s, _ in scans]}")
    now = dt.datetime.now(IST)
    with psycopg.connect(os.environ["DATABASE_URL"]) as con:
        days = [d for d in stored_days(con, a.days + 1) if rp.day_is_settled(d, now)][: a.days]
        t0 = time.time()
        for i, d in enumerate(days, 1):
            if al.replay_needed(con, d, scans) or al.outcomes_needed(con, d):
                frames, refs = rp.load_day(con, d), rp.load_refs(con, d)
                al.ensure_replay_alerts(con, d, frames, refs, scans, now)
                al.ensure_outcomes(con, d, frames, refs, scans, now)
            print(f"  {i}/{len(days)} {d} prepared ({time.time() - t0:.0f}s)", flush=True)
        with con.cursor() as cur:
            cur.execute("SELECT symbol, lot_size FROM instruments"); lots = {s: int(l or 0) for s, l in cur.fetchall()}
            cur.execute("SELECT ts, symbol, price, payload, source FROM alerts WHERE scan=%s AND ts>=%s ORDER BY ts",
                        (a.scan, dt.datetime.combine(min(days), dt.time(0), IST)))
            rows = [{"ts": ts.astimezone(IST), "symbol": s, "price": pr, "payload": p or {}, "source": src, "scan": a.scan} for ts, s, pr, p, src in cur.fetchall()]
    rows = al.merge_live_replay(rows)
    graded = [r for r in rows if (r["payload"].get("outcome") or {}).get("hit")]
    hits = {k: sum(1 for r in graded if r["payload"]["outcome"]["hit"] == k) for k in ("target", "stop", "none")}
    decided = hits["target"] + hits["stop"]
    rs = {k: [float(r["payload"]["outcome"][k]) for r in graded if r["payload"]["outcome"].get(k) is not None] for k in ("r15", "r30", "r60")}
    pnl = []
    for r in graded:
        p = r["payload"]
        if p.get("stop") is not None and p.get("target") is not None:
            v = pnl_for(p["outcome"], p.get("side"), r["price"], p["stop"], p["target"], lots.get(r["symbol"], 0))
            if v is not None:
                pnl.append(v)
    print(f"\n{a.scan} — {len(days)} sessions {days[-1]} .. {days[0]}")
    print(f"fires: {len(rows)} ({len(rows) / max(1, len(days)):.1f} per day), graded {len(graded)}")
    if decided:
        print(f"target first: {hits['target']} ({hits['target'] / decided * 100:.0f}%)   stop first: {hits['stop']}   neither by close: {hits['none']}")
    for k, v in rs.items():
        if v:
            print(f"{k}: mean {statistics.mean(v):+.2f}%  median {statistics.median(v):+.2f}%  (favourable = positive)")
    if pnl:
        print(f"one lot per fire, before costs: total ₹{sum(pnl):,.0f}  mean ₹{statistics.mean(pnl):,.0f}  worst ₹{min(pnl):,.0f}  best ₹{max(pnl):,.0f}")
    by_hour: dict = {}
    for r in graded:
        h = by_hour.setdefault(r["ts"].strftime("%H"), [0, 0, []])
        h[0] += 1; h[1] += r["payload"]["outcome"]["hit"] == "target"
        if r["payload"]["outcome"].get("r30") is not None:
            h[2].append(float(r["payload"]["outcome"]["r30"]))
    print("by hour:  " + "  ".join(f"{k}h {n} fires {t / n * 100:.0f}% target {statistics.mean(v) if v else 0:+.2f}% @30m" for k, (n, t, v) in sorted(by_hour.items())))
    freq: dict = {}
    for r in rows:
        freq[r["symbol"]] = freq.get(r["symbol"], 0) + 1
    print("most frequent: " + ", ".join(f"{s} {n}" for s, n in sorted(freq.items(), key=lambda kv: -kv[1])[:8]))


if __name__ == "__main__":
    main()
