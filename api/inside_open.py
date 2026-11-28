"""The Inside open page (web/inside-open.html): one session of the Inside open alarm, as the page replays it.

`classify`, `funnel`, `assemble`, `messages` and `index` are pure (hand-built dicts and frames in, the route bodies
out); the loaders below them read Postgres and are exercised at deploy. A row is one alert of the digest group (live
and replay merged as on the Alarms board) with its trade row (levels, odds, size, outcome) and the symbol's 1-minute
bars, so the page can replay the session on its own clock. The Telegram texts come from the functions that send them
(core.rules.digest_text, followups). Nothing is written. All times IST; a minute index counts from 09:15."""
import logging, datetime as dt
import followups
from core import rules
from core.asof import view_at
from core.sizing import shares

log = logging.getLogger("inside_open")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
GROUP = "Inside open"
OPEN_MIN = 9 * 60 + 15
FIRE = 5                    # 09:20, the minute the alarm fires (used when a day has no alarm to read it from)
CHIPS = 10                  # sessions offered on the page, newest first
REC_DAYS = 250              # calendar days of alarms read for the chips and the record
COST = followups.COST_PCT   # round-trip costs counted in every "after costs" figure, in percent of the trade
PROBE = "RELIANCE"          # the symbol whose candles say a session exists (as /api/days does)
COMBOS = (("all", "every stock that passes"), ("mkt", "with the market only"),
          ("star", "strongest 3 each side"), ("both", "strongest 3, with the market"))
_SETTINGS = ("capital", "max_loss", "day_loss", "max_trades", "position_cap", "product", "flat_by")
_NO_FUNNEL = {"universe": None, "others": [], "breadth": None, "market": None}


# ---------- small helpers ----------
def _dt(x) -> dt.datetime:
    if isinstance(x, str):
        x = dt.datetime.fromisoformat(x)
    return x.astimezone(IST)


def midx(x) -> int:
    """Minutes since 09:15 of a datetime or ISO string."""
    t = _dt(x)
    return t.hour * 60 + t.minute - OPEN_MIN


def _hm_idx(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5]) - OPEN_MIN


def hhmm(m: int) -> str:
    t = OPEN_MIN + int(m)
    return f"{t // 60:02d}:{t % 60:02d}"


def _at(day: dt.date, m: int) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(9, 15), IST) + dt.timedelta(minutes=int(m))


def _r2(x):
    return None if x is None else round(float(x), 2)


def _ctx(ref: dict) -> dict:
    return {"prev_day_close": ref.get("prev_close", 0), "prev_day_high": ref.get("prev_high", 0), "prev_day_low": ref.get("prev_low", 0)}


def odds_view(odds: dict | None) -> dict | None:
    """A trade row's replay odds in the card's short shape; None without a Monte Carlo reading."""
    mc = (odds or {}).get("mc") or {}
    if mc.get("target_pct") is None:
        return None
    last = ((odds.get("past") or {}).get("last") or [])[-12:]
    return {"t": mc.get("target_pct"), "s": mc.get("stop_pct"), "n": mc.get("none_pct"), "verdict": odds.get("verdict"),
            "sessions": mc.get("sessions"), "last": [[str(x[0])[5:], x[1]] for x in last]}


# ---------- the funnel: who was in the universe at the fire minute, and why the others were left out ----------
def classify(first: dict, refs: dict, scan: dict, at) -> dict:
    """symbol -> (vote, why) from the bars the alarm saw at `at` (`first`: symbol -> 1-minute frame up to that minute).
    vote: the symbol's vote in the market's lean (None = outside the universe). why: "inside" or how it left yesterday's range."""
    tf = scan.get("timeframe", "1m")
    out = {}
    for sym, df in first.items():
        ref = refs.get(sym)
        if not ref or not ref.get("prev_close") or not ref.get("prev_high") or not ref.get("prev_low"):
            continue
        v = view_at(df, at)
        if v.empty:
            continue
        vote = rules.market_vote(scan, rules.join_sessions(None, rules.resample(v, tf)), _ctx(ref))
        hi, lo, ph, pl = float(v.high.max()), float(v.low.min()), float(ref["prev_high"]), float(ref["prev_low"])
        why = ("opened above PDH" if lo >= ph else "opened below PDL" if hi <= pl else
               "first candle crossed PDH" if hi >= ph else "first candle crossed PDL" if lo <= pl else "inside")
        out[sym] = (vote, why)
    return out


