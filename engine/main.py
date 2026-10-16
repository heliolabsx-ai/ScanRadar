"""
ScanRadar engine: every minute (on the ingest's 'candles:closed' signal)
  1. load today's 1-min candles for the universe
  2. ranking scanner -> Redis 'scan:latest' (JSON) + Postgres snapshot
  3. rule scans from /app/scans/*.json -> alerts (Telegram + Postgres) with cooldown
"""
import os, json, glob, time, logging, datetime as dt
import redis, psycopg, pandas as pd
from analytics import analyse, score
from rules import resample, evaluate
from notify import telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("engine")
R = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
DB = os.environ["DATABASE_URL"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TOP_N = 3


def load_today() -> dict[str, pd.DataFrame]:
    today = dt.datetime.now(IST).date()
    with psycopg.connect(DB) as con:
        df = pd.read_sql("SELECT ts,symbol,open,high,low,close,volume,oi,delta FROM candles_1m "
                         "WHERE ts >= %s ORDER BY symbol, ts", con, params=(dt.datetime.combine(today, dt.time(9, 15), IST),))
    frames = {s: g.drop(columns="symbol").reset_index(drop=True) for s, g in df.groupby("symbol")}
    # append the forming candle from Redis so [0] means "current"
    for s, g in frames.items():
        raw = R.get(f"live:{s}")
        if raw:
            v = json.loads(raw)
            live = pd.DataFrame([{"ts": pd.Timestamp(v["ts"]), "open": v["o"], "high": v["h"], "low": v["l"],
                                  "close": v["c"], "volume": v["v"], "oi": v["oi"], "delta": v["d"]}])
            if g.empty or live.ts.iloc[0] > g.ts.iloc[-1]:
                frames[s] = pd.concat([g, live], ignore_index=True)
    return frames


def load_scans() -> list[dict]:
    out = []
    for p in sorted(glob.glob("/app/scans/*.json")):
        try:
            with open(p) as f:
                sc = json.load(f); sc["_file"] = os.path.basename(p); out.append(sc)
        except Exception as e:  # noqa
            log.warning("bad scan %s: %s", p, e)
    return out


def run_once():
    frames = load_today()
    refs = {s: R.hgetall(f"ref:{s}") for s in frames}
    rows = []
    for s, df in frames.items():
        a = analyse(s, df, refs.get(s, {}))
        if a:
            rows.append(a)
    if not rows:
        return
    # sector averages + scores
    sec = {}
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
    bulls = [a for a in rows if a["dir"] > 0]; bears = [a for a in rows if a["dir"] < 0]
    for lst in (bulls, bears):
        for i, a in enumerate(lst):
            a["side_rank"] = i + 1; a["picked"] = i < TOP_N and a["rr"] >= 1.5
    snapshot = {"ts": dt.datetime.now(IST).isoformat(), "sectors": sec_avg, "n_bull": len(bulls), "n_bear": len(bears),
                "bulls": bulls[:40], "bears": bears[:40]}
    R.set("scan:latest", json.dumps(snapshot, default=float))
    R.publish("scan:updated", snapshot["ts"])

    # ---- rule scans ----
    scans = load_scans()
    now = dt.datetime.now(IST)
    fired = []
    with psycopg.connect(DB) as con, con.cursor() as cur:
        cur.executemany("INSERT INTO scan_snapshots(ts,symbol,score,rank,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        [(now, a["symbol"], a["score"], a["rank"], json.dumps({k: a[k] for k in ("pct", "oi_pct", "oi_label", "rvol", "rr", "picked")}, default=float)) for a in rows])
        for sc in scans:
            tf = sc.get("timeframe", "5m")
            cooldown = int(sc.get("alert", {}).get("cooldown_min", 30))
            for s, df in frames.items():
                bars = resample(df, tf)
                ref = refs.get(s, {})
                ctx = {"prev_day_close": ref.get("prev_close", 0), "prev_day_high": ref.get("prev_high", 0),
                       "prev_day_low": ref.get("prev_low", 0)}
                if evaluate(sc, bars, ctx):
                    key = f"cooldown:{sc['name']}:{s}"
                    if R.set(key, "1", ex=cooldown * 60, nx=True):
                        price = float(bars.close.iloc[-1])
                        cur.execute("INSERT INTO alerts(scan,symbol,price,payload) VALUES(%s,%s,%s,%s)",
                                    (sc["name"], s, price, json.dumps({"tf": tf})))
                        fired.append((sc, s, price))
        con.commit()
    for sc, s, price in fired:
        if sc.get("alert", {}).get("telegram", True):
            telegram(f"<b>{sc['name']}</b>\n{s} @ ₹{price:,.2f}  ({sc.get('timeframe','5m')})\n{now:%H:%M}  ·  scanradar.tech")
    log.info("scan: %d symbols, %d bull / %d bear, %d alerts", n, len(bulls), len(bears), len(fired))


def main():
    ps = R.pubsub(); ps.subscribe("candles:closed")
    log.info("engine waiting for candle signals")
    last = 0
    for msg in ps.listen():
        if msg["type"] != "message":
            continue
        if time.time() - last < 20:   # debounce
            continue
        last = time.time()
        try:
            run_once()
        except Exception as e:  # noqa
            log.exception("run failed: %s", e)


if __name__ == "__main__":
    main()
