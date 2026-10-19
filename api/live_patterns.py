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
MIN_CONF = float(os.environ.get("PATTERN_MIN_CONF", "40"))     # Telegram and desk only for shapes whose recorded hit rate clears this


def worth_telling(r: dict, min_conf: float = MIN_CONF) -> bool:
    """A trigger is messaged when its recorded confidence clears the floor, or when it has no record yet."""
    c = r.get("confidence")
    return c is None or c.get("hit_rate") is None or c["hit_rate"] >= min_conf
from core.patterns import LABELS as LABEL


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


_st_cache: dict = {}


def _stats_cached(con, now: dt.datetime) -> dict:
    if _st_cache.get("until") and _st_cache["until"] > now:
        return _st_cache["value"]
    import patterns as pt
    value = pt.stats(con, 42)
    _st_cache.update(until=now + dt.timedelta(minutes=10), value=value)
    return value


_R = None


def _redis():
    global _R
    if _R is None:
        import redis
        _R = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    return _R


def tick(connect, now: dt.datetime, send) -> tuple[int, int]:
    """One iteration, run in a worker process: returns (rows, new triggers)."""
    import patterns as pt
    from replay import load_day, load_refs
    from core.patterns import detector_hash
    R = _redis()
    at = now.astimezone(IST).replace(second=0, microsecond=0)
    day = at.date()
    with connect() as con:
        frames = load_day(con, day, upto=at)
        rows: list = []
        if frames:
            refs = load_refs(con, day)
            cut = {s: df[df.ts < at] for s, df in frames.items()}
            records, rows = pt.detect_frames(cut, refs, pd.Timestamp(at))
            rows = [{**r, "source": "live"} for r in rows]
            if records:
                with con.cursor() as cur:
                    pt.upsert_patterns(cur, day, records, "live", detector_hash())     # full lifecycle, DO UPDATE
                con.commit()
    try:
        st = _stats_cached(con, at)
    except Exception:  # noqa: BLE001
        st = {}
    js = pt.to_json_rows(pt.with_confidence(rows, st))
    R.set("patterns:today:latest", json.dumps({"at": at.strftime("%H:%M"), "rows": js}), ex=300)
    seen_key = f"pseen:{day}"
    seen = set(R.smembers(seen_key))
    fresh = new_triggers(seen, js)
    for r in fresh:
        R.sadd(seen_key, _key(r)); R.expire(seen_key, 86400)
        if not worth_telling(r):
            continue
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
