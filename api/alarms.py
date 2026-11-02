"""The Alarms board: one column per setup, the stocks stacking under it as they fire.

`assemble` is pure (hand-built dicts in, the /api/alarms body out); the loaders below it read Postgres/Redis and
are exercised at deploy. The board's scan list lives here, not in the scan files (their hash drives the replay rows).
All times IST; a row's `ts` is the bar-close minute the alert / trade row already carries."""
import os, json, logging, datetime as dt

log = logging.getLogger("alarms")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

DEFAULT_SCANS = ["PDH break", "PDL break", "PDH fade", "PDL fade", "B0101"]
BOARD_SCANS = [s.strip() for s in os.environ.get("ALARMS_SCANS", ",".join(DEFAULT_SCANS)).split(",") if s.strip()]
RECORD_DAYS = 20
MIN_RECORD_N = 20                                   # under this many fires / decided rows a record is not shown
INDEX_SYMBOL = "NIFTY50"

# scan name -> (column key, column name, sub, side)   (side None = take it from the scan file)
_FIXED = {
    "PDH break": ("pdh_brk", "PDH breakout", "crossed above yesterday's high", "short"),
    "PDL break": ("pdl_brk", "PDL breakdown", "crossed below yesterday's low", "long"),
    "PDH fade": ("pdh_fade", "PDH fade", "short · first 5m close back below", "short"),
    "PDL fade": ("pdl_fade", "PDL fade", "long · first 5m close back above", "long"),
}
_BREAK_TEXT = {"pdh_brk": "above PDH", "pdl_brk": "below PDL"}
_KIND_ORDER = {"break": 0, "alarm": 1, "desk": 2, "exit": 3}


# ---------- pure ----------
def columns(board_scans: list[str], scan_sides: dict[str, str]) -> list[dict]:
    """The board's columns in order: PDH breakout, PDL breakdown, Patterns, PDH fade, PDL fade, then the other board scans.
    A board scan that is not in `scan_sides` (no such file in scans/) is skipped with a log line."""
    present = []
    for name in board_scans:
        if name not in scan_sides:
            log.warning("board scan %r has no scan file; skipped", name)
            continue
        present.append(name)

    def col(name):
        if name in _FIXED:
            key, nm, sub, side = _FIXED[name]
            return {"key": key, "name": nm, "sub": sub, "side": side, "scan": name}
        return {"key": f"scan:{name}", "name": name, "sub": "rule scan", "side": scan_sides.get(name), "scan": name}

    by_key = {c["key"]: c for c in map(col, present)}
    out = [by_key.pop(k) for k in ("pdh_brk", "pdl_brk") if k in by_key]
    out.append({"key": "pat", "name": "Patterns", "sub": "trade-grade triggers", "side": None, "scan": None})
    out += [by_key.pop(k) for k in ("pdh_fade", "pdl_fade") if k in by_key]
    out += list(by_key.values())
    return out


def _dt(s) -> dt.datetime | None:
    if s is None:
        return None
    if isinstance(s, dt.datetime):
        return s if s.tzinfo else s.replace(tzinfo=IST)
    d = dt.datetime.fromisoformat(str(s))
    return d if d.tzinfo else d.replace(tzinfo=IST)


def _hhmm(s) -> str | None:
    """'HH:MM' from an iso timestamp, a datetime, or an 'HH:MM' string."""
    if s is None:
        return None
    if isinstance(s, dt.datetime):
        return s.astimezone(IST).strftime("%H:%M")
    s = str(s)
    if "T" in s or " " in s:
        return _dt(s).astimezone(IST).strftime("%H:%M")
    return s[:5]


def _px(x) -> str:
    """A price for an event text; a missing number reads as a dash (alerts.price and a trade row's entry are nullable)."""
    return "—" if x is None else f"{x:,.2f}"


def _sell(side) -> str:
    return "SELL" if side == "short" else "BUY"


def _rr(price, stop, target):
    try:
        risk = abs(float(price) - float(stop))
        return round(abs(float(target) - float(price)) / risk, 2) if risk else None
    except (TypeError, ValueError):
        return None


