"""Live pattern loop (API process, until Phase 2 moves it into the engine).

Every minute during the session: detect today's patterns from the stored candles,
upsert them as live rows, send one Telegram per new trigger (once per symbol and
kind per day) and publish `patterns:updated` so the dashboard refreshes.
"""
import os, json, asyncio, logging, datetime as dt
import pandas as pd
from core.calendar import is_trading_day

log = logging.getLogger("live_patterns")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
START, END = dt.time(9, 20), dt.time(15, 20)
LABEL = {"double_bottom": "Double bottom", "triple_bottom": "Triple bottom", "double_top": "Double top", "triple_top": "Triple top"}


def should_run(now: dt.datetime) -> bool:
    n = now.astimezone(IST)
    return is_trading_day(n.date()) and START <= n.time() <= END


def _key(r: dict) -> str:
    return f"{r['symbol']}|{r['kind']}|{str(r['formed_at'])[:16]}"


def new_triggers(seen: set, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if not r.get("triggered_at"):
            continue
        k = _key(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def message(r: dict) -> str:
    side = "BUY" if r["side"] == "long" else "SELL"
    hhmm = str(r["triggered_at"])[11:16]
    return (f"<b>{LABEL.get(r['kind'], r['kind'])} · {side}</b>\n"
            f"{r['symbol']} entry ₹{r['entry']:,.2f} · stop ₹{r['stop']:,.2f} · target ₹{r['target']:,.2f} · "
            f"R:R 1:{r['rr']:.1f} · score {r['score']}/5\n{hhmm} · scanradar.tech")


def tick(connect, now: dt.datetime, send) -> tuple[int, int]:
    """One iteration, run in a worker process: returns (rows, new triggers)."""
    import redis
    import patterns as pt
    from core.patterns import detector_hash
    R = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    at = now.astimezone(IST).replace(second=0, microsecond=0)
    day = at.date()
    with connect() as con:
        rows = pt.today_rows(con, at)
        if rows:
            with con.cursor() as cur:
                pt.upsert_patterns(cur, day, rows, "live", detector_hash())
            con.commit()
    seen_key = f"pseen:{day}"
    seen = set(R.smembers(seen_key))
    fresh = new_triggers(seen, rows)
    for r in fresh:
        R.sadd(seen_key, _key(r)); R.expire(seen_key, 86400)
        if R.set(f"pcool:{day}:{r['symbol']}:{r['kind']}", "1", ex=86400, nx=True):
            try:
                send(message(r))
            except Exception as e:  # noqa: BLE001
                log.warning("telegram failed: %s", e)
    if fresh:
        R.publish("patterns:updated", json.dumps({"at": at.strftime("%H:%M"), "new": len(fresh)}))
    return len(rows), len(fresh)


async def run(connect, pool, send) -> None:
    log.info("live pattern loop up (PATTERNS_LIVE=%s)", os.environ.get("PATTERNS_LIVE", "1"))
    loop = asyncio.get_running_loop()
    while True:
        now = dt.datetime.now(IST)
        try:
            if should_run(now):
                n, k = await loop.run_in_executor(pool, tick, connect, now, send)
                if k:
                    log.info("patterns %s: %d rows, %d new triggers", now.strftime("%H:%M"), n, k)
        except Exception:  # noqa: BLE001
            log.exception("live pattern tick failed")
        await asyncio.sleep(65 - dt.datetime.now(IST).second)     # a few seconds after each minute boundary
