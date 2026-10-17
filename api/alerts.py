"""Per-day alerts: live rows written by the engine, replay rows computed here from
stored candles with core.backtest, and the outcome of each alert once the day is
over (what a trade taken at the alert did)."""
import os, json, glob, hashlib, datetime as dt
import pandas as pd
from core.analytics import analyse
from core.asof import view_at
from core.backtest import fires, outcome

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SETTLED = dt.time(15, 35)            # a day's alerts/outcomes may be computed from this time on
SCAN_DIR = os.environ.get("SCAN_DIR", "/app/scans")


def ist_day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time(0), IST)
    return start, start + dt.timedelta(days=1)


def scan_files() -> list[tuple[dict, str]]:
    out = []
    for p in sorted(glob.glob(os.path.join(SCAN_DIR, "*.json"))):
        with open(p, "rb") as f:
            b = f.read()
        try:
            out.append((json.loads(b), hashlib.sha256(b).hexdigest()))
        except ValueError:
            continue
    return out


def day_is_settled(day: dt.date, now: dt.datetime) -> bool:
    today = now.astimezone(IST).date()
    return day < today or (day == today and now.astimezone(IST).timetz().replace(tzinfo=None) >= SETTLED)


def side_levels(a: dict, side: str) -> tuple[float, float]:
    """(stop, target) for a trade on `side`, from analyse()'s levels. When the
    analysis direction agrees with the side its own stop/target are used; otherwise
    the same construction is applied for the trade's side."""
    ob = a.get("ob")
    if side == "long":
        if a["dir"] > 0:
            return float(a["stop"]), float(a["target"])
        return float(min(a["S1"], ob["lo"]) if ob else a["S1"]), float(a["R2"])
    if a["dir"] < 0:
        return float(a["stop"]), float(a["target"])
    return float(max(a["R1"], ob["hi"]) if ob else a["R1"]), float(a["S2"])


def _ctx(ref: dict) -> dict:
    return {"prev_day_close": ref.get("prev_close", 0), "prev_day_high": ref.get("prev_high", 0),
            "prev_day_low": ref.get("prev_low", 0)}


def _trade_payload(sym: str, df: pd.DataFrame, ref: dict, ts: pd.Timestamp, side: str, price: float) -> dict:
    a = analyse(sym, view_at(df, ts), ref)
    if not a:
        return {}
    stop, target = side_levels(a, side)
    return {"stop": stop, "target": target, "outcome": outcome(df, ts, side, price, stop, target)}


def ensure_replay_alerts(con, day: dt.date, frames: dict, refs: dict, scans: list[tuple[dict, str]], now: dt.datetime) -> None:
    """Cache per (day, scan): recompute when missing or when the scan file changed
    (hash in payload). Never for a day still in progress."""
    if not day_is_settled(day, now) or not frames:
        return
    start, end = ist_day_bounds(day)
    with con.cursor() as cur:
        cur.execute("SELECT scan, payload->>'scan_hash' FROM alerts WHERE source='replay' AND ts>=%s AND ts<%s GROUP BY 1,2",
                    (start, end))
        have: dict[str, set] = {}
        for name, h in cur.fetchall():
            have.setdefault(name, set()).add(h)
        for scan, h in scans:
            name = scan["name"]
            if have.get(name) == {h}:
                continue
            cur.execute("DELETE FROM alerts WHERE source='replay' AND scan=%s AND ts>=%s AND ts<%s", (name, start, end))
            tf, side = scan.get("timeframe", "5m"), scan.get("side")
            cooldown = int(scan.get("alert", {}).get("cooldown_min", 30))
            rows = []
            for sym, df in frames.items():
                ref = refs.get(sym)
                if not ref or not ref.get("prev_close"):
                    continue
                for ts in fires(scan, df, _ctx(ref), cooldown):
                    price = float(view_at(df, ts).close.iloc[-1])
                    payload = {"tf": tf, "scan_hash": h, "source": "replay", "side": side}
                    if side:
                        payload.update(_trade_payload(sym, df, ref, ts, side, price))
                    rows.append((ts.to_pydatetime(), name, sym, price, json.dumps(payload, default=float)))
            if rows:
                cur.executemany("INSERT INTO alerts(ts,scan,symbol,price,payload,source) VALUES(%s,%s,%s,%s,%s,'replay')", rows)
    con.commit()