def _funnel(classes: dict, fired: set, scan: dict) -> dict:
    lean = float(((rules.digest(scan) or {}).get("market") or {}).get("lean", rules.LEAN))
    mk = rules.market([v for v, _ in classes.values()], lean)
    inside = {s for s, (v, _) in classes.items() if v is not None}
    others = [{"sym": s, "why": "inside, no alarm" if why == "inside" else why}
              for s, (v, why) in sorted(classes.items()) if v is not None and s not in fired]
    return {"universe": len(inside | set(fired)), "others": others,
            "breadth": mk["breadth"] if mk else None, "market": mk["side"] if mk else None}


def funnel(first: dict, refs: dict, scan: dict | None, at, fired: set) -> dict:
    """{"universe", "others": [{"sym", "why"}], "breadth", "market"}: every stock the alarm could have picked, the ones
    it did not (a stock that fired is never among them and always counts), and the market's lean read from the same bars."""
    if not scan:
        return dict(_NO_FUNNEL)
    return _funnel(classify(first, refs, scan, at), fired, scan)


# ---------- one row ----------
def _grade(bars: list, side: str, stop: float, target: float, fire: int, flat: int, final: bool, entry: float):
    """First touch on the bars after the alarm minute and before flat-by (a bar touching both counts as the stop):
    (hit, settled minute, exit price), or None while the trade is open. The rule of core.gate.settle, for a row
    the trade loop has not written."""
    long, last = side == "long", None
    for m, _o, h, l, c, _v in bars:
        if m <= fire or m >= flat:
            continue
        if (l <= stop) if long else (h >= stop):
            return "stop", m + 1, stop
        if (h >= target) if long else (l <= target):
            return "target", m + 1, target
        last = c
    if not final:
        return None
    return "none", flat, entry if last is None else last


def _row(a: dict, t: dict | None, bars: list, ref: dict, settings: dict, flat: int, final: bool) -> dict | None:
    dg = a["digest"]
    side = (t or {}).get("side") or a.get("side")
    entry = (t or {}).get("entry") if t else a.get("price")
    stop = (t or {}).get("stop") if t else a.get("stop")
    target = (t or {}).get("target") if t else a.get("target")
    if side not in ("long", "short") or entry is None or stop is None or target is None or not bars or not ref.get("prev_close"):
        return None
    entry, stop, target, long = float(entry), float(stop), float(target), side == "long"
    if (target - stop) * (1 if long else -1) <= 0:
        return None
    fire = midx(a["ts"])
    o = (t or {}).get("outcome")
    if o and o.get("settled_at") and o.get("exit_px") is not None:
        hit, x, xp = o.get("hit") or "none", hhmm(midx(o["settled_at"])), float(o["exit_px"])
    else:
        g = None if t else _grade(bars, side, stop, target, fire, flat, final, entry)
        hit, x, xp = (g[0], hhmm(g[1]), float(g[2])) if g else (None, None, None)
    risk = (entry - stop) if long else (stop - entry)
    reward = (target - entry) if long else (entry - target)
    rr = t["rr"] if t and t.get("rr") is not None else (reward / risk if risk > 0 else None)
    size = (t or {}).get("size") or shares(entry, stop, settings, side=side)
    f5 = [b for b in bars if b[0] < fire]
    vol = sum(b[5] for b in f5)
    tp = [(b[2] + b[3] + b[4]) / 3 for b in f5]
    vw = (sum(p * b[5] for p, b in zip(tp, f5)) / vol if vol > 0 else sum(tp) / len(tp)) if f5 else None
    lean = dg.get("market")
    return {"ref": f"alert:{a['id']}", "sym": a["symbol"], "side": side, "sector": ref.get("sector") or "OTHERS", "origin": a.get("source"),
            "pc": _r2(ref.get("prev_close")), "ph": _r2(ref.get("prev_high")), "pl": _r2(ref.get("prev_low")),
            "o5": _r2(f5[0][1]) if f5 else None, "h5": _r2(max(b[2] for b in f5)) if f5 else None,
            "l5": _r2(min(b[3] for b in f5)) if f5 else None, "c5": _r2(f5[-1][4]) if f5 else None, "vwap5": _r2(vw),
            "entry": _r2(entry), "stop": _r2(stop), "target": _r2(target), "rr": _r2(rr),
            "hit": hit, "x": x, "xp": _r2(xp),
            "gross": None if xp is None else round(((xp / entry - 1) if long else (1 - xp / entry)) * 100, 2),
            "astr": dg.get("strength"), "rank": dg.get("rank"), "of": dg.get("of"), "star": bool(dg.get("star")),
            "mkt": "" if lean in (None, "mixed") else "with" if lean == side else "against",
            "size": {k: size.get(k) for k in ("shares", "risk_inr", "position_inr", "limited_by")},
            "odds": odds_view((t or {}).get("odds")), "gate": (t or {}).get("gate"), "gate_reason": (t or {}).get("gate_reason"),
            "m": [b[0] for b in bars], "o": [_r2(b[1]) for b in bars], "h": [_r2(b[2]) for b in bars],
            "l": [_r2(b[3]) for b in bars], "c": [_r2(b[4]) for b in bars], "v": [int(b[5]) for b in bars]}