def _outcome(trade: dict | None, at: dt.datetime) -> dict | None:
    """The trade row's outcome, only once it has settled by `at` (later facts never leak into an earlier minute)."""
    o = (trade or {}).get("outcome")
    if not o or o.get("settled_at") is None:
        return None
    s = _dt(o["settled_at"])
    if s is None or s > at:
        return None
    return {"hit": o.get("hit"), "r60": o.get("r60"), "settled_at": o["settled_at"], "exit_px": o.get("exit_px")}


def _event_text(row: dict, col_key: str) -> tuple[str, str]:
    """(kind, text) of the event that put this row on the board."""
    if col_key in _BREAK_TEXT:
        word, st = _BREAK_TEXT[col_key], row.get("story")
        if st and st.get("line") is not None:
            pct = st.get("run_pct")
            return "break", (f"{word} {_px(st['line'])} at {_hhmm(st.get('break_at'))} · "
                             f"ran to {_px(st.get('run_px'))} ({'—' if pct is None else f'{pct:+.1f}'} %)")
        return "break", word
    return "alarm", f"{row['setup']} · {_sell(row['side'])} at {_px(row['price'])}"


def assemble(day: dt.date, at: dt.datetime, *, alerts, trade_rows, desk_calls, deliveries, board_scans, scan_sides,
             records, breadth, index, telegram_on: bool, live: bool, pending: bool, patterns_total: int,
             pattern_origins: dict[str, str] | None = None) -> dict:
    """The /api/alarms body. Everything is cut at `at`; rows ascend by ts (the tab puts the newest on top).
    Every row carries `origin`: "replay" when the alarm was computed on replay (it never rang live), else "live";
    alert rows from the alert's `source`, pattern rows from `pattern_origins` (trade-row ref -> patterns.source), default live."""
    pattern_origins = pattern_origins or {}
    at = _dt(at)
    cols = columns(board_scans, scan_sides)
    col_of = {c["scan"]: c for c in cols if c["scan"]}
    trade_by_ref = {t["ref"]: t for t in trade_rows}
    verdict_by_ref = {}
    for d in desk_calls:
        out = d.get("output") or {}
        t = _dt(d["ts"])
        if out.get("call") and t is not None and t <= at:
            verdict_by_ref[d["ref"]] = {"call": out["call"], "confidence": out.get("confidence"), "at": _hhmm(d["ts"]), "_ts": t}

    rows, folded_alerts, folded_scans = [], 0, set()
    for a in alerts:
        t = _dt(a["ts"])
        if t is None or t > at:
            continue
        col = col_of.get(a["scan"])
        if col is None:
            if a["scan"] not in board_scans:
                folded_alerts += 1
                folded_scans.add(a["scan"])
            continue
        ref = f"alert:{a['id']}"
        tr = trade_by_ref.get(ref)
        stop = tr["stop"] if tr and tr.get("stop") is not None else a.get("stop")
        target = tr["target"] if tr and tr.get("target") is not None else a.get("target")
        rows.append(_row(ref, col["key"], a["ts"], a["symbol"], a.get("side") or col["side"], a["scan"], "alert",
                         a.get("price"), stop, target, tr, a.get("story"), verdict_by_ref, deliveries, at,
                         "replay" if a.get("source") == "replay" else "live"))
    n_pat = 0
    for tr in trade_rows:
        if tr.get("source") != "pattern":
            continue
        t = _dt(tr["ts"])
        if t is None or t > at:
            continue
        n_pat += 1
        rows.append(_row(tr["ref"], "pat", tr["ts"], tr["symbol"], tr.get("side"), tr.get("label"), "pattern",
                         tr.get("entry"), tr.get("stop"), tr.get("target"), tr, None, verdict_by_ref, deliveries, at,
                         "replay" if pattern_origins.get(tr["ref"]) == "replay" else "live"))
    rows.sort(key=lambda r: (_dt(r["ts"]), r["ref"]))

    # one timeline per symbol: its break, alarms, desk calls and exits, in time order
    events: dict[str, list[dict]] = {}
    for r in rows:
        ev = events.setdefault(r["symbol"], [])
        kind, text = _event_text(r, r["col"])
        st = r["story"] if kind == "break" else None
        ev.append({"at": _hhmm(st["break_at"]) if st and st.get("break_at") else _hhmm(r["ts"]), "kind": kind, "text": text})
        v = verdict_by_ref.get(r["ref"])
        if v:
            ev.append({"at": v["at"], "kind": "desk", "text": " ".join(["desk", str(v["call"]).upper()] + ([] if v["confidence"] is None else [str(v["confidence"])]))})
        o = r["outcome"]
        if o and o.get("exit_px") is not None:
            word = {"target": "target", "stop": "stop"}.get(o.get("hit"), "flat")
            ev.append({"at": _hhmm(o["settled_at"]), "kind": "exit", "text": f"{word} at {_px(o['exit_px'])}"})
    for sym, ev in events.items():
        ev.sort(key=lambda e: (e["at"], _KIND_ORDER[e["kind"]]))
    for r in rows:
        r["timeline"] = [dict(e) for e in events[r["symbol"]]]

    out_cols = [{**c, "record": (records or {}).get(c["key"]) or {"text": "no record yet"}} for c in cols]
    return {"day": day.isoformat(), "at": at.astimezone(IST).strftime("%H:%M"), "live": live,
            "telegram": "on" if telegram_on else "not set up", "index": index, "breadth": breadth,
            "columns": out_cols, "rows": rows,
            "folded": {"alerts": folded_alerts, "scans": len(folded_scans), "patterns": max(0, patterns_total - n_pat)},
            "pending": pending}


