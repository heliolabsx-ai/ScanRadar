import os, json, asyncio, datetime as dt, logging
from concurrent.futures import ProcessPoolExecutor
import redis.asyncio as aioredis, psycopg
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException

import replay as rp
import history as hs
import alerts as al
import patterns as pt
import live_patterns as lp
from core.patterns import detector_hash
from core.notify import telegram

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="ScanRadar API")
R = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
DB = os.environ["DATABASE_URL"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_background: set = set()
_prep_pool = ProcessPoolExecutor(max_workers=1)      # keeps 2–4 min of pandas out of the API's GIL
_live_pool = ProcessPoolExecutor(max_workers=1)      # the live pattern loop never waits behind a day preparation
_today_locks: dict = {}
_tl_mem: dict = {}                                    # day -> (expires_at, timeline); parsed once per process


def connect():
    return psycopg.connect(DB)


def _today() -> dt.date:
    return dt.datetime.now(IST).date()


@app.on_event("startup")
async def _start_live_patterns():
    if os.environ.get("PATTERNS_LIVE", "1") != "0":
        _background.add(asyncio.create_task(lp.run(connect, _live_pool, telegram)))


@app.get("/api/health")
async def health():
    return {"ingest": await R.get("ingest:status"), "ws": await R.get("ingest:ws"), "live_ts": await R.get("live:ts")}


@app.get("/api/scan")
async def scan():
    raw = await R.get("scan:latest")
    if not raw:
        raise HTTPException(404, "no scan yet (market closed or ingest not ready)")
    return json.loads(raw)


@app.get("/api/days")
async def days():
    def q():
        with connect() as con, con.cursor() as cur:
            cur.execute("SELECT DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::date AS d FROM candles_1m "
                        "WHERE symbol='RELIANCE' ORDER BY d DESC")
            ds = [r[0].isoformat() for r in cur.fetchall()]
            cur.execute("SELECT min(expiry) FROM instruments")
            ex = cur.fetchone()[0]
        return ds, ex
    ds, ex = await asyncio.get_running_loop().run_in_executor(None, q)
    live = (await R.get("ingest:ws")) == "connected"
    return {"days": ds, "latest": ds[0] if ds else None, "live": live, "expiry": ex.isoformat() if ex else None}


@app.get("/api/instruments")
async def instruments():
    def q():
        with connect() as con, con.cursor() as cur:
            cur.execute("SELECT symbol, lot_size, sector, expiry FROM instruments ORDER BY symbol")
            return [{"symbol": s, "lot_size": lot, "sector": sec, "expiry": ex.isoformat() if ex else None}
                    for s, lot, sec, ex in cur.fetchall()]
    return await asyncio.get_running_loop().run_in_executor(None, q)


async def _timeline(day: dt.date):
    hit = _tl_mem.get(day)
    if hit and hit[0] > dt.datetime.now(IST):
        return hit[1]
    key = f"tl:{day}"
    raw = await R.get(key)
    if raw:
        tl = await asyncio.get_running_loop().run_in_executor(None, json.loads, raw)
        _remember_tl(day, tl)
        return tl

    def q():
        with connect() as con:
            return hs.rank_timeline(con, day)
    tl = await asyncio.get_running_loop().run_in_executor(None, q)
    if tl:
        complete = max(tl) >= "15:30"
        await R.set(key, json.dumps(tl), ex=86400 if complete and day < _today() else 60)
        _remember_tl(day, tl)
    return tl


def _remember_tl(day: dt.date, tl) -> None:
    ttl = 86400 if day < _today() else 60
    _tl_mem[day] = (dt.datetime.now(IST) + dt.timedelta(seconds=ttl), tl)
    while len(_tl_mem) > 8:                             # ~3 MB each; keep the recent handful
        _tl_mem.pop(next(iter(_tl_mem)))


def _prepare_day(day: dt.date) -> None:
    """Background: replay alerts + outcomes (settled days) and the per-minute ranking
    history. Triggered once per day by the first replay/alerts request."""
    now = dt.datetime.now(IST)
    if not rp.day_is_settled(day, now):
        return                                        # a live day is never prepared or pre-warmed
    with connect() as con:
        scans = al.scan_files()
        if al.replay_needed(con, day, scans) or al.outcomes_needed(con, day):
            frames = rp.load_day(con, day)
            refs = rp.load_refs(con, day)
            al.ensure_replay_alerts(con, day, frames, refs, scans, now)
            al.ensure_outcomes(con, day, frames, refs, scans, now)
        if pt.patterns_needed(con, day, detector_hash()):
            rows = pt.patterns_for_day(con, day)
            with con.cursor() as cur:
                pt.upsert_patterns(cur, day, rows, "replay", detector_hash())
            con.commit()
        import redis as _redis
        _redis.from_url(os.environ["REDIS_URL"], decode_responses=True).set(f"patdone:{day}:{detector_hash()}", "1", ex=30 * 86400)
        if hs.rank_timeline(con, day) is None:
            hs.build_timeline(connect, day)
        # warm the frames a viewer lands on: the close and every half hour
        import redis as _redis
        r = _redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
        for hhmm in ("15:30", "09:45", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"):
            key = f"replay:{day}:{hhmm}"
            if not r.exists(key):
                try:
                    snap = rp.snapshot_at(con, day, dt.datetime.combine(day, dt.time.fromisoformat(hhmm), IST))
                    r.set(key, json.dumps(snap, default=float), ex=7 * 86400)
                except rp.ReplayError:
                    pass


async def _kick_prepare(day: dt.date) -> None:
    key = f"prep:{day}"
    if not await R.set(key, "1", ex=900, nx=True):
        return
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(_prep_pool, _prepare_day, day)
    _background.add(task)

    def done(t):
        _background.discard(t)
        try:
            t.result()
        except Exception:  # noqa: BLE001
            logging.getLogger("api").exception("prepare_day %s failed", day)
        asyncio.ensure_future(R.delete(key))
    task.add_done_callback(done)


@app.get("/api/replay")
async def replay_at(day: str, at: str):
    try:
        d, at_dt = rp.parse_replay_args(day, at)
    except rp.ReplayError as e:
        raise HTTPException(e.status_code, e.detail)
    now = dt.datetime.now(IST)
    try:
        rp.assert_not_future(at_dt, now)
    except rp.ReplayError as e:
        raise HTTPException(e.status_code, e.detail)
    at = at_dt.strftime("%H:%M")
    loop = asyncio.get_running_loop()
    key = f"replay:{d}:{at}"
    raw = await R.get(key)
    if raw:
        snap = json.loads(raw)
    else:
        def compute():
            with connect() as con:
                return rp.snapshot_at(con, d, at_dt)
        try:
            snap = await loop.run_in_executor(None, compute)
        except rp.ReplayError as e:
            raise HTTPException(e.status_code, e.detail)
        ttl = rp.cache_ttl(d, at_dt, now)
        if ttl:
            await R.set(key, json.dumps(snap, default=float), ex=ttl)

    tl = await _timeline(d)
    if tl:
        for a in snap["bulls"] + snap["bears"]:
            a["top3_since"], a["rank_30m_ago"] = hs.stability(tl, at, a["symbol"])
    elif rp.day_is_settled(d, now):
        await _kick_prepare(d)
    return snap


@app.get("/api/candles/{symbol}")
async def candles(symbol: str, day: str | None = None, tf: str | None = None, days: int = 250):
    sym = symbol.upper()
    loop = asyncio.get_running_loop()
    if tf == "1d":
        def q_daily():
            with connect() as con, con.cursor() as cur:
                cur.execute("SELECT day,open,high,low,close,volume,oi FROM candles_1d WHERE symbol=%s ORDER BY day DESC LIMIT %s", (sym, days))
                rows = cur.fetchall()[::-1]
            return {"symbol": sym, "tf": "1d", "bars": [{"day": r[0].isoformat(), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5], "oi": r[6]} for r in rows]}
        return await loop.run_in_executor(None, q_daily)
    try:
        d = dt.date.fromisoformat(day) if day else _today()
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    start = dt.datetime.combine(d, dt.time(9, 15), IST)

    def q():
        with connect() as con, con.cursor() as cur:
            cur.execute("SELECT ts,open,high,low,close,volume,oi,delta FROM candles_1m WHERE symbol=%s AND ts>=%s AND ts<%s ORDER BY ts",
                        (sym, start, start + dt.timedelta(hours=7)))
            rows = [{"ts": r[0].astimezone(IST).isoformat(), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5], "oi": r[6], "d": r[7]} for r in cur.fetchall()]
            cur.execute("SELECT prev_close, prev_high, prev_low, prev_oi, avg_vol_1m FROM daily_ref WHERE day=%s AND symbol=%s", (d, sym))
            r = cur.fetchone()
        ref = {"prev_close": r[0], "prev_high": r[1], "prev_low": r[2], "prev_oi": r[3], "avg_vol_1m": r[4]} if r else {}
        return rows, ref
    rows, ref = await loop.run_in_executor(None, q)
    if d == _today():
        live_ref = await R.hgetall(f"ref:{sym}")
        if live_ref:
            ref = {k: (float(v) if k != "sector" else v) for k, v in live_ref.items()}
    return {"symbol": sym, "day": d.isoformat(), "ref": ref, "candles": rows, "delta_available": any(c["d"] for c in rows)}