# ---------- the Telegram messages of the day ----------
def _delivery(d: dict | None) -> dict | None:
    if not d:
        return None
    return {"ok": bool(d.get("ok")), "at": _dt(d["sent_at"]).strftime("%H:%M:%S") if d.get("sent_at") else None, "error": d.get("error")}


def _trade_like(day: dt.date, row: dict) -> dict:
    """The row as followups reads a trade row: ref, symbol, side, levels and the outcome once settled."""
    out = None if row["hit"] is None else {"hit": row["hit"], "exit_px": row["xp"], "settled_at": _at(day, _hm_idx(row["x"])).isoformat()}
    return {"ref": row["ref"], "symbol": row["sym"], "side": row["side"], "entry": row["entry"], "stop": row["stop"],
            "target": row["target"], "outcome": out}


def messages(day: dt.date, items: list, scans: dict, flat_by: str, final: bool, deliveries: dict) -> list[dict]:
    """What the phone gets for the day, in time order: the quiet summary at the fire minute, for each STARRED row the
    note when a 1-minute close has covered followups.NEAR of the way to the target (never from a bar before the alarm,
    never once the row has settled) and its exit, and the quiet day result at flat-by once the day is final.
    `items`: (row, alert) pairs. Each message: {"m", "at", "kind", "text", "quiet", "delivery"}."""
    if not items:
        return []
    out = []

    def add(m, kind, text, quiet, ref):
        out.append({"m": int(m), "at": hhmm(m), "kind": kind, "text": text, "quiet": quiet, "delivery": _delivery(deliveries.get(ref))})

    fire = min(midx(a["ts"]) for _, a in items)
    sides, mk, label = {}, None, None
    for _row_, a in sorted(items, key=lambda x: x[1]["scan"]):
        sc, dg = scans.get(a["scan"]) or {}, a["digest"]
        d = rules.digest(sc) or {}
        s = sides.setdefault(a["scan"], {"side": sc.get("side") or a.get("side"), "title": d.get("title") or a["scan"], "rows": []})
        s["rows"].append({"symbol": a["symbol"], "price": float(a["price"]), "stop": a.get("stop"), "target": a.get("target"),
                          "rank": dg.get("rank"), "star": bool(dg.get("star"))})
        if mk is None and dg.get("market") and dg.get("breadth") is not None:
            mk, label = {"breadth": dg["breadth"], "side": dg["market"]}, (d.get("market") or {}).get("label")
    sent = next((f"alert:{a['id']}" for _, a in items if f"alert:{a['id']}" in deliveries), None)
    add(fire, "summary", rules.digest_text(GROUP, _at(day, fire), mk, label, list(sides.values())), True, sent)

    flat = _hm_idx(flat_by)
    for row, a in items:
        if not a["digest"].get("star"):
            continue
        sc, tl = scans.get(a["scan"]), _trade_like(day, row)
        done = None if row["hit"] is None else _hm_idx(row["x"])
        for m, c in zip(row["m"], row["c"]):
            if m < fire or m + 1 >= (done if done is not None else flat):
                continue
            if followups.progress(row["entry"], row["target"], c) >= followups.NEAR:
                add(m + 1, "near", followups.near_text(tl, a, c, _at(day, m + 1), sc), False, f"followup:{row['ref']}:near")
                break
        if row["hit"] in ("target", "stop"):
            add(done, row["hit"], followups.exit_text(tl, a, sc), False, f"followup:{row['ref']}:exit")
    if final:
        metas = {row["ref"]: a for row, a in items}
        add(flat, "result", followups.day_result_text(GROUP, [_trade_like(day, r) for r, _ in items], metas, flat_by), True,
            f"followup:group:{GROUP}:result")
    out.sort(key=lambda x: x["m"])
    return out