def _row(ref, col, ts, symbol, side, setup, source, price, stop, target, tr, story, verdict_by_ref, deliveries, at,
         origin="live") -> dict:
    odds = (tr or {}).get("odds") or None
    size = (tr or {}).get("size") or {}
    v = verdict_by_ref.get(ref)
    d = deliveries.get(ref)
    if d is not None and (_dt(d.get("sent_at")) is None or _dt(d["sent_at"]) > at):
        d = None                                        # not sent yet as of `at`: a replay at the fire minute must not show "sent"
    return {"ref": ref, "col": col, "ts": ts, "symbol": symbol, "side": side, "setup": setup, "source": source, "origin": origin,
            "price": price, "stop": stop, "target": target,
            "rr": tr["rr"] if tr and tr.get("rr") is not None else _rr(price, stop, target),
            "odds": odds, "past": list(((odds or {}).get("past") or {}).get("last") or [])[-12:],
            "shares": size.get("shares"), "risk_inr": size.get("risk_inr"),
            "gate": (tr or {}).get("gate"), "gate_reason": (tr or {}).get("gate_reason"),
            "desk": {k: v[k] for k in ("call", "confidence", "at")} if v else None,
            "outcome": _outcome(tr, at), "story": story, "delivery": d}


def break_record(rows: list[dict], break_scan: str, fade_scan: str, days: int) -> dict | None:
    """rows {ts, scan, symbol} (ts a datetime) -> {per_day, fade_pct, n, days}: break fires per trading day seen and the share of
    (day, symbol) break pairs that had a fade the same day. None when there are no break rows."""
    breaks = [(_dt(r["ts"]).astimezone(IST).date(), r["symbol"]) for r in rows if r["scan"] == break_scan]
    if not breaks:
        return None
    fades = {(_dt(r["ts"]).astimezone(IST).date(), r["symbol"]) for r in rows if r["scan"] == fade_scan}
    pairs = set(breaks)
    seen_days = {d for d, _ in breaks}
    return {"per_day": len(breaks) / len(seen_days), "fade_pct": len(pairs & fades) / len(pairs) * 100,
            "n": len(breaks), "days": days}


def record_text(col_key: str, rec: dict | None) -> str:
    """The header line under a column: '54 % hit · +0.3 % @60m · n 520', '137/day · 31 % faded', '62 % T1 · n 1,420'."""
    if not rec or (rec.get("n") or 0) < MIN_RECORD_N:
        return "no record yet"
    if col_key in _BREAK_TEXT:
        return f"{rec['per_day']:.0f}/day · {rec['fade_pct']:.0f} % faded"
    if rec.get("hit") is None:
        return "no record yet"
    if col_key == "pat":
        return f"{rec['hit']:.0f} % T1 · n {rec['n']:,}"
    avg = f" · {rec['avg_r']:+.1f} % @60m" if rec.get("avg_r") is not None else ""
    return f"{rec['hit']:.0f} % hit{avg} · n {rec['n']:,}"