@app.get("/api/alerts")
async def alerts(limit: int = 100, day: str | None = None):
    loop = asyncio.get_running_loop()
    if day is None:
        def q_recent():
            with connect() as con, con.cursor() as cur:
                cur.execute("SELECT ts,scan,symbol,price,payload,source FROM alerts ORDER BY ts DESC LIMIT %s", (limit * 2,))
                rows = [{"ts": r[0].astimezone(IST), "scan": r[1], "symbol": r[2], "price": r[3],
                         "tf": (r[4] or {}).get("tf"), "source": r[5], "side": (r[4] or {}).get("side"),
                         "outcome": (r[4] or {}).get("outcome")} for r in cur.fetchall()]
                return [dict(r, ts=r["ts"].isoformat()) for r in al.merge_live_replay(rows)][:limit]
        return await loop.run_in_executor(None, q_recent)
    try:
        d = dt.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    def check():
        with connect() as con:
            now = dt.datetime.now(IST)
            need = rp.day_is_settled(d, now) and (al.replay_needed(con, d, al.scan_files()) or al.outcomes_needed(con, d))
            return need, al.alerts_for_day(con, d)
    need, rows = await loop.run_in_executor(None, check)
    if need:
        await _kick_prepare(d)
    return {"day": d.isoformat(), "pending": bool(need), "alerts": rows}


