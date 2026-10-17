import os, json, asyncio, datetime as dt, logging
import redis.asyncio as aioredis, psycopg
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException

import replay as rp
import history as hs

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="ScanRadar API")
R = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
DB = os.environ["DATABASE_URL"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_background: set = set()


def connect():
    return psycopg.connect(DB)


def _today() -> dt.date:
    return dt.datetime.now(IST).date()


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
    key = f"tl:{day}"
    raw = await R.get(key)
    if raw:
        return json.loads(raw)

    def q():
        with connect() as con:
            return hs.rank_timeline(con, day)
    tl = await asyncio.get_running_loop().run_in_executor(None, q)
    if tl:
        complete = max(tl) >= "15:30"
        await R.set(key, json.dumps(tl), ex=86400 if complete and day < _today() else 60)
    return tl


@app.get("/api/replay")
async def replay_at(day: str, at: str):
    try:
        d, at_dt = rp.parse_replay_args(day, at)
    except rp.ReplayError as e:
        raise HTTPException(e.status_code, e.detail)
    loop = asyncio.get_running_loop()
    key = f"replay:{d}:{at}"
    raw = await R.get(key)
    if raw:
        snap = json.loads(raw)
    else:
        def compute():
            with connect() as con:
                return rp.snapshot_at(con, d, at_dt)
        snap = await loop.run_in_executor(None, compute)
        if snap is None:
            raise HTTPException(404, f"no candles for {d}")
        await R.set(key, json.dumps(snap, default=float), ex=7 * 86400)

    tl = await _timeline(d)
    if tl:
        for a in snap["bulls"] + snap["bears"]:
            a["top3_since"], a["rank_30m_ago"] = hs.stability(tl, at, a["symbol"])
    elif d < _today() and await R.set(f"timeline:{d}", "1", ex=600, nx=True):
        task = loop.run_in_executor(None, hs.build_timeline, connect, d)
        _background.add(task)
        task.add_done_callback(_background.discard)
    return snap


@app.get("/api/candles/{symbol}")
async def candles(symbol: str, day: str | None = None):
    d = dt.date.fromisoformat(day) if day else dt.datetime.now(IST).date()
    start = dt.datetime.combine(d, dt.time(9, 15), IST)
    with psycopg.connect(DB) as con, con.cursor() as cur:
        cur.execute("SELECT ts,open,high,low,close,volume,oi,delta FROM candles_1m WHERE symbol=%s AND ts>=%s AND ts<%s ORDER BY ts",
                    (symbol.upper(), start, start + dt.timedelta(hours=7)))
        rows = [{"ts": r[0].isoformat(), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5], "oi": r[6], "d": r[7]} for r in cur.fetchall()]
    ref = await R.hgetall(f"ref:{symbol.upper()}")
    return {"symbol": symbol.upper(), "ref": ref, "candles": rows}


@app.get("/api/alerts")
async def alerts(limit: int = 100):
    with psycopg.connect(DB) as con, con.cursor() as cur:
        cur.execute("SELECT ts,scan,symbol,price,payload FROM alerts ORDER BY ts DESC LIMIT %s", (limit,))
        return [{"ts": r[0].isoformat(), "scan": r[1], "symbol": r[2], "price": r[3], "payload": r[4]} for r in cur.fetchall()]


@app.websocket("/ws/scan")
async def ws_scan(ws: WebSocket):
    await ws.accept()
    ps = R.pubsub(); await ps.subscribe("scan:updated")
    try:
        raw = await R.get("scan:latest")
        if raw:
            await ws.send_text(raw)
        async for msg in ps.listen():
            if msg["type"] == "message":
                raw = await R.get("scan:latest")
                if raw:
                    await ws.send_text(raw)
    except WebSocketDisconnect:
        pass
    finally:
        await ps.unsubscribe("scan:updated")