# ---------- the day ----------
def assemble(day: dt.date, *, alerts, trade_rows, bars, refs, funnel, settings, scans, deliveries, live: bool, final: bool,
             last: int | None = None) -> dict:
    """The body of GET /api/inside-open?day=. `alerts`: alerts_for_day rows; `trade_rows`: rows_for_day; `bars`: symbol ->
    [(minute, open, high, low, close, volume)]; `refs`: load_refs; `funnel`: funnel() or None; `last`: the last stored
    minute the loader saw. An alert of another group, without levels or without candles is not a row."""
    flat_by = settings["flat_by"]
    flat = _hm_idx(flat_by)
    by_ref = {t["ref"]: t for t in trade_rows}
    items = []
    for a in alerts:
        if (a.get("digest") or {}).get("group") != GROUP:
            continue
        row = _row(a, by_ref.get(f"alert:{a['id']}"), bars.get(a["symbol"]) or [], refs.get(a["symbol"]) or {}, settings, flat, final)
        if row is None:
            log.info("inside open %s: alert %s (%s) left out: no levels or no candles", day, a.get("id"), a.get("symbol"))
            continue
        items.append((row, a))
    items.sort(key=lambda x: (x[0]["side"] != "long", x[0]["sym"]))
    rows = [r for r, _ in items]
    fz = funnel or _NO_FUNNEL
    dg = next((a["digest"] for _, a in items if a["digest"].get("market") and a["digest"].get("breadth") is not None), None)
    origins = {r["origin"] for r in rows}
    seen = [r["m"][-1] for r in rows if r["m"]] + ([last] if last is not None else [])
    return {"day": day.isoformat(), "live": live, "final": final, "last": max(seen) if seen else None,
            "fire": min((midx(a["ts"]) for _, a in items), default=FIRE), "flat_by": flat_by, "close": flat,
            "settings": {k: settings.get(k) for k in _SETTINGS},
            "universe": fz.get("universe"), "others": fz.get("others") or [],
            "breadth": dg["breadth"] if dg else fz.get("breadth"), "market": dg["market"] if dg else fz.get("market"),
            "origin": None if not origins else origins.pop() if len(origins) == 1 else "mixed",
            "rows": rows, "messages": messages(day, items, scans, flat_by, final, deliveries)}


# ---------- the day chips and the record ----------
def _stats(rows: list[dict]) -> dict:
    done = [r for r in rows if r.get("hit") is not None and r.get("gross") is not None]
    n = len(done)
    if not n:
        return {"n": 0, "target": None, "stop": None, "net": None}
    return {"n": n, "target": round(100 * sum(r["hit"] == "target" for r in done) / n),
            "stop": round(100 * sum(r["hit"] == "stop" for r in done) / n),
            "net": round(sum(r["gross"] for r in done) / n - COST, 2)}


