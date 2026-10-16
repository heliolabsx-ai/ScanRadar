"""
ScanRadar ingest:
  08:55  login (TOTP), build universe, load previous-day references
  09:14  open WebSocket, stream equity (QUOTE) + futures (FULL, for OI)
  every minute: flush closed candles to Postgres, publish live state to Redis
  15:31  stop
"""
import os, json, time, logging, datetime as dt, threading
import redis, psycopg, schedule
from kiteconnect import KiteTicker
from kiteconnect.exceptions import NetworkException

import kite_auth, universe
from candles import SymbolState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")

R = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
DB = os.environ["DATABASE_URL"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MARKET_OPEN, MARKET_CLOSE = dt.time(9, 15), dt.time(15, 30)
HIST_PACE = 0.4   # seconds between historical calls; Kite allows 3 req/s per API key

state: dict[int, SymbolState] = {}   # token -> state (both eq and fut tokens map to the same object)
symbols: dict[str, SymbolState] = {}
kite = None
ticker = None
lock = threading.Lock()


# ---------------- daily setup ----------------
def _hist(token, frm, to, interval, oi=False, tries=4):
    """historical_data with pacing and back-off. Kite answers HTTP 429 with a JSON
    NetworkException("Too many requests") when the 3 req/s budget is exceeded."""
    for attempt in range(1, tries + 1):
        try:
            out = kite.historical_data(int(token), frm, to, interval, oi=oi)
            time.sleep(HIST_PACE)
            return out
        except NetworkException as e:
            if attempt == tries:
                raise
            log.warning("historical %s throttled (%s); retry %d in %ds", token, e, attempt, 2 * attempt)
            time.sleep(2 * attempt)


def load_daily_refs(u):
    """Previous day's close/high/low for equity, previous OI for futures, avg 1-min volume (20d)."""
    today = dt.date.today()
    frm, to = today - dt.timedelta(days=35), today - dt.timedelta(days=1)
    ok = missing_oi = 0
    with psycopg.connect(DB) as con, con.cursor() as cur:
        for row in u.itertuples():
            try:
                d = _hist(row.eq_token, frm, to, "day")
                f = _hist(row.fut_token, frm, to, "day", oi=True)
                if not d:
                    log.warning("ref load: no daily candles for %s", row.symbol)
                    continue
                last = d[-1]
                # 'oi' key exists only when Kite returned the 7-column form; don't lose the equity refs over it
                prev_oi = f[-1].get("oi", 0) if f else 0
                if not prev_oi:
                    missing_oi += 1
                avg_vol_1m = sum(x["volume"] for x in d[-20:]) / max(1, len(d[-20:])) / 375
                cur.execute("""INSERT INTO daily_ref(day,symbol,prev_close,prev_high,prev_low,prev_oi,avg_vol_1m)
                               VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(day,symbol) DO UPDATE SET
                               prev_close=EXCLUDED.prev_close,prev_high=EXCLUDED.prev_high,prev_low=EXCLUDED.prev_low,
                               prev_oi=EXCLUDED.prev_oi,avg_vol_1m=EXCLUDED.avg_vol_1m""",
                            (today, row.symbol, last["close"], last["high"], last["low"], prev_oi, avg_vol_1m))
                R.hset(f"ref:{row.symbol}", mapping={"prev_close": last["close"], "prev_high": last["high"],
                                                      "prev_low": last["low"], "prev_oi": prev_oi, "avg_vol_1m": avg_vol_1m,
                                                      "sector": row.sector})
                ok += 1
            except Exception as e:  # noqa
                log.warning("ref load failed %s: %s", row.symbol, e)
        con.commit()
    log.info("daily refs: %d/%d symbols loaded, %d without prev-day OI", ok, len(u), missing_oi)


def morning_setup():
    try:
        _morning_setup()
    except Exception as e:  # noqa: BLE001
        log.exception("morning setup failed")
        R.set("ingest:status", f"error: {type(e).__name__}: {str(e)[:120]}")
        raise


def _morning_setup():
    global kite
    R.set("ingest:status", "logging in")
    kite = kite_auth.session(R)
    R.set("ingest:status", "building universe")
    u = universe.build(kite)
    with psycopg.connect(DB) as con, con.cursor() as cur:
        for row in u.itertuples():
            cur.execute("""INSERT INTO instruments(symbol,eq_token,fut_token,fut_symbol,expiry,lot_size,sector)
                           VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(symbol) DO UPDATE SET eq_token=EXCLUDED.eq_token,
                           fut_token=EXCLUDED.fut_token,fut_symbol=EXCLUDED.fut_symbol,expiry=EXCLUDED.expiry,
                           lot_size=EXCLUDED.lot_size,sector=EXCLUDED.sector,updated_at=now()""",
                        (row.symbol, int(row.eq_token), int(row.fut_token), row.fut_symbol, row.expiry, int(row.lot_size), row.sector))
        con.commit()
    state.clear(); symbols.clear()
    for row in u.itertuples():
        s = SymbolState(row.symbol, int(row.eq_token), int(row.fut_token))
        state[s.eq_token] = s; state[s.fut_token] = s; symbols[s.symbol] = s
    R.delete("universe"); R.sadd("universe", *symbols.keys())
    log.info("universe ready: %d symbols; loading daily refs (takes ~3 min)", len(symbols))
    R.set("ingest:status", "loading daily refs")
    load_daily_refs(u)
    R.set("ingest:status", "ready")


# ---------------- websocket ----------------
def on_ticks(ws, ticks):
    now = dt.datetime.now(IST)
    # The socket opens at 09:14 and pre-open ticks arrive before 09:15; after 15:30
    # the closing-price/post-close ticks would open a bogus 15:30+ candle.
    if not (MARKET_OPEN <= now.time() < MARKET_CLOSE):
        with lock:
            for t in ticks:
                s = state.get(t["instrument_token"])
                if s and t["instrument_token"] == s.fut_token:
                    s.on_fut_tick(t)   # keep OI current; no candle work
        return
    with lock:
        for t in ticks:
            s = state.get(t["instrument_token"])
            if not s:
                continue
            if t["instrument_token"] == s.fut_token:
                s.on_fut_tick(t)
            else:
                s.on_equity_tick(t, now)


def on_connect(ws, response):
    eq = [s.eq_token for s in symbols.values()]
    fut = [s.fut_token for s in symbols.values()]
    ws.subscribe(eq + fut)
    ws.set_mode(ws.MODE_QUOTE, eq)
    ws.set_mode(ws.MODE_FULL, fut)      # FULL mode carries OI
    log.info("ws connected; subscribed %d eq + %d fut", len(eq), len(fut))
    R.set("ingest:ws", "connected")


def on_close(ws, code, reason):
    log.warning("ws closed %s %s", code, reason)
    R.set("ingest:ws", "disconnected")


def on_error(ws, code, reason):
    log.error("ws error %s %s", code, reason)


def start_ws():
    global ticker
    token = R.get("kite:access_token")
    if not token or not symbols:
        log.error("cannot open websocket: token=%s universe=%d (morning setup failed?)", bool(token), len(symbols))
        R.set("ingest:ws", "not started")
        return
    ticker = KiteTicker(os.environ["KITE_API_KEY"], token, reconnect=True,
                        reconnect_max_tries=300, reconnect_max_delay=30)
    ticker.on_ticks = on_ticks; ticker.on_connect = on_connect; ticker.on_close = on_close; ticker.on_error = on_error
    ticker.connect(threaded=True)


def end_of_day():
    """15:31: close the socket, persist the last candle, then exit.

    KiteTicker runs a twisted reactor in a background thread and a reactor cannot be
    restarted in the same process, so KiteTicker.connect() the next morning would
    silently do nothing. Exiting lets Docker (restart: unless-stopped) hand us a
    fresh process that waits for 08:55."""
    if ticker:
        try: ticker.close()
        except Exception: pass
    R.set("ingest:ws", "stopped")
    with lock:
        for s in symbols.values():
            s.finalize()
    flush()
    log.info("session over; exiting so tomorrow starts with a fresh process")
    os._exit(0)


# ---------------- flush ----------------
def flush():
    """Runs every minute: persist closed candles, publish live snapshot."""
    rows, live = [], {}
    with lock:
        for s in symbols.values():
            for c in s.pop_closed():
                rows.append((c.ts, s.symbol, c.open, c.high, c.low, c.close, c.volume, c.oi, c.delta))
            if s.cur:
                live[s.symbol] = {"ts": s.cur.ts.isoformat(), "o": s.cur.open, "h": s.cur.high, "l": s.cur.low,
                                  "c": s.cur.close, "v": s.cur.volume, "oi": s.cur.oi, "d": s.cur.delta}
    if rows:
        with psycopg.connect(DB) as con, con.cursor() as cur:
            cur.executemany("""INSERT INTO candles_1m(ts,symbol,open,high,low,close,volume,oi,delta)
                               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", rows)
            con.commit()
    if live:
        pipe = R.pipeline()
        for sym, v in live.items():
            pipe.set(f"live:{sym}", json.dumps(v))
        pipe.set("live:ts", dt.datetime.now(IST).isoformat())
        pipe.publish("candles:closed", json.dumps([r[1] for r in rows]))
        pipe.execute()
    log.info("flush: %d candles persisted, %d live", len(rows), len(live))


def main():
    schedule.every().day.at("08:55").do(lambda: threading.Thread(target=morning_setup, daemon=True).start())
    schedule.every().day.at("09:14").do(start_ws)
    schedule.every().day.at("15:31").do(end_of_day)
    schedule.every().minute.at(":01").do(flush)
    log.info("ingest scheduler up (process TZ=%s, now %s); waiting for 08:55 IST",
             os.environ.get("TZ"), dt.datetime.now(IST).strftime("%H:%M"))

    # If started mid-session on a weekday, catch up immediately.
    now = dt.datetime.now(IST)
    if now.weekday() < 5 and dt.time(8, 55) <= now.time() <= MARKET_CLOSE:
        log.info("started mid-session; running setup now")
        try:
            morning_setup()
            start_ws()
        except Exception:  # noqa: BLE001
            # Don't crash: Docker would restart us in a tight loop and each restart
            # retries the Kite login, which risks an account lockout. Wait for 08:55.
            log.error("mid-session setup failed; idling until the next 08:55 run")

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
