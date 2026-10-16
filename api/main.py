import os, json, asyncio, datetime as dt
import redis.asyncio as aioredis, psycopg
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException

app = FastAPI(title="ScanRadar API")
R = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
DB = os.environ["DATABASE_URL"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


@app.get("/api/health")
async def health():
    return {"ingest": await R.get("ingest:status"), "ws": await R.get("ingest:ws"), "live_ts": await R.get("live:ts")}


@app.get("/api/scan")
async def scan():
    raw = await R.get("scan:latest")
    if not raw:
        raise HTTPException(404, "no scan yet (market closed or ingest not ready)")
    return json.loads(raw)


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