def index(days: list[dict]) -> dict:
    """{"days": the chips, newest first, at most CHIPS; "rec": the record over the settled sessions, None without one}.
    `days`: [{"day", "live", "breadth", "market", "rows": [{"side", "hit", "gross", "star", "mkt"}]}] in any order."""
    days = sorted(days, key=lambda d: d["day"], reverse=True)
    chips = []
    for d in days[:CHIPS]:
        rs, date = d["rows"], dt.date.fromisoformat(d["day"])
        chips.append({"day": d["day"], "label": f"{date:%a} {date.day} {date:%b}", "live": bool(d.get("live")), "n": len(rs),
                      "long": sum(r["side"] == "long" for r in rs), "target": sum(r.get("hit") == "target" for r in rs),
                      "stop": sum(r.get("hit") == "stop" for r in rs), "net": None if d.get("live") else _stats(rs)["net"],
                      "breadth": d.get("breadth"), "market": d.get("market")})
    sess = [d for d in days if not d.get("live") and d["rows"]]
    if not sess:
        return {"days": chips, "rec": None}
    every = [r for d in sess for r in d["rows"]]
    done = [r for r in every if r.get("hit") is not None and r.get("gross") is not None]
    pick = {"all": lambda r: True, "mkt": lambda r: r.get("mkt") == "with", "star": lambda r: bool(r.get("star")),
            "both": lambda r: bool(r.get("star")) and r.get("mkt") == "with"}
    combos = {}
    for key, label in COMBOS:
        rs = [r for r in every if pick[key](r)]
        per = len(rs) / len(sess)
        combos[key] = {"label": label, "per_day": round(per) if per >= 10 else round(per, 1), **_stats(rs)}
    first, lastd = dt.date.fromisoformat(sess[-1]["day"]), dt.date.fromisoformat(sess[0]["day"])
    return {"days": chips,
            "rec": {"sessions": len(sess), "from": f"{first.day} {first:%b}", "to": f"{lastd.day} {lastd:%b}",
                    "min": min(len(d["rows"]) for d in sess), "max": max(len(d["rows"]) for d in sess),
                    "gross": round(sum(r["gross"] for r in done) / len(done), 2) if done else None,
                    "long": _stats([r for r in every if r["side"] == "long"]),
                    "short": _stats([r for r in every if r["side"] == "short"]), "combos": combos}}


# ---------- loaders ----------
_CLASSES: dict = {}        # day -> classify() once the first five minutes are complete (they never change afterwards)


def _session(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time(9, 15), IST)
    return start, start + dt.timedelta(hours=6, minutes=16)


def _bars(con, day: dt.date, syms: list[str]) -> dict:
    if not syms:
        return {}
    start, end = _session(day)
    out: dict = {}
    with con.cursor() as cur:
        cur.execute("SELECT symbol, ts, open, high, low, close, volume FROM candles_1m WHERE symbol = ANY(%s) AND ts >= %s AND ts < %s "
                    "ORDER BY symbol, ts", (syms, start, end))
        for s, ts, o, h, l, c, v in cur.fetchall():
            if o is None or h is None or l is None or c is None:
                continue
            out.setdefault(s, []).append((midx(ts), float(o), float(h), float(l), float(c), int(v or 0)))
    return out


def _last_minute(con, day: dt.date) -> int | None:
    """The last stored minute of the session (read on the probe symbol), None when the day has no candles."""
    start, end = _session(day)
    with con.cursor() as cur:
        cur.execute("SELECT max(ts) FROM candles_1m WHERE symbol = %s AND ts >= %s AND ts < %s", (PROBE, start, end))
        ts = cur.fetchone()[0]
    return None if ts is None else midx(ts)


def _funnel_for(con, day: dt.date, now: dt.datetime, refs: dict, scans: dict, fired: set, settled: bool) -> dict | None:
    import replay as rp
    scan = next((s for s in scans.values() if (rules.digest(s) or {}).get("group") == GROUP), None)
    at = dt.datetime.combine(day, dt.time(9, 20), IST)
    if scan is None or now < at + dt.timedelta(seconds=10):
        return None                                    # the first five minutes are not all stored yet
    classes = _CLASSES.get(day)
    if classes is None:
        import pandas as pd
        classes = classify(rp.load_day(con, day, upto=at), refs, scan, pd.Timestamp(at))
        if settled or now >= at + dt.timedelta(minutes=2):
            while len(_CLASSES) >= 40:
                _CLASSES.pop(next(iter(_CLASSES)))
            _CLASSES[day] = classes
    return _funnel(classes, fired, scan)


