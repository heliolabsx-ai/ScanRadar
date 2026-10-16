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

import kite_auth, universe
from candles import SymbolState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")

R = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
DB = os.environ["DATABASE_URL"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

state: dict[int, SymbolState] = {}   # token -> state (both eq and fut tokens map to the same object)
symbols: dict[str, SymbolState] = {}
kite = None
ticker = None
lock = threading.Lock()


# ---------------- daily setup ----------------
def load_daily_refs(u):
    """Previous day's close/high/low for equity, previous OI for futures, avg 1-min volume (20d)."""
    today = dt.date.today()
    frm = today - dt.timedelta(days=35)
    with psycopg.connect(DB) as con, con.cursor() as cur:
        for row in u.itertuples():
            try:
                d = kite.historical_data(row.eq_token, frm, today - dt.timedelta(days=1), "day")
                f = kite.historical_data(row.fut_token, frm, today - dt.timedelta(days=1), "day", oi=True)
                if not d:
                    continue
                last = d[-1]
                prev_oi = f[-1]["oi"] if f else 0
                avg_vol_1m = sum(x["volume"] for x in d[-20:]) / max(1, len(d[-20:])) / 375
                cur.execute("""INSERT INTO daily_ref(day,symbol,prev_close,prev_high,prev_low,prev_oi,avg_vol_1m)
                               VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(day,symbol) DO UPDATE SET
                               prev_close=EXCLUDED.prev_close,prev_high=EXCLUDED.prev_high,prev_low=EXCLUDED.prev_low,
                               prev_oi=EXCLUDED.prev_oi,avg_vol_1m=EXCLUDED.avg_vol_1m""",
                            (today, row.symbol, last["close"], last["high"], last["low"], prev_oi, avg_vol_1m))
                R.hset(f"ref:{row.symbol}", mapping={"prev_close": last["close"], "prev_high": last["high"],
                                                      "prev_low": last["low"], "prev_oi": prev_oi, "avg_vol_1m": avg_vol_1m,
                                                      "sector": row.sector})
                time.sleep(0.35)  # historical API: 3 req/s
            except Exception as e:  # noqa
                log.warning("ref load failed %s: %s", row.symbol, e)
        con.commit()


def morning_setup():
    global kite
    kite, token = kite_auth.login()
    R.set("kite:access_token", token, ex=20 * 3600)
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
    load_daily_refs(u)
    R.set("ingest:status", "ready")


# ---------------- websocket ----------------
def on_ticks(ws, ticks):
    now = dt.datetime.now(IST)
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
    ticker = KiteTicker(os.environ["KITE_API_KEY"], R.get("kite:access_token"), reconnect=True,
                        reconnect_max_tries=300, reconnect_max_delay=30)
    ticker.on_ticks = on_ticks; ticker.on_connect = on_connect; ticker.on_close = on_close; ticker.on_error = on_error
    ticker.connect(threaded=True)


def stop_ws():
    if ticker:
        try: ticker.close()
        except Exception: pass
    R.set("ingest:ws", "stopped")


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
    schedule.every().day.at("15:31").do(stop_ws)
    schedule.every().minute.at(":01").do(flush)
    log.info("ingest scheduler up; waiting for 08:55 IST")

    # If started mid-session on a weekday, catch up immediately.
    now = dt.datetime.now(IST)
    if now.weekday() < 5 and dt.time(8, 55) <= now.time() <= dt.time(15, 30):
        log.info("started mid-session; running setup now")
        morning_setup()
        start_ws()

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
