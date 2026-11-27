"""Follow-up Telegram messages for digest alarms (the Inside open group), sent from the API's trades minute.

For the STARRED rows of a digest group: one note when the price has covered NEAR of the way from the entry to the
target, and the exit (target or stop) when the trade row settles. For the whole group: one quiet day-result message
at flat-by. Every message goes out once (`fu:{day}:{key}` in Redis) through the deferred queue `tg:defer:{day}` with
an empty odds key, so the same flush that sends the alarms sends it at once and records the delivery.
Pure builders first (`due`), the queue (`send_once`) and the loader (`run`) last."""
import json, html, logging, datetime as dt

log = logging.getLogger("followups")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NEAR = 0.8                # share of the way from the entry to the target that counts as "close to its target"
COST_PCT = 0.10           # round-trip costs counted in the day result, in percent of the trade
LATE_S = 15 * 60          # an exit that settled longer ago (the API was away) is marked done, never sent late
RESULT_LATE_S = 20 * 60   # the same for the day result after flat-by
TTL = 86400
_TARGET_WORD = {"prev_day_high": "PDH", "prev_day_low": "PDL", "prev_day_close": "prev close"}


def progress(entry: float, target: float, px: float) -> float:
    """Share of the way from the entry to the target the price has covered (negative when it went the other way)."""
    d = float(target) - float(entry)
    return (float(px) - float(entry)) / d if d else 0.0


def gain(side: str, entry: float, px: float) -> float:
    """The trade's move in percent, positive in its favour, before costs."""
    return (float(px) / float(entry) - 1) * 100 * (1 if side == "long" else -1)


def _sg(x: float) -> str:
    return ("+" if x >= 0 else "−") + f"{abs(x):.2f}"


def _hhmm(ts) -> str:
    if isinstance(ts, str):
        ts = dt.datetime.fromisoformat(ts)
    return ts.astimezone(IST).strftime("%H:%M")


def _target_word(scan: dict | None) -> str:
    return _TARGET_WORD.get((((scan or {}).get("trade") or {}).get("target") or {}).get("series"), "target")


def near_text(row: dict, meta: dict, px: float, at: dt.datetime, scan: dict | None) -> str:
    return (f"<b>{html.escape(row['symbol'])}</b> is close to its target\n"
            f"now {px:,.2f} · {_target_word(scan)} {row['target']:,.2f} · {html.escape(meta['scan'])}\n"
            f"{at:%H:%M}  ·  scanradar.tech")


def exit_text(row: dict, meta: dict, scan: dict | None) -> str:
    o = row["outcome"]
    when, pct = _hhmm(o["settled_at"]), _sg(gain(row["side"], row["entry"], o["exit_px"]))
    foot = f"{html.escape(meta['scan'])}  ·  scanradar.tech"
    if o["hit"] == "target":
        return f"<b>TARGET</b> · <b>{html.escape(row['symbol'])}</b>\n{_target_word(scan)} {row['target']:,.2f} reached at {when} · {pct} %\n{foot}"
    return f"<b>STOP</b> · <b>{html.escape(row['symbol'])}</b>\n{row['stop']:,.2f} hit at {when} · {pct} %\n{foot}"


def _tally(rows: list[dict]) -> dict:
    done = [r for r in rows if r.get("outcome") and r["outcome"].get("exit_px") is not None]
    hits = [r["outcome"].get("hit") for r in done]
    avg = sum(gain(r["side"], r["entry"], r["outcome"]["exit_px"]) for r in done) / len(done) - COST_PCT if done else None
    return {"n": len(rows), "target": hits.count("target"), "stop": hits.count("stop"),
            "flat": len(done) - hits.count("target") - hits.count("stop"), "open": len(rows) - len(done), "avg": avg}


def day_result_text(group: str, rows: list[dict], metas: dict, flat_by: str) -> str:
    """The group's day in three or four lines: every alarm's result, the average after costs, then the starred ones."""
    a = _tally(rows)
    stars = [r for r in rows if ((metas.get(r["ref"]) or {}).get("digest") or {}).get("star")]
    s = _tally(stars)
    out = [f"<b>{html.escape(group)} · day result</b>",
           f"{a['n']} alarms · {a['target']} target · {a['stop']} stopped · {a['flat']} flat at {flat_by}" + (f" · {a['open']} still open" if a["open"] else "")]
    if a["avg"] is not None:
        out.append(f"average {_sg(a['avg'])} % a trade after costs")
    if stars and len(stars) < len(rows) and s["avg"] is not None:
        out.append(f"★ strongest {s['n']}: {s['target']} target · {s['stop']} stopped · {_sg(s['avg'])} %")
    return "\n".join(out)