def _pooled_t1(stats: dict) -> dict | None:
    """All pattern kinds pooled: T1 hit rate weighted by decided rows, n = decided rows."""
    n = sum(s.get("decided_t1") or 0 for s in (stats or {}).values())
    if not n:
        return None
    hit = sum((s.get("hit_rate_t1") or 0) * (s.get("decided_t1") or 0) for s in stats.values()) / n
    return {"n": n, "hit": hit}


# ---------- loaders (DB / Redis) ----------
def scan_sides() -> dict[str, str]:
    import alerts as al
    return {s["name"]: s.get("side") for s, _ in al.scan_files()}


_REC: dict = {}          # {"until": datetime, "val": records dict}; the board asks on every slider step and refresh


def records(con) -> dict[str, dict]:
    """Column key -> its record dict (numbers + `text`): the scans from the rail's stats, the break columns from the PD alert rows,
    Patterns from the pooled T1 hit rate, all over the last RECORD_DAYS sessions. Cached for 10 minutes; the scan and pattern
    stats come from `live_desk.setup_stats` (its own 10-minute cache)."""
    import alerts as al
    import live_desk as ld
    now = dt.datetime.now(IST)
    if _REC.get("until") and _REC["until"] > now:
        return _REC["val"]
    pat, scan = ld.setup_stats(con, now)
    out: dict[str, dict] = {}
    key_of = {name: v[0] for name, v in _FIXED.items()}
    for name, s in scan.items():
        key = key_of.get(name, f"scan:{name}")
        if key in _BREAK_TEXT:
            continue
        out[key] = {"n": s["fires"], "hit": s["hit_rate"], "avg_r": s["avg_r60"], "days": RECORD_DAYS}
    pooled = _pooled_t1(pat)
    if pooled:
        out["pat"] = {**pooled, "days": RECORD_DAYS}
    since = now - dt.timedelta(days=int(RECORD_DAYS * 7 / 5) + 3)
    with con.cursor() as cur:
        cur.execute("SELECT ts, scan, symbol, source FROM alerts WHERE ts >= %s AND scan = ANY(%s)", (since, list(_FIXED)))
        rows = [{"ts": ts.astimezone(IST), "scan": sc, "symbol": sym, "source": src} for ts, sc, sym, src in cur.fetchall()]
    rows = al.merge_live_replay(rows)
    for brk, fade in (("PDH break", "PDH fade"), ("PDL break", "PDL fade")):
        rec = break_record(rows, brk, fade, RECORD_DAYS)
        if rec:
            out[_FIXED[brk][0]] = rec
    for key, rec in out.items():
        rec["text"] = record_text(key, rec)
    _REC.update(until=now + dt.timedelta(minutes=10), val=out)
    return out


def index_fresh(ts_iso, now: dt.datetime, max_age_s: int = 180) -> bool:
    """True when the index's last tick (`ts_iso`) is at most `max_age_s` seconds before `now`; a missing or unparsable ts is stale."""
    try:
        t = _dt(ts_iso)
    except (TypeError, ValueError):
        return False
    return t is not None and (now - t).total_seconds() <= max_age_s


def index_at(con, R, day: dt.date, at: dt.datetime, live: bool) -> dict | None:
    """NIFTY 50 at the board's minute: live from Redis `index:NIFTY50`, a replay day from the last stored `index_1m` close at or before `at`
    (previous close = the last close of the previous stored session). None when nothing is stored."""
    if live:
        try:
            j = json.loads(R.get(f"index:{INDEX_SYMBOL}") or "null")
        except (TypeError, ValueError):
            j = None
        if j and j.get("ltp") is not None:
            if not index_fresh(j.get("ts"), at):      # a stalled index feed reads as no index, not as an old price
                return None
            return {"symbol": "NIFTY 50", "ltp": j["ltp"], "prev_close": j.get("prev_close"), "pct": j.get("pct"), "at": _hhmm(j.get("ts"))}
    start = dt.datetime.combine(day, dt.time(0), IST)
    with con.cursor() as cur:
        cur.execute("SELECT ts, close FROM index_1m WHERE symbol=%s AND ts>=%s AND ts<=%s ORDER BY ts DESC LIMIT 1",
                    (INDEX_SYMBOL, start, min(at, start + dt.timedelta(days=1))))
        last = cur.fetchone()
        if last is None:
            return None
        cur.execute("SELECT close FROM index_1m WHERE symbol=%s AND ts<%s ORDER BY ts DESC LIMIT 1", (INDEX_SYMBOL, start))
        prev = cur.fetchone()
    ltp, prev_close = float(last[1]), (float(prev[0]) if prev else None)
    return {"symbol": "NIFTY 50", "ltp": ltp, "prev_close": prev_close,
            "pct": round((ltp / prev_close - 1) * 100, 2) if prev_close else None, "at": _hhmm(last[0])}