@app.get("/api/patterns")
async def patterns(day: str, at: str):
    try:
        d, at_dt = rp.parse_replay_args(day, at)
        now = dt.datetime.now(IST)
        rp.assert_not_future(at_dt, now)
    except rp.ReplayError as e:
        raise HTTPException(e.status_code, e.detail)
    at = at_dt.strftime("%H:%M")
    loop = asyncio.get_running_loop()
    settled = rp.day_is_settled(d, now)
    if settled:
        done = await R.exists(f"patdone:{d}:{detector_hash()}")
        def q():
            with connect() as con:
                return (pt.patterns_needed(con, d, detector_hash()) if not done else False), pt.rows_at(pt.stored_rows(con, d), at_dt)
        need, rows = await loop.run_in_executor(None, q)
        rows = pt.to_json_rows(rows)
        if need:
            await _kick_prepare(d)
    else:
        need = False
        latest = await R.get("patterns:today:latest")
        latest = json.loads(latest) if latest else None
        if latest and latest.get("at") == at:
            rows = latest["rows"]                      # the live loop computed this minute already
        else:
            key = f"patterns:today:{at}"
            raw = await R.get(key)
            if raw:
                rows = json.loads(raw)
            else:
                lock = _today_locks.setdefault(at, asyncio.Lock())
                async with lock:                           # coalesce slider steps on the same minute
                    raw = await R.get(key)
                    if raw:
                        rows = json.loads(raw)
                    else:
                        rows = await loop.run_in_executor(_live_pool, pt.today_rows_json, at_dt.isoformat())
                        await R.set(key, json.dumps(rows), ex=60)
    counts = {k: sum(1 for r in rows if r["state"] == k) for k in ("forming", "triggered", "failed")}
    return {"day": d.isoformat(), "at": at, "pending": bool(need), "rows": rows, "counts": counts}


@app.get("/api/pattern-stats")
async def pattern_stats(days: int = 20):
    def q():
        with connect() as con:
            return pt.stats(con, days)
    return await asyncio.get_running_loop().run_in_executor(None, q)


@app.get("/api/scan-stats")
async def scan_stats(days: int = 20):
    def q():
        with connect() as con:
            return al.scan_stats(con, days)
    return await asyncio.get_running_loop().run_in_executor(None, q)


@app.websocket("/ws/scan")
async def ws_scan(ws: WebSocket):
    await ws.accept()
    ps = R.pubsub(); await ps.subscribe("scan:updated", "patterns:updated")
    try:
        raw = await R.get("scan:latest")
        if raw:
            await ws.send_text(raw)
        async for msg in ps.listen():
            if msg["type"] != "message":
                continue
            if msg["channel"] == "patterns:updated":
                await ws.send_text(json.dumps({"type": "patterns", "data": json.loads(msg["data"])}))
                continue
            raw = await R.get("scan:latest")
            if raw:
                await ws.send_text(raw)
    except WebSocketDisconnect:
        pass
    finally:
        await ps.unsubscribe("scan:updated", "patterns:updated")