def due(rows: list[dict], metas: dict, prices: dict, at: dt.datetime, final: bool, flat_by: str, scans: dict) -> list[dict]:
    """What this minute owes, before the once-only check: [{"key", "text", "silent", "skip"?}].
    `rows`: the day's trade rows of digest alerts; `metas`: ref -> the alert (scan, digest); `prices`: symbol -> last close
    for the open starred rows; `final`: the minute is at or past flat-by. A "skip" item is marked done without a send."""
    out = []
    for r in rows:
        m = metas.get(r["ref"]) or {}
        if not (m.get("digest") or {}).get("star"):
            continue
        sc, o = scans.get(m.get("scan")), r.get("outcome")
        if o is None:
            px = prices.get(r["symbol"])
            if px is not None and progress(r["entry"], r["target"], px) >= NEAR:
                out.append({"key": f"{r['ref']}:near", "silent": False, "text": near_text(r, m, px, at, sc)})
        elif o.get("hit") in ("target", "stop") and o.get("exit_px") is not None and o.get("settled_at"):
            age = (at - dt.datetime.fromisoformat(o["settled_at"])).total_seconds()
            out.append({"key": f"{r['ref']}:exit", "silent": False, "skip": age > LATE_S, "text": exit_text(r, m, sc)})
    if final:
        groups: dict = {}
        for r in rows:
            g = ((metas.get(r["ref"]) or {}).get("digest") or {}).get("group")
            if g:
                groups.setdefault(g, []).append(r)
        close = dt.datetime.combine(at.date(), dt.time.fromisoformat(flat_by), at.tzinfo)
        for g, rs in groups.items():
            out.append({"key": f"group:{g}:result", "silent": True, "skip": (at - close).total_seconds() > RESULT_LATE_S,
                        "text": day_result_text(g, rs, metas, flat_by)})
    return out


def send_once(R, day, at: dt.datetime, items: list[dict]) -> int:
    """Queue each item that was never queued before; the number queued. The odds key is written empty first, so the
    deferred flush sends the message at once and plain (it has no trade row of its own to wait for)."""
    n = 0
    for it in items:
        mark = f"fu:{day}:{it['key']}"
        if not R.set(mark, "1", ex=TTL, nx=True):
            continue
        if it.get("skip"):
            continue
        ref = f"followup:{it['key']}"
        try:
            R.set(f"trades:{day}:{ref}", "", ex=TTL)
            R.rpush(f"tg:defer:{day}", json.dumps({"ref": ref, "text": it["text"], "ts": at.isoformat(), "silent": bool(it.get("silent"))}))
        except Exception as e:  # noqa: BLE001  not queued: take the mark back so the next minute tries again
            log.warning("follow-up %s not queued (%s); retrying next minute", it["key"], e)
            try:
                R.delete(mark)
            except Exception:  # noqa: BLE001
                pass
            continue
        n += 1
        try:
            R.expire(f"tg:defer:{day}", TTL)
        except Exception as e:  # noqa: BLE001  queued already; a missing TTL never warrants a second send
            log.warning("deferred list TTL not set: %s", e)
    return n


def _last_closes(con, day, at: dt.datetime, symbols: list[str]) -> dict:
    """symbol -> (bar start, close) of its last 1-minute bar before `at` today."""
    if not symbols:
        return {}
    start = dt.datetime.combine(day, dt.time(9, 15), IST)
    with con.cursor() as cur:
        cur.execute("SELECT DISTINCT ON (symbol) symbol, ts, close FROM candles_1m WHERE symbol = ANY(%s) AND ts >= %s AND ts < %s "
                    "ORDER BY symbol, ts DESC", (symbols, start, at))
        return {s: (ts, float(c)) for s, ts, c in cur.fetchall() if c is not None}


def _minute(ts) -> dt.datetime:
    if isinstance(ts, str):
        ts = dt.datetime.fromisoformat(ts)
    return ts.astimezone(IST).replace(second=0, microsecond=0)


def run(con, R, day, at: dt.datetime, flat_by: str, scans: dict) -> int:
    """The minute's follow-ups for the day's live digest alerts; the number queued."""
    import alerts as al
    import trades
    metas = {f"alert:{a['id']}": a for a in al.alerts_for_day(con, day) if a.get("digest") and a.get("source") == "live"}
    if not metas:
        return 0
    rows = [r for r in trades.rows_for_day(con, day) if r["ref"] in metas]
    watch = [r for r in rows if r.get("outcome") is None and (metas[r["ref"]].get("digest") or {}).get("star")]
    last = _last_closes(con, day, at, sorted({r["symbol"] for r in watch}))
    prices = {}
    for r in watch:                                           # a bar from before the alarm is not "now": the first minute has no price yet
        bar = last.get(r["symbol"])
        if bar and bar[0].astimezone(IST) >= _minute(r["ts"]):
            prices[r["symbol"]] = bar[1]
    final = at.time() >= dt.time.fromisoformat(flat_by)
    return send_once(R, day, at, due(rows, metas, prices, at, final, flat_by, scans))