def breadth_at(con, R, day: dt.date, at: dt.datetime, live: bool) -> dict | None:
    """{bulls, bears, up: [[sector, pct]]*2, down: [[sector, pct]]*2} from the ranking snapshot: Redis `scan:latest` live, else the
    replay snapshot (shared with /api/replay through its Redis cache key)."""
    import replay as rp
    snap = None
    if live:
        try:
            snap = json.loads(R.get("scan:latest") or "null")
        except (TypeError, ValueError):
            snap = None
    else:
        key = f"replay:{day}:{at.strftime('%H:%M')}"
        try:
            snap = json.loads(R.get(key) or "null")
        except (TypeError, ValueError):
            snap = None
        if snap is None:
            try:
                snap = rp.snapshot_at(con, day, at)
            except rp.ReplayError:
                return None
            ttl = rp.cache_ttl(day, at, dt.datetime.now(IST))
            if ttl:
                R.set(key, json.dumps(snap, default=float), ex=ttl)
    if not snap:
        return None
    sec = sorted((snap.get("sectors") or {}).items(), key=lambda kv: kv[1], reverse=True)
    return {"bulls": snap.get("n_bull"), "bears": snap.get("n_bear"),
            "up": [[k, round(v, 2)] for k, v in sec[:2]], "down": [[k, round(v, 2)] for k, v in sec[::-1][:2]]}


def _desk_verdicts(con, day: dt.date) -> list[dict]:
    with con.cursor() as cur:
        cur.execute("SELECT ts, ref, output FROM desk_calls WHERE kind='verdict' AND day=%s AND output IS NOT NULL ORDER BY ts, id", (day,))
        out = [{"ts": ts, "ref": ref, "output": output} for ts, ref, output in cur.fetchall()]
    for r in out:
        r["ts"] = r["ts"].astimezone(IST).isoformat()
    return out


def board(con, R, day: dt.date, at: dt.datetime, live: bool, pending: bool) -> dict:
    """Load everything the board needs for `day` and cut it at `at` (see `assemble`)."""
    import alerts as al
    import trades as tr
    import deliveries as dv
    sides = scan_sides()
    with con.cursor() as cur:
        cur.execute("SELECT count(*) FROM patterns WHERE day=%s AND triggered_at IS NOT NULL AND triggered_at <= %s AND superseded_at IS NULL",
                    (day, at))
        patterns_total = cur.fetchone()[0]
    return assemble(day, at, alerts=al.alerts_for_day(con, day), trade_rows=tr.rows_for_day(con, day, at),
                    desk_calls=_desk_verdicts(con, day), deliveries=dv.for_day(con, day),
                    board_scans=BOARD_SCANS, scan_sides=sides, records=records(con),
                    breadth=breadth_at(con, R, day, at, live), index=index_at(con, R, day, at, live),
                    telegram_on=bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
                    live=live, pending=pending, patterns_total=patterns_total, pattern_origins=pattern_origins(con, day))


def pattern_origins(con, day: dt.date) -> dict[str, str]:
    """Trade-row ref of each of the day's patterns -> its `source` (live | replay), the ref built as live_desk.pattern_ref does."""
    import live_desk as ld
    with con.cursor() as cur:
        cur.execute("SELECT symbol, kind, formed_at, source FROM patterns WHERE day=%s", (day,))
        recs = cur.fetchall()
    return {ld.pattern_ref({"symbol": sym, "kind": kind, "formed_at": _dt(formed).astimezone(IST)}): src
            for sym, kind, formed, src in recs if formed is not None}
