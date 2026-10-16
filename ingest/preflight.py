"""
First-day preflight for the ingest pipeline. Run inside the ingest container:

    docker compose run --rm ingest python preflight.py            # all checks
    docker compose run --rm ingest python preflight.py --no-ws    # skip the WebSocket sample

Checks, in order, and stops at the first failure:
  1. Kite session: reuses the cached token or performs the TOTP auto-login.
  2. Universe: instrument dump -> ~200 stock futures, nearest expiry, sector coverage.
  3. Historical API: daily candles for 3 symbols, equity + futures with oi=True,
     and confirms the futures rows actually carry an 'oi' column.
  4. WebSocket (market hours only): 20 s sample on 5 symbols; confirms equity ticks
     carry volume_traded and futures FULL-mode ticks carry oi.

Safe to run while the ingest service is up: it never logs in if the cached token
still works (a fresh login would invalidate the token the live socket is using).
"""
import os, sys, time, datetime as dt, threading, collections
import redis
from kiteconnect import KiteTicker

import kite_auth, universe

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
R = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)


def step(title):
    print(f"\n=== {title}", flush=True)


def fail(msg):
    print(f"FAIL: {msg}", flush=True)
    sys.exit(1)


def main(do_ws=True):
    step("1. Kite session")
    kite = kite_auth.session(R)
    prof = kite.profile()
    print(f"OK  user={prof.get('user_id')} broker={prof.get('broker')} exchanges={prof.get('exchanges')}")

    step("2. Universe")
    u = universe.build(kite)
    if len(u) < 150:
        fail(f"only {len(u)} stock futures found; expected ~200 (check instrument dump / INDEX_NAMES filter)")
    others = int((u.sector == "OTHERS").sum())
    print(f"OK  {len(u)} symbols, nearest expiry {u.expiry.min()} .. {u.expiry.max()}, "
          f"{others} unmapped sectors ({others * 100 // len(u)}%)")
    if others:
        print("    unmapped:", " ".join(sorted(u[u.sector == "OTHERS"].symbol)[:40]), "..." if others > 40 else "")

    step("3. Historical API (eq daily + fut daily with oi=True)")
    today = dt.date.today()
    frm, to = today - dt.timedelta(days=35), today - dt.timedelta(days=1)
    sample = u[u.symbol.isin(["RELIANCE", "HDFCBANK", "TATASTEEL"])]
    if sample.empty:
        sample = u.head(3)
    t0 = time.time()
    for row in sample.itertuples():
        d = kite.historical_data(int(row.eq_token), frm, to, "day")
        time.sleep(0.4)
        f = kite.historical_data(int(row.fut_token), frm, to, "day", oi=True)
        time.sleep(0.4)
        if not d:
            fail(f"{row.symbol}: no equity daily candles")
        if not f:
            fail(f"{row.symbol}: no futures daily candles for {row.fut_symbol}")
        if "oi" not in f[-1]:
            fail(f"{row.symbol}: futures candle has no 'oi' key: {f[-1]} (historical plan missing?)")
        print(f"OK  {row.symbol:10s} prev_close={d[-1]['close']:<10} fut={row.fut_symbol:16s} "
              f"prev_oi={f[-1]['oi']:<10} last_day={d[-1]['date'].date()}")
    print(f"    6 calls in {time.time() - t0:.1f}s (budget is 3 req/s; full ref load ≈ {len(u) * 2 * 0.4 / 60:.1f} min)")

    if not do_ws:
        return
    step("4. WebSocket sample (20 s, 5 symbols)")
    now = dt.datetime.now(IST)
    if now.weekday() > 4 or not (dt.time(9, 0) <= now.time() <= dt.time(15, 30)):
        print("SKIP  outside market hours; re-run between 09:15 and 15:30 IST on a trading day")
        return
    picks = u.head(5)
    eq = [int(x) for x in picks.eq_token]
    fut = [int(x) for x in picks.fut_token]
    token_sym = {**{int(r.eq_token): r.symbol for r in picks.itertuples()},
                 **{int(r.fut_token): r.fut_symbol for r in picks.itertuples()}}
    seen = collections.defaultdict(int)
    eq_vol = collections.defaultdict(int)
    fut_oi = {}
    done = threading.Event()

    def on_ticks(ws, ticks):
        for t in ticks:
            tok = t["instrument_token"]
            seen[token_sym.get(tok, tok)] += 1
            if tok in eq:
                eq_vol[token_sym[tok]] = t.get("volume_traded")
            if tok in fut and t.get("oi"):
                fut_oi[token_sym[tok]] = t["oi"]

    def on_connect(ws, resp):
        ws.subscribe(eq + fut)
        ws.set_mode(ws.MODE_QUOTE, eq)
        ws.set_mode(ws.MODE_FULL, fut)
        print("    connected; subscribed", len(eq), "eq +", len(fut), "fut")

    def on_error(ws, code, reason):
        print(f"    ws error {code} {reason}")
        done.set()

    kt = KiteTicker(os.environ["KITE_API_KEY"], R.get("kite:access_token"), reconnect=False)
    kt.on_ticks, kt.on_connect, kt.on_error = on_ticks, on_connect, on_error
    kt.connect(threaded=True)
    done.wait(20)
    try:
        kt.close()
    except Exception:
        pass
    print("    ticks per instrument:", dict(seen))
    print("    equity volume_traded:", dict(eq_vol))
    print("    futures oi          :", fut_oi)
    if not seen:
        fail("no ticks received in 20 s (token invalid? market closed? check 'ws error' above)")
    if not any(eq_vol.values()):
        fail("equity ticks arrived without volume_traded; QUOTE mode not applied?")
    if not fut_oi:
        fail("no futures tick carried OI in 20 s; FULL mode not applied, or illiquid picks — retry")
    print("OK  OI arrives on futures FULL-mode ticks; volume_traded on equity QUOTE ticks")


if __name__ == "__main__":
    code = 0
    try:
        main(do_ws="--no-ws" not in sys.argv)
        print("\nPREFLIGHT PASSED", flush=True)
    except SystemExit as e:
        code = int(e.code or 0)
    except Exception:  # noqa: BLE001
        import traceback; traceback.print_exc()
        code = 1
    finally:
        os._exit(code)   # the twisted reactor thread would otherwise keep the process alive
