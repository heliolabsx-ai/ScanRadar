"""
ScanRadar engine: every minute (on the ingest's 'candles:closed' signal)
  1. load today's 1-min candles for the universe
  2. ranking scanner -> Redis 'scan:latest' (JSON) + Postgres snapshot
  3. rule scans from /app/scans/*.json -> alerts (Telegram + Postgres) with cooldown
"""
import os, json, glob, time, html, logging, datetime as dt
import redis, psycopg, pandas as pd
from analytics import analyse, score
from rules import resample, resampler, evaluate, trade_levels, continuous, join_sessions, in_hours, hours_window, validate_scan, story, alert_text
from rules import LEAN, digest, strength, market_vote, market, apply_digest, digest_text
from notify import telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("engine")
R = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
DB = os.environ["DATABASE_URL"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TOP_N = 3


def record_delivery(ref: str, ok: bool, error: str | None, silent: bool, now: dt.datetime) -> None:
    """One `deliveries` row for a message the engine sent itself (own connection; a failure is logged, never raised)."""
    try:
        with psycopg.connect(DB) as con:
            con.execute("INSERT INTO deliveries(day, ref, channel, sent_at, ok, error, silent) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (now.astimezone(IST).date(), ref, "telegram", now, ok, error, silent))
    except Exception as e:  # noqa: BLE001
        log.warning("delivery record for %s failed: %s", ref, e)


def record_deliveries(refs: list[str], ok: bool, error: str | None, silent: bool, now: dt.datetime) -> None:
    """One `deliveries` row per ref for ONE message that covered them all (a digest); a failure is logged, never raised."""
    try:
        with psycopg.connect(DB) as con, con.cursor() as cur:
            cur.executemany("INSERT INTO deliveries(day, ref, channel, sent_at, ok, error, silent) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            [(now.astimezone(IST).date(), ref, "telegram", now, ok, error, silent) for ref in refs])
            con.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("delivery records for %d alerts failed: %s", len(refs), e)


def send_digest(group: str, items: list, now: dt.datetime) -> None:
    """One quiet Telegram message for every fire of a digest group this minute (never deferred: it carries no odds line),
    then one delivery row per alert. `items`: (scan, symbol, price, levels or None, alert id, payload digest or None)."""
    try:
        sides: dict = {}
        mk = label = None
        for sc, s, price, lv, _aid, meta in items:
            d = digest(sc) or {}
            side = sides.setdefault(sc["name"], {"side": sc.get("side"), "title": d.get("title") or sc["name"], "rows": []})
            side["rows"].append({"symbol": s, "price": price, "stop": lv["stop"] if lv else None, "target": lv["target"] if lv else None,
                                 "rank": (meta or {}).get("rank"), "star": bool((meta or {}).get("star"))})
            if mk is None and meta and meta.get("market"):
                mk, label = {"breadth": meta["breadth"], "side": meta["market"]}, (d.get("market") or {}).get("label")
        text = digest_text(group, now, mk, label, list(sides.values()))
    except Exception as e:  # noqa: BLE001  a summary still goes out, as a plain list of names
        log.warning("digest text of %s failed: %s", group, e)
        text = f"<b>{html.escape(group)} · {now:%H:%M}</b>\n" + ", ".join(html.escape(i[1]) for i in items)
    ok, err = telegram(text, silent=True)
    record_deliveries([f"alert:{i[4]}" for i in items], ok, err, True, now)


def defer(ref: str, text: str, now: dt.datetime, silent: bool = False) -> None:
    """Queue a Telegram message on `tg:defer:{day}` (never waits for the API); sent directly only if the push fails."""
    key = f"tg:defer:{now:%Y-%m-%d}"
    try:
        R.rpush(key, json.dumps({"ref": ref, "text": text, "ts": now.isoformat(), "silent": silent}))
    except Exception as e:  # noqa: BLE001
        log.warning("deferred telegram failed (%s); sending directly", e)
        ok, err = telegram(text, silent=silent)
        record_delivery(ref, ok, err, silent, now)
        return
    try:
        R.expire(key, 86400)
    except Exception as e:  # noqa: BLE001  queued already; a missing TTL never warrants a second send
        log.warning("deferred list TTL not set: %s", e)


def load_today() -> dict[str, pd.DataFrame]:
    today = dt.datetime.now(IST).date()
    with psycopg.connect(DB) as con:
        df = pd.read_sql("SELECT ts,symbol,open,high,low,close,volume,oi,delta FROM candles_1m "
                         "WHERE ts >= %s ORDER BY symbol, ts", con, params=(dt.datetime.combine(today, dt.time(9, 15), IST),))
    df["ts"] = pd.to_datetime(df.ts, utc=True).dt.tz_convert("Asia/Kolkata")     # one dtype whatever the driver returned
    frames = {s: g.drop(columns="symbol").reset_index(drop=True) for s, g in df.groupby("symbol")}
    # append the forming candle from Redis so [0] means "current"
    for s, g in frames.items():
        raw = R.get(f"live:{s}")
        if raw:
            v = json.loads(raw)
            live = pd.DataFrame([{"ts": pd.Timestamp(v["ts"]), "open": v["o"], "high": v["h"], "low": v["l"],
                                  "close": v["c"], "volume": v["v"], "oi": v["oi"], "delta": v["d"]}])
            live["ts"] = pd.to_datetime(live.ts, utc=True).dt.tz_convert("Asia/Kolkata")
            if g.empty or live.ts.iloc[0] > g.ts.iloc[-1]:
                frames[s] = pd.concat([g, live], ignore_index=True)
                frames[s]["ts"] = pd.to_datetime(frames[s].ts, utc=True).dt.tz_convert("Asia/Kolkata")   # concat must stay datetime, never object
    return frames


_PREV: dict = {}      # previous session's bars per timeframe, loaded once per day (continuous scans)


def prev_session_bars(tf: str) -> dict[str, pd.DataFrame]:
    """The last stored session's bars before today, resampled to `tf`, per symbol."""
    today = dt.datetime.now(IST).date()
    key = (today, tf)
    if key in _PREV:
        return _PREV[key]
    start = dt.datetime.combine(today, dt.time(0), IST)
    with psycopg.connect(DB) as con, con.cursor() as cur:
        cur.execute("SELECT max((ts AT TIME ZONE 'Asia/Kolkata')::date) FROM candles_1m WHERE symbol='RELIANCE' AND ts < %s", (start,))
        prev = cur.fetchone()[0]
        out: dict[str, pd.DataFrame] = {}
        if prev:
            df = pd.read_sql("SELECT ts,symbol,open,high,low,close,volume,oi,delta FROM candles_1m WHERE ts >= %s AND ts < %s ORDER BY symbol, ts",
                             con, params=(dt.datetime.combine(prev, dt.time(9, 15), IST), dt.datetime.combine(prev, dt.time(15, 31), IST)))
            df["ts"] = pd.to_datetime(df.ts, utc=True).dt.tz_convert("Asia/Kolkata")
            out = {s: resample(g.drop(columns="symbol").reset_index(drop=True), tf) for s, g in df.groupby("symbol")}
    _PREV.clear(); _PREV[key] = out
    log.info("previous session %s: %d symbols of %s bars for continuous scans", prev, len(out), tf)
    return out


def load_scans() -> list[dict]:
    out = []
    for p in sorted(glob.glob("/app/scans/*.json")):
        try:
            with open(p) as f:
                sc = json.load(f)
            validate_scan(sc)                 # a malformed hours block or min_rr drops this scan here, not every run
            sc["_file"] = os.path.basename(p); out.append(sc)
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
    R.set("scan:latest", json.dumps(snapshot, default=float), ex=1800)     # a snapshot older than 30 min is never served as live
    R.publish("scan:updated", snapshot["ts"])

    # ---- rule scans ----
    scans = load_scans()
    now = dt.datetime.now(IST)
    fired = []
    bars_of = resampler(frames)                 # each symbol resampled once per timeframe per run, not once per scan
    with psycopg.connect(DB) as con, con.cursor() as cur:
        cur.executemany("INSERT INTO scan_snapshots(ts,symbol,score,rank,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        [(now, a["symbol"], a["score"], a["rank"], json.dumps({k: a[k] for k in ("pct", "oi_pct", "oi_label", "rvol", "rr", "picked")}, default=float)) for a in rows])
        ctx_of = lambda ref: {"prev_day_close": ref.get("prev_close", 0), "prev_day_high": ref.get("prev_high", 0),
                              "prev_day_low": ref.get("prev_low", 0)}
        leans: dict = {}                        # digest group -> the market's lean this minute (None when it could not be read)
        for sc in scans:
            win = hours_window(sc)
            if not in_hours(sc, now, win):
                continue
            tf = sc.get("timeframe", "5m")
            cooldown = int(sc.get("alert", {}).get("cooldown_min", 30))
            prev = prev_session_bars(tf) if continuous(sc) else {}
            dg = digest(sc)
            hits = []                           # this scan's fires this minute: ranked together when it is a digest scan, then stored
            for s in frames:
                bars = join_sessions(prev.get(s), bars_of(s, tf))
                ctx = ctx_of(refs.get(s, {}))
                if evaluate(sc, bars, ctx):
                    key = f"cooldown:{sc['name']}:{s}"
                    if R.set(key, "1", ex=cooldown * 60, nx=True):
                        price = float(bars.close.iloc[-1])
                        payload = {"tf": tf, "side": sc.get("side")}
                        lv = trade_levels(sc, bars, ctx, price) if sc.get("side") else None
                        if lv:
                            payload.update(stop=lv["stop"], target=lv["target"], levels=lv["basis"])
                        st = story(sc, bars, ctx) if sc.get("story") else None
                        if st:
                            payload["story"] = st
                        hits.append({"symbol": s, "price": price, "lv": lv, "st": st, "payload": payload,
                                     "strength": strength(sc, bars, ctx) if dg else None})
            if dg and hits:
                try:
                    if dg["group"] not in leans:
                        votes = [market_vote(sc, join_sessions(prev.get(s), bars_of(s, tf)), ctx_of(refs.get(s, {}))) for s in frames]
                        leans[dg["group"]] = market(votes, float((dg.get("market") or {}).get("lean", LEAN)))
                    apply_digest(sc, hits, leans[dg["group"]])
                except Exception as e:  # noqa: BLE001  the alerts are stored and sent all the same, unranked
                    log.warning("digest ranking of %s failed: %s", sc["name"], e)
            for h in hits:
                cur.execute("INSERT INTO alerts(scan,symbol,price,payload) VALUES(%s,%s,%s,%s) RETURNING id",
                            (sc["name"], h["symbol"], h["price"], json.dumps(h["payload"], default=float)))
                fired.append((sc, h["symbol"], h["price"], h["lv"], h["st"], cur.fetchone()[0], h["payload"].get("digest")))
        con.commit()
    groups: dict = {}
    for sc, s, price, lv, st, aid, meta in fired:
        tg = sc.get("alert", {}).get("telegram", True)      # falsy: none; "silent": no buzz; "digest": one summary per group; else loud
        if tg == "digest":
            groups.setdefault((digest(sc) or {}).get("group") or sc["name"], []).append((sc, s, price, lv, aid, meta))
        elif tg:
            try:
                text = alert_text(sc, s, price, lv, st, now)
            except (KeyError, IndexError, TypeError, ValueError) as e:   # a bad story template keeps the plain text
                log.warning("story template of %s: %s", sc["name"], e)
                text = alert_text(sc, s, price, lv, None, now)
            if os.environ.get("TRADES_LIVE", "1") != "0":
                defer(f"alert:{aid}", text, now, silent=(tg == "silent"))  # the API's trades loop sends it with the odds line
            else:
                ok, err = telegram(text, silent=(tg == "silent"))
                record_delivery(f"alert:{aid}", ok, err, tg == "silent", now)
    for group, items in groups.items():
        try:
            send_digest(group, items, now)
        except Exception as e:  # noqa: BLE001  a summary that fails never stops the run
            log.warning("digest of %s failed: %s", group, e)
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
