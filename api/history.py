"""Per-minute ranking history of a day (scan_snapshots): written live by the engine,
built once in the background for backfilled days. Feeds the card's rank-stability
fields: how long a symbol has been a pick and where it ranked 30 minutes ago."""
import json, datetime as dt, logging
import pandas as pd
from replay import IST, session_bounds, load_day, load_refs, snapshot_from_frames

log = logging.getLogger("history")
Timeline = dict[str, dict[str, tuple[int, int, bool]]]      # "HH:MM" -> symbol -> (rank, side_rank, picked)
PAYLOAD_KEYS = ("pct", "oi_pct", "oi_label", "rvol", "rr", "picked", "side_rank")


def rank_timeline(con, day: dt.date) -> Timeline | None:
    start, end = session_bounds(day)
    with con.cursor() as cur:
        cur.execute("SELECT ts, symbol, rank, payload FROM scan_snapshots WHERE ts >= %s AND ts < %s", (start, end))
        rows = cur.fetchall()
    if not rows:
        return None
    tl: Timeline = {}
    for ts, sym, rk, payload in rows:
        m = ts.astimezone(IST).strftime("%H:%M")
        p = payload or {}
        tl.setdefault(m, {})[sym] = (int(rk), int(p.get("side_rank") or 0), bool(p.get("picked", False)))
    return tl


def stability(tl: Timeline, at: str, symbol: str) -> tuple[str | None, int | None]:
    """(top3_since, rank_30m_ago) for `symbol` as of minute `at` ("HH:MM")."""
    minutes = sorted(m for m in tl if m <= at)
    if not minutes:
        return None, None
    since = None
    for m in reversed(minutes):
        row = tl[m].get(symbol)
        if not row or not row[2]:
            break
        since = m
    t = dt.datetime.strptime(at, "%H:%M") - dt.timedelta(minutes=30)
    ago = tl.get(t.strftime("%H:%M"), {}).get(symbol)
    return since, (ago[0] if ago else None)


def build_timeline(connect, day: dt.date) -> int:
    """Compute the minute-by-minute ranking of a stored day and persist it to
    scan_snapshots (ON CONFLICT DO NOTHING). Returns rows inserted."""
    with connect() as con:
        frames = load_day(con, day)
        refs = load_refs(con, day)
        if not frames:
            return 0
        start, _ = session_bounds(day)
        n = 0
        with con.cursor() as cur:
            for i in range(376):                       # 09:15 .. 15:30
                at = start + dt.timedelta(minutes=i)
                snap = snapshot_from_frames(frames, refs, pd.Timestamp(at))
                if not snap:
                    continue
                rows = [(at, a["symbol"], a["score"], a["rank"], json.dumps({k: a[k] for k in PAYLOAD_KEYS}, default=float))
                        for a in snap["bulls"] + snap["bears"]]
                cur.executemany("INSERT INTO scan_snapshots(ts,symbol,score,rank,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", rows)
                n += len(rows)
                if i % 30 == 29:
                    con.commit()
            con.commit()
    log.info("timeline %s: %d rows", day, n)
    return n