def day_body(con, day: dt.date, now: dt.datetime) -> dict:
    """GET /api/inside-open?day=: everything the page needs to replay `day` (a live day: up to the last stored minute)."""
    import alerts as al
    import trades as tr
    import deliveries as dv
    import replay as rp
    settled = rp.day_is_settled(day, now)
    alerts = [a for a in al.alerts_for_day(con, day) if (a.get("digest") or {}).get("group") == GROUP]
    syms = sorted({a["symbol"] for a in alerts})
    settings = tr.get_settings(con)
    scans = {s["name"]: s for s, _ in al.scan_files()}
    refs = rp.load_refs(con, day)
    last = _last_minute(con, day)
    today = day == now.astimezone(IST).date()
    final = settled or (today and now.astimezone(IST).time() >= dt.time.fromisoformat(settings["flat_by"]))
    return assemble(day, alerts=alerts, trade_rows=tr.rows_for_day(con, day), bars=_bars(con, day, syms), refs=refs,
                    funnel=_funnel_for(con, day, now, refs, scans, set(syms), settled), settings=settings, scans=scans,
                    deliveries=dv.for_day(con, day), live=today and not settled and last is not None, final=final, last=last)


def index_body(con, now: dt.datetime) -> dict:
    """GET /api/inside-open/days: the day chips and the record, from the stored alarms and their trade rows."""
    import alerts as al
    import replay as rp
    today = now.astimezone(IST).date()
    since = dt.datetime.combine(today - dt.timedelta(days=REC_DAYS), dt.time(0), IST)
    names = [s["name"] for s, _ in al.scan_files() if (rules.digest(s) or {}).get("group") == GROUP]
    sql, args = "SELECT id, ts, scan, symbol, price, payload, source FROM alerts WHERE ts >= %s AND payload->'digest'->>'group' = %s", [since, GROUP]
    if names:                                           # the group's scans are known: read them through the (scan, ts) index
        sql, args = sql + " AND scan = ANY(%s)", args + [names]
    with con.cursor() as cur:
        cur.execute(sql + " ORDER BY ts", args)
        raw = [{"id": i, "ts": ts.astimezone(IST), "scan": sc, "symbol": sym, "price": pr, "payload": p or {}, "source": src}
               for i, ts, sc, sym, pr, p, src in cur.fetchall()]
        by_day: dict = {}
        for r in raw:
            by_day.setdefault(r["ts"].date(), []).append(r)
        by_day = {d: al.merge_live_replay(rs) for d, rs in by_day.items()}
        refs = [f"alert:{r['id']}" for rs in by_day.values() for r in rs]
        trs = {}
        if refs:
            cur.execute("SELECT ref, side, entry, outcome FROM trade_rows WHERE day >= %s AND ref = ANY(%s)", (since.date(), refs))
            trs = {ref: (side, entry, o) for ref, side, entry, o in cur.fetchall()}
    days = []
    for d, rs in by_day.items():
        rows, dg0 = [], None
        for r in rs:
            dg = r["payload"].get("digest") or {}
            side_t, entry, o = trs.get(f"alert:{r['id']}", (None, None, None))
            side = side_t or r["payload"].get("side")
            if side not in ("long", "short"):
                continue
            hit = gross = None
            if o and o.get("settled_at") and o.get("exit_px") is not None and entry:
                hit = o.get("hit") or "none"
                gross = round(followups.gain(side, float(entry), float(o["exit_px"])), 2)
            lean = dg.get("market")
            rows.append({"side": side, "hit": hit, "gross": gross, "star": bool(dg.get("star")),
                         "mkt": "" if lean in (None, "mixed") else "with" if lean == side else "against"})
            if dg0 is None and dg.get("market"):
                dg0 = dg
        days.append({"day": d.isoformat(), "live": d == today and not rp.day_is_settled(d, now), "rows": rows,
                     "breadth": (dg0 or {}).get("breadth"), "market": (dg0 or {}).get("market")})
    if today not in by_day and not rp.day_is_settled(today, now) and _last_minute(con, today) is not None:
        days.append({"day": today.isoformat(), "live": True, "rows": [], "breadth": None, "market": None})
    return index(days)