def ensure_outcomes(con, day: dt.date, frames: dict, refs: dict, scans: list[tuple[dict, str]], now: dt.datetime) -> None:
    """Fill payload.outcome for alerts of a settled day that lack it (live alerts)."""
    if not day_is_settled(day, now) or not frames:
        return
    start, end = ist_day_bounds(day)
    by_name = {s["name"]: s for s, _ in scans}
    with con.cursor() as cur:
        cur.execute("SELECT id, ts, scan, symbol, price, payload FROM alerts WHERE ts>=%s AND ts<%s AND NOT (COALESCE(payload,'{}'::jsonb) ? 'outcome')",
                    (start, end))
        todo = cur.fetchall()
        for aid, ts, name, sym, price, payload in todo:
            scan = by_name.get(name)
            side = scan.get("side") if scan else None
            df, ref = frames.get(sym), refs.get(sym)
            if not side or df is None or not ref or price is None:
                continue
            extra = _trade_payload(sym, df, ref, pd.Timestamp(ts).tz_convert("Asia/Kolkata"), side, float(price))
            if extra:
                extra["side"] = side
                cur.execute("UPDATE alerts SET payload = COALESCE(payload,'{}'::jsonb) || %s::jsonb WHERE id=%s",
                            (json.dumps(extra, default=float), aid))
    con.commit()


def merge_live_replay(rows: list[dict]) -> list[dict]:
    """Drop a replay row when a live row for the same scan+symbol sits within ±1 minute."""
    live = [(r["scan"], r["symbol"], r["ts"]) for r in rows if r.get("source") == "live"]
    out = []
    for r in rows:
        if r.get("source") == "replay" and any(s == r["scan"] and y == r["symbol"] and abs((t - r["ts"]).total_seconds()) <= 60
                                               for s, y, t in live):
            continue
        out.append(r)
    return out


def alerts_for_day(con, day: dt.date) -> list[dict]:
    start, end = ist_day_bounds(day)
    with con.cursor() as cur:
        cur.execute("SELECT ts, scan, symbol, price, payload, source FROM alerts WHERE ts>=%s AND ts<%s ORDER BY ts DESC", (start, end))
        rows = [{"ts": ts.astimezone(IST), "scan": sc, "symbol": sym, "price": pr, "payload": p or {}, "source": src}
                for ts, sc, sym, pr, p, src in cur.fetchall()]
    rows = merge_live_replay(rows)
    return [{"ts": r["ts"].isoformat(), "scan": r["scan"], "symbol": r["symbol"], "price": r["price"],
             "tf": r["payload"].get("tf"), "source": r["source"], "side": r["payload"].get("side"),
             "stop": r["payload"].get("stop"), "target": r["payload"].get("target"),
             "outcome": r["payload"].get("outcome")} for r in rows]


def scan_stats(con, days: int) -> list[dict]:
    """Per-scan hit rate and average returns over roughly the last N trading days."""
    since = dt.datetime.now(IST) - dt.timedelta(days=int(days * 7 / 5) + 3)
    with con.cursor() as cur:
        cur.execute("SELECT scan, payload->'outcome' FROM alerts WHERE ts>=%s AND payload ? 'outcome'", (since,))
        rows = cur.fetchall()
    agg: dict[str, dict] = {}
    for name, o in rows:
        s = agg.setdefault(name, {"scan": name, "fires": 0, "target": 0, "stop": 0, "r30": [], "r60": []})
        s["fires"] += 1
        if o.get("hit") in ("target", "stop"):
            s[o["hit"]] += 1
        for k in ("r30", "r60"):
            if o.get(k) is not None:
                s[k].append(float(o[k]))
    out = []
    for s in agg.values():
        decided = s["target"] + s["stop"]
        out.append({"scan": s["scan"], "fires": s["fires"],
                    "hit_rate": (s["target"] / decided * 100) if decided else None,
                    "avg_r30": (sum(s["r30"]) / len(s["r30"])) if s["r30"] else None,
                    "avg_r60": (sum(s["r60"]) / len(s["r60"])) if s["r60"] else None})
    return sorted(out, key=lambda x: x["scan"])
