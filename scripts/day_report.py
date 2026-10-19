#!/usr/bin/env python
"""Facts for the end-of-day trader review of one session (run after 15:35):

    docker compose run --rm -T api python scripts/day_report.py 2026-10-19

Prepares the day (replay alerts, outcomes, patterns, timeline), then prints: data health,
rule alerts per scan with outcomes and one-lot P&L, pattern triggers per kind with outcomes,
fires per hour, the desk's verdicts against what happened, and the symbols that fired most.
"""
import os, sys, json, statistics, datetime as dt
for p in ("/app", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api")):
    if p not in sys.path:
        sys.path.insert(0, p)
import psycopg
import alerts as al, patterns as pt, desk, live_desk as ld, replay as rp
from core.patterns import detector_hash, LABELS
from main import _prepare_day

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def pct(a, b):
    return f"{a / b * 100:.0f}%" if b else "—"


def main():
    day = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.datetime.now(IST).date()
    _prepare_day(day)
    with psycopg.connect(os.environ["DATABASE_URL"]) as con:
        lots = ld.lots_map(con)
        with con.cursor() as cur:
            cur.execute("SELECT count(DISTINCT ts), count(DISTINCT symbol), count(*) FILTER (WHERE oi>0), count(*) FROM candles_1m WHERE ts>=%s AND ts<%s", rp.session_bounds(day))
            n_min, n_sym, n_oi, n_all = cur.fetchone()
            rows = desk.rows_for_day(cur, day)
        print(f"# {day}  candles: {n_min} minutes x {n_sym} symbols, OI on {pct(n_oi, n_all)} of candles")
        # rule alerts
        als = al.alerts_for_day(con, day)
        by_scan: dict = {}
        for a in als:
            t = by_scan.setdefault(a["scan"], {"fires": 0, "live": 0, "target": 0, "stop": 0, "none": 0, "r30": [], "pnl": 0.0, "hours": {}})
            t["fires"] += 1; t["live"] += a["source"] == "live"
            o = a.get("outcome") or {}
            t[o.get("hit") or "none"] += 1
            if o.get("r30") is not None:
                t["r30"].append(float(o["r30"]))
            if a.get("stop") is not None and a.get("target") is not None and a.get("side"):
                v = desk.pnl_for(o, a["side"], a["price"], a["stop"], a["target"], lots.get(a["symbol"], 0))
                t["pnl"] += v or 0
            h = a["ts"][11:13]; t["hours"][h] = t["hours"].get(h, 0) + 1
        print("\n## Rule alerts (live + replay merged; P&L one lot, before costs)")
        for name, t in by_scan.items():
            dec = t["target"] + t["stop"]
            print(f"- {name}: {t['fires']} fires ({t['live']} live) · target-first {pct(t['target'], dec)} of {dec} decided · open {t['none']} · "
                  f"r30 mean {statistics.mean(t['r30']) if t['r30'] else 0:+.2f}% · P&L ₹{t['pnl']:,.0f} · by hour {dict(sorted(t['hours'].items()))}")
        # patterns
        stored = [r for r in pt.stored_rows(con, day) if not r.get("superseded_at")]
        trig = [r for r in stored if r.get("triggered_at")]
        print(f"\n## Patterns: {len(stored)} rows, {len(trig)} triggered, sources {sorted({r['source'] for r in stored})}, detector {detector_hash()[:6]}")
        for kind in sorted({r["kind"] for r in trig}):
            rs = [r for r in trig if r["kind"] == kind]
            hits = sum(1 for r in rs if (r.get("outcome") or {}).get("hit") == "target"); stops = sum(1 for r in rs if (r.get("outcome") or {}).get("hit") == "stop")
            pnl = sum((desk.pnl_for(r.get("outcome"), r["side"], r["entry"], r["stop"], r["target"], lots.get(r["symbol"], 0)) or 0) for r in rs)
            hi = [r for r in rs if (r.get("score") or 0) >= 5]
            hhits = sum(1 for r in hi if (r.get("outcome") or {}).get("hit") == "target"); hdec = sum(1 for r in hi if (r.get("outcome") or {}).get("hit") in ("target", "stop"))
            print(f"- {LABELS.get(kind, kind)}: {len(rs)} triggers · target-first {pct(hits, hits + stops)} of {hits + stops} · score>=5 {pct(hhits, hdec)} of {hdec} · "
                  f"failed<=3 bars {sum(1 for r in rs if r.get('failed_at'))} · P&L one lot ₹{pnl:,.0f}")
        best = sorted([r for r in trig if r.get("outcome")], key=lambda r: -(desk.pnl_for(r["outcome"], r["side"], r["entry"], r["stop"], r["target"], lots.get(r["symbol"], 0)) or 0))
        print("  best: " + "; ".join(f"{r['symbol']} {r['kind']} {str(r['triggered_at'])[11:16]} {r['outcome'].get('hit')}" for r in best[:5]))
        print("  worst: " + "; ".join(f"{r['symbol']} {r['kind']} {str(r['triggered_at'])[11:16]} {r['outcome'].get('hit')}" for r in best[-5:]))
        # desk
        verdicts = [r for r in rows if r["kind"] == "verdict"]
        judged = [r for r in verdicts if r.get("output")]
        ld.verdict_outcomes(con, day, judged)
        print(f"\n## Desk: {len(judged)} verdicts, {sum(1 for r in verdicts if (r.get('error') or '').startswith(('skipped', 'stale')))} skipped, "
              f"{sum(1 for r in verdicts if r.get('error') and not (r['error'] or '').startswith(('skipped', 'stale')))} failed, spend ₹{sum(r.get('cost_inr') or 0 for r in rows):.0f}")
        for call in ("take", "wait", "skip"):
            rs = [r for r in judged if r["output"]["call"] == call]
            hits = sum(1 for r in rs if (r.get("outcome") or {}).get("hit") == "target"); stops = sum(1 for r in rs if (r.get("outcome") or {}).get("hit") == "stop")
            pnl = sum((desk.pnl_for(r.get("outcome"), r["context"]["side"], r["context"]["ticket"]["entry"], r["context"]["ticket"]["stop"], r["context"]["ticket"]["target"], lots.get(r["symbol"], 0)) or 0) for r in rs)
            print(f"- {call.upper()}: {len(rs)} · target-first {pct(hits, hits + stops)} of {hits + stops} · P&L one lot if all taken ₹{pnl:,.0f} · "
                  f"avg confidence {statistics.mean([r['output']['confidence'] for r in rs]) if rs else 0:.1f}")
        skips = {}
        for r in verdicts:
            e = (r.get("error") or "")
            if e.startswith(("skipped", "stale")):
                k = e.split(":")[1].strip()[:40]; skips[k] = skips.get(k, 0) + 1
        print("  skip reasons: " + json.dumps(skips))
        briefs = [r for r in rows if r["kind"] == "brief"]
        print(f"  briefs: {sum(1 for b in briefs if b.get('output'))} ok, {sum(1 for b in briefs if b.get('error'))} failed; review: {'yes' if any(r['kind'] == 'review' and r.get('output') for r in rows) else 'no'}")
        # most frequent symbols across alerts
        freq: dict = {}
        for a in als:
            freq[a["symbol"]] = freq.get(a["symbol"], 0) + 1
        print("\n## Most alerted symbols: " + ", ".join(f"{s} {n}" for s, n in sorted(freq.items(), key=lambda kv: -kv[1])[:10]))


if __name__ == "__main__":
    main()
