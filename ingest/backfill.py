"""
Historical backfill: 1-minute candles (equity OHLCV + futures OI) and daily
candles for the whole F&O universe, then rebuild daily_ref. Gap-fill only:
1-minute inserts never overwrite live-captured rows, daily OI is write-once.

    docker compose run --rm ingest python backfill.py                  # 60 calendar days of minutes, 400 of dailies
    docker compose run --rm ingest python backfill.py --days 3         # evening top-up
    docker compose run --rm ingest python backfill.py --symbols RELIANCE,TCS

Never logs in: uses the access token already cached in Redis (preflight / 08:55 login).
"""
import os, sys, argparse, datetime as dt, logging, time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MINUTE_CHUNK_DAYS = 59          # Kite serves at most 60 days of minute candles per request
SESSION_SETTLED = dt.time(15, 35)

INSERT_1M = """INSERT INTO candles_1m(ts,symbol,open,high,low,close,volume,oi,delta)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING"""
UPSERT_1D = """INSERT INTO candles_1d(day,symbol,open,high,low,close,volume,oi) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (symbol,day) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
               close=EXCLUDED.close, volume=EXCLUDED.volume,
               oi=COALESCE(NULLIF(candles_1d.oi,0), EXCLUDED.oi)"""
UPSERT_INSTRUMENTS = """INSERT INTO instruments(symbol,eq_token,fut_token,fut_symbol,expiry,lot_size,sector)
               VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(symbol) DO UPDATE SET eq_token=EXCLUDED.eq_token,
               fut_token=EXCLUDED.fut_token,fut_symbol=EXCLUDED.fut_symbol,expiry=EXCLUDED.expiry,
               lot_size=EXCLUDED.lot_size,sector=EXCLUDED.sector,updated_at=now()"""
REBUILD_DAILY_REF_SQL = """
INSERT INTO daily_ref(day, symbol, prev_close, prev_high, prev_low, prev_oi, avg_vol_1m)
SELECT day, symbol, prev_close, prev_high, prev_low, COALESCE(prev_oi, 0), avg_vol_1m FROM (
  SELECT day, symbol,
         lag(close) OVER w AS prev_close, lag(high) OVER w AS prev_high, lag(low) OVER w AS prev_low,
         lag(oi) OVER w AS prev_oi,
         avg(volume) OVER (PARTITION BY symbol ORDER BY day ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) / 375 AS avg_vol_1m
  FROM candles_1d
  WINDOW w AS (PARTITION BY symbol ORDER BY day)
) x
WHERE prev_close IS NOT NULL AND day >= %s
ON CONFLICT (day, symbol) DO UPDATE SET prev_close=EXCLUDED.prev_close, prev_high=EXCLUDED.prev_high,
  prev_low=EXCLUDED.prev_low, prev_oi=EXCLUDED.prev_oi, avg_vol_1m=EXCLUDED.avg_vol_1m
"""


def fetch_window(now: dt.datetime, days: int) -> tuple[dt.date, dt.date]:
    """(frm, to). While a weekday session is still open (< 15:35 IST) `to` is yesterday,
    so backfill never writes today's partial minutes ahead of the live ingest."""
    to = now.date()
    if now.weekday() < 5 and now.timetz().replace(tzinfo=None) < SESSION_SETTLED:
        to = to - dt.timedelta(days=1)
    return to - dt.timedelta(days=days), to


def merge_oi(eq: list[dict], fut: list[dict]) -> list[tuple]:
    """Equity minute bars + futures OI by timestamp, forward-filled within a calendar
    day (0 before the first futures bar of the day). Rows match candles_1m columns
    minus symbol; delta is 0 (no ticks in historical data)."""
    oi_at = {b["date"]: int(b.get("oi") or 0) for b in fut}
    rows, cur, cur_day = [], 0, None
    for b in eq:
        d = b["date"].date()
        if d != cur_day:
            cur_day, cur = d, 0
        if oi_at.get(b["date"]):
            cur = oi_at[b["date"]]
        rows.append((b["date"], b["open"], b["high"], b["low"], b["close"], int(b["volume"]), cur, 0))
    return rows


def _chunks(frm: dt.date, to: dt.date, span: int):
    a = frm
    while a <= to:
        b = min(a + dt.timedelta(days=span), to)
        yield a, b
        a = b + dt.timedelta(days=1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="calendar days of 1-minute history")
    ap.add_argument("--daily-days", type=int, default=400, help="calendar days of daily history")
    ap.add_argument("--symbols", default="", help="comma-separated subset")
    ap.add_argument("--universe-only", action="store_true", help="only (re)write the instruments table from Kite's dump")
    args = ap.parse_args(argv)

    import redis, psycopg
    from kiteconnect import KiteConnect
    from hist import hist
    R = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    DB = os.environ["DATABASE_URL"]

    token = R.get("kite:access_token")
    if not token:
        print("no valid Kite token cached; run the preflight (or wait for the 08:55 login) and retry", file=sys.stderr)
        return 2
    kite = KiteConnect(api_key=os.environ["KITE_API_KEY"])
    kite.set_access_token(token)
    try:
        kite.profile()
    except Exception as e:  # noqa: BLE001
        print(f"cached Kite token rejected ({e}); run the preflight (or wait for the 08:55 login) and retry", file=sys.stderr)
        return 2

    now = dt.datetime.now(IST)
    frm, to = fetch_window(now, args.days)
    dfrm = to - dt.timedelta(days=args.daily_days)
    with psycopg.connect(DB) as con, con.cursor() as cur:
        cur.execute("SELECT symbol, eq_token, fut_token FROM instruments ORDER BY symbol")
        uni = cur.fetchall()
    if not uni or args.universe_only:
        # the instruments table is normally written by the 08:55 ingest; before the first
        # live day it is empty, and replay needs sector + lot_size from it
        import universe
        u = universe.build(kite)
        with psycopg.connect(DB) as con, con.cursor() as cur:
            cur.executemany(UPSERT_INSTRUMENTS, [(r.symbol, int(r.eq_token), int(r.fut_token), r.fut_symbol, r.expiry,
                                                  int(r.lot_size), r.sector) for r in u.itertuples()])
            con.commit()
        log.info("instruments table written: %d symbols", len(u))
        if args.universe_only:
            return 0
        uni = [(r.symbol, int(r.eq_token), int(r.fut_token)) for r in u.itertuples()]
    if args.symbols:
        want = set(args.symbols.upper().split(","))
        uni = [r for r in uni if r[0] in want]
    log.info("backfill: %d symbols, minutes %s..%s, dailies %s..%s", len(uni), frm, to, dfrm, to)

    t0 = time.time()
    inserted = failures = 0
    with psycopg.connect(DB) as con, con.cursor() as cur:
        for sym, eq_tok, fut_tok in uni:
            try:
                eq_min, fut_min = [], []
                for a, b in _chunks(frm, to, MINUTE_CHUNK_DAYS):
                    eq_min += hist(kite, eq_tok, a, b, "minute")
                    fut_min += hist(kite, fut_tok, a, b, "minute", oi=True)
                rows = [(ts, sym, o, h, l, c, v, oi, d) for ts, o, h, l, c, v, oi, d in merge_oi(eq_min, fut_min)]
                cur.executemany(INSERT_1M, rows)
                n_ins = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
                eq_day = hist(kite, eq_tok, dfrm, to, "day")
                fut_day = {b["date"].date(): int(b.get("oi") or 0) for b in hist(kite, fut_tok, dfrm, to, "day", oi=True)}
                cur.executemany(UPSERT_1D, [(b["date"].date(), sym, b["open"], b["high"], b["low"], b["close"],
                                             int(b["volume"]), fut_day.get(b["date"].date(), 0)) for b in eq_day])
                con.commit()
                inserted += n_ins
                log.info("%s: +%d 1m rows (%d existed), %d daily", sym, n_ins, len(rows) - n_ins, len(eq_day))
            except Exception as e:  # noqa: BLE001
                con.rollback()
                failures += 1
                log.warning("%s failed: %s", sym, e)
        cur.execute(REBUILD_DAILY_REF_SQL, (dfrm,))
        con.commit()
        cur.execute("SELECT count(DISTINCT day) FROM candles_1d WHERE symbol='RELIANCE' AND day BETWEEN %s AND %s", (frm, to))
        tdays = cur.fetchone()[0]
    log.info("done: %d symbols, %d trading days in the minute window, %d 1m rows inserted, %d failures, %.0fs",
             len(uni), tdays, inserted, failures, time.time() - t0)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
