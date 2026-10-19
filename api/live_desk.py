"""Live desk loop (API process): every minute during the session, verdicts for new
pattern triggers and rule alerts, a brief every 15 minutes, the review at 15:40.
Runs in a worker thread (network-bound); one Telegram per verdict."""
import os, json, logging, asyncio, datetime as dt
import pandas as pd
import desk
import patterns as pt
import alerts as al
from replay import load_refs, session_bounds, COLS as CANDLE_COLS
from core.calendar import is_trading_day
from core.analytics import analyse
from core.backtest import outcome as grade

log = logging.getLogger("live_desk")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
START, END = dt.time(9, 20), dt.time(15, 45)
MIN_CONF = float(os.environ.get("PATTERN_MIN_CONF", "40"))
REVIEW_AT = dt.time(15, 40)
_stats_cache: dict = {}


def should_run(now: dt.datetime) -> bool:
    n = now.astimezone(IST)
    return is_trading_day(n.date()) and START <= n.time() <= END


def brief_due(at: dt.datetime, every: int = desk.BRIEF_EVERY_MIN) -> bool:
    t = at.astimezone(IST)
    if not (dt.time(9, 30) <= t.time() <= dt.time(15, 15)):
        return False
    return (t.hour * 60 + t.minute - (9 * 60 + 15)) % every == 0


def pattern_ref(r: dict) -> str:
    return f"pattern:{r['symbol']}|{r['kind']}|{desk._hhmm(r['formed_at'])}"


def gate(score, rr) -> bool:
    return (score or 0) >= desk.MIN_SCORE and (rr or 0) >= desk.MIN_RR


def stale(trigger_at: dt.datetime, at: dt.datetime, max_age: int = desk.MAX_AGE_MIN) -> bool:
    """A trigger older than max_age minutes (restart backlog, day preparation) gets no verdict."""
    return (at - trigger_at).total_seconds() > max_age * 60


def skip_row(cur, day, at, ref, symbol, why: str, trigger: dict | None = None) -> None:
    desk.store(cur, {"day": day, "kind": "verdict", "ts": at, "ref": ref, "symbol": symbol, "context": {"trigger": trigger or {}}, "error": why})


def load_symbol(con, day, symbol: str, upto: dt.datetime) -> pd.DataFrame:
    start, end = session_bounds(day)
    with con.cursor() as cur:
        cur.execute("SELECT symbol, ts, open, high, low, close, volume, oi, delta FROM candles_1m WHERE symbol=%s AND ts>=%s AND ts<%s AND ts<=%s ORDER BY ts",
                    (symbol, start, end, upto))
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=CANDLE_COLS)
    if not df.empty:
        df["ts"] = pd.to_datetime(df.ts, utc=True).dt.tz_convert("Asia/Kolkata")
    return df.drop(columns="symbol")


def lots_map(con) -> dict:
    with con.cursor() as cur:
        cur.execute("SELECT symbol, lot_size FROM instruments")
        return {s: int(l or 0) for s, l in cur.fetchall()}


def setup_stats(con, now: dt.datetime) -> tuple[dict, dict]:
    """Per-kind pattern stats and per-scan alert stats, refreshed every 10 minutes."""
    if _stats_cache.get("until", dt.datetime.min.replace(tzinfo=IST)) > now:
        return _stats_cache["pat"], _stats_cache["scan"]
    try:
        pat = pt.stats(con, 20)
    except Exception:  # noqa: BLE001
        pat = {}
    try:
        scan = {s["scan"]: s for s in al.scan_stats(con, 20)}
    except Exception:  # noqa: BLE001
        scan = {}
    _stats_cache.update(until=now + dt.timedelta(minutes=10), pat=pat, scan=scan)
    return pat, scan


def pattern_record(r: dict, pat_stats: dict) -> dict:
    s = pat_stats.get(r["kind"]) or {}
    bucket = (s.get("by_score") or {}).get(">=5" if (r.get("score") or 0) >= 5 else "<5") or {}
    return {"hit_rate_pct": desk._r(s.get("hit_rate")), "triggers_20d": s.get("triggers"), "avg_r30_pct": desk._r(s.get("avg_r30")),
            "median_min_to_target": s.get("median_tt_min"), "same_score_bucket": {"hit_rate_pct": desk._r(bucket.get("hit_rate")), "triggers": bucket.get("triggers")}}


def _verdict(cur, R, con, *, day, at, trigger, ref, snap, refs, lots, record, model=desk.MODEL):
    sym = trigger["symbol"]
    df = load_symbol(con, day, sym, at)
    sector = (refs.get(sym) or {}).get("sector")
    sector_pct = ((snap or {}).get("sectors") or {}).get(sector)
    ctx = desk.verdict_context(trigger, snap=snap, sector_pct=sector_pct, bars=desk.bars5m_tail(df, at), ref=refs.get(sym),
                               lot=lots.get(sym, 0), setup_stats=record, now_hhmm=at.strftime("%H:%M"))
    if ctx["ranking_row"] is None and not df.empty and refs.get(sym):
        a = analyse(sym, df, refs[sym])
        if a:
            ctx["ranking_row"] = desk.rank_row({"bulls": [a] if a["dir"] > 0 else [], "bears": [a] if a["dir"] <= 0 else []}, sym)
    return desk.run_job(cur, R, day=day, kind="verdict", ts=at, ref=ref, symbol=sym, system=desk.VERDICT_SYSTEM,
                        context=ctx, schema=desk.VERDICT_SCHEMA, model=model, max_tokens=4000)


def new_pattern_triggers(con, day, at, include_replay: bool = False) -> list[dict]:
    """Live, triggered, not superseded pattern rows as the dashboard shows them at `at`."""
    stored = [r for r in pt.stored_rows(con, day) if (include_replay or r["source"] == "live") and r.get("triggered_at")]
    return [r for r in pt.rows_at(stored, at) if r["state"] != "forming" and r["triggered_at"] is not None]


def new_alerts(con, day, at, include_replay: bool = False) -> list[dict]:
    start, _ = session_bounds(day)
    src = ("live", "replay") if include_replay else ("live",)
    with con.cursor() as cur:
        cur.execute("SELECT id, ts, scan, symbol, price, payload FROM alerts WHERE source = ANY(%s) AND ts>=%s AND ts<=%s ORDER BY id", (list(src), start, at))
        return [{"id": i, "ts": ts.astimezone(IST), "scan": sc, "symbol": sym, "price": float(pr), "payload": p or {}} for i, ts, sc, sym, pr, p in cur.fetchall()]


def process_minute(con, R, day, at: dt.datetime, snap: dict | None, *, prepare=None, include_replay: bool = False) -> tuple[dict, list[dict]]:
    """The desk's work for one minute of `day`: verdicts for new triggers, the brief when
    due, the review after 15:40. Returns (counts, verdict records to send)."""
    done = {"verdicts": 0, "brief": 0, "review": 0}
    sent: list[dict] = []
    refs = load_refs(con, day)
    lots = lots_map(con)
    pat_stats, scan_stats = setup_stats(con, at)
    scans = {s["name"]: s for s, _ in al.scan_files()}
    with con.cursor() as cur:
        for r in new_pattern_triggers(con, day, at, include_replay):
            ref = pattern_ref(r)
            if r["state"] == "failed" or desk.exists(cur, day, "verdict", ref):
                continue                                      # a trigger that already failed gets no verdict
            if not gate(r.get("score"), r.get("rr")):
                skip_row(cur, day, at, ref, r["symbol"], f"skipped: score {r.get('score')} / R:R {r.get('rr'):.1f} below the gate"); con.commit(); continue
            conf = pt.confidence(pat_stats, r["kind"], r.get("score"), r["state"])
            if conf and conf.get("hit_rate") is not None and conf["hit_rate"] < MIN_CONF:
                skip_row(cur, day, at, ref, r["symbol"], f"skipped: {r['kind']} records {conf['hit_rate']:.0f}% in this bucket, below {MIN_CONF:.0f}%"); con.commit(); continue
            if stale(r["triggered_at"], at):
                skip_row(cur, day, at, ref, r["symbol"], f"stale: triggered {desk._hhmm(r['triggered_at'])}, judged only within {desk.MAX_AGE_MIN} min"); con.commit(); continue
            trig = {"source": "pattern", "kind": r["kind"], "label": desk.LABEL.get(r["kind"], r["kind"]), "at": desk._hhmm(r["triggered_at"]),
                    "score": r.get("score"), "checks": r.get("checks"), "tf": "5m", "symbol": r["symbol"], "side": r["side"],
                    "entry": r["entry"], "stop": r["stop"], "target": r["target"], "state": r["state"]}
            rec = _verdict(cur, R, con, day=day, at=at, trigger=trig, ref=ref, snap=snap, refs=refs, lots=lots, record=pattern_record(r, pat_stats))
            con.commit(); done["verdicts"] += 1
            if rec.get("output"):
                sent.append(rec)
        for a in new_alerts(con, day, at, include_replay):
            ref = f"alert:{a['id']}"
            if desk.exists(cur, day, "verdict", ref):
                continue
            sc = scans.get(a["scan"]) or {}
            if sc.get("desk") is False:
                continue                                      # scan opted out of desk verdicts ("desk": false in its JSON)
            side = sc.get("side") or a["payload"].get("side")
            if stale(a["ts"], at):
                skip_row(cur, day, at, ref, a["symbol"], f"stale: fired {a['ts'].strftime('%H:%M')}, judged only within {desk.MAX_AGE_MIN} min", {"label": a["scan"]}); con.commit(); continue
            if not desk.budget_allows("alert", desk.spent(R, day)):
                skip_row(cur, day, at, ref, a["symbol"], f"skipped: rule-alert share of the budget ({int(desk.ALERT_SHARE * 100)} %) is spent", {"label": a["scan"]}); con.commit(); continue
            row = desk.raw_row(snap, a["symbol"])
            if row is None:
                df = load_symbol(con, day, a["symbol"], at)
                row = analyse(a["symbol"], df, refs[a["symbol"]]) if not df.empty and refs.get(a["symbol"]) else None
            if not side or row is None:
                desk.store(cur, {"day": day, "kind": "verdict", "ts": at, "ref": ref, "symbol": a["symbol"], "context": {"alert": a["scan"]},
                                 "error": "no side or analysis for this alert"}); con.commit()
                continue
            stop, target = al.side_levels(row, side)
            rr = abs(target - a["price"]) / abs(a["price"] - stop) if a["price"] != stop else 0
            if rr < desk.ALERT_MIN_RR or not gate(None if desk.MIN_SCORE == 0 else 5, rr):
                skip_row(cur, day, at, ref, a["symbol"], f"skipped: R:R 1:{rr:.1f} below {desk.ALERT_MIN_RR:.1f}", {"label": a["scan"], "side": side}); con.commit(); continue
            st = scan_stats.get(a["scan"]) or {}
            trig = {"source": "alert", "kind": a["scan"], "label": a["scan"], "at": a["ts"].strftime("%H:%M"), "tf": a["payload"].get("tf") or sc.get("timeframe"),
                    "scan_rules": sc.get("rules"), "symbol": a["symbol"], "side": side, "entry": a["price"], "stop": stop, "target": target}
            record = {"hit_rate_pct": desk._r(st.get("hit_rate")), "fires_20d": st.get("fires"), "avg_r30_pct": desk._r(st.get("avg_r30")), "avg_r60_pct": desk._r(st.get("avg_r60"))}
            rec = _verdict(cur, R, con, day=day, at=at, trigger=trig, ref=ref, snap=snap, refs=refs, lots=lots, record=record)
            con.commit(); done["verdicts"] += 1
            if rec.get("output"):
                sent.append(rec)
        if brief_due(at):
            ref = f"brief:{at.strftime('%H:%M')}"
            if not desk.exists(cur, day, "brief", ref):
                rows = desk.rows_for_day(cur, day)
                briefs = [r for r in rows if r["kind"] == "brief" and r.get("output")]
                prev = briefs[-1] if briefs else None
                since = prev["ts"][11:16] if prev else None
                since_dt = at - dt.timedelta(minutes=desk.BRIEF_EVERY_MIN)
                live = [r for r in pt.stored_rows(con, day) if include_replay or r["source"] == "live"]
                recent = [r for r in pt.rows_at(live, at) if (r.get("triggered_at") or r.get("failed_at") or r["formed_at"]) >= since_dt]
                verdicts = [r for r in rows if r["kind"] == "verdict"]
                ctx = desk.brief_context(snap, recent, verdicts, prev["output"] if prev else None, at.strftime("%H:%M"), since)
                desk.run_job(cur, R, day=day, kind="brief", ts=at, ref=ref, symbol=None, system=desk.BRIEF_SYSTEM, context=ctx,
                             schema=desk.BRIEF_SCHEMA, model=desk.BRIEF_MODEL, max_tokens=6000)
                con.commit(); done["brief"] = 1
        if at.time() >= REVIEW_AT and not desk.exists(cur, day, "review", "review"):
            run_review(con, cur, R, day, at, prepare)
            con.commit(); done["review"] = 1
    return done, sent


def tick(connect, now: dt.datetime, send, prepare=None) -> dict:
    """One live minute (today, now). Telegram per verdict, then `desk:updated`."""
    R = desk_redis()
    at = now.astimezone(IST).replace(second=0, microsecond=0)
    day = at.date()
    snap = json.loads(R.get("scan:latest") or "null")
    with connect() as con:
        done, sent = process_minute(con, R, day, at, snap, prepare=prepare)
    for rec in sent:
        if os.environ.get("DESK_TELEGRAM", "1") != "0":
            try:
                send(desk.telegram_text(rec))
            except Exception as e:  # noqa: BLE001
                log.warning("telegram failed: %s", e)
    if any(done.values()):
        R.publish("desk:updated", json.dumps({"at": at.strftime("%H:%M"), **done}))
    return done


def verdict_outcomes(con, day, verdicts: list[dict]) -> None:
    """Grade each verdict's ticket against the day's candles (in place: v['outcome'])."""
    from replay import load_day
    frames = load_day(con, day)
    for v in verdicts:
        c = v.get("context") or {}
        t = c.get("ticket")
        df = frames.get(c.get("symbol"))
        if not t or df is None or df.empty:
            continue
        v["outcome"] = grade(df, pd.Timestamp(v["ts"]), c["side"], t["entry"], t["stop"], t["target"])


def run_review(con, cur, R, day, at, prepare=None) -> dict:
    """End-of-day review: outcomes for alerts/patterns/verdicts, P&L at size, data health."""
    if prepare:
        try:
            prepare(day)
        except Exception:  # noqa: BLE001
            log.exception("prepare before review failed")
    rows = desk.rows_for_day(cur, day)
    verdicts = [r for r in rows if r["kind"] == "verdict"]
    verdict_outcomes(con, day, verdicts)
    pats = [r for r in pt.stored_rows(con, day) if r["source"] == "live" and not r.get("superseded_at")]
    if not pats:
        pats = [r for r in pt.stored_rows(con, day) if not r.get("superseded_at")]
    with con.cursor() as c2:
        c2.execute("SELECT count(DISTINCT ts), count(DISTINCT symbol) FROM candles_1m WHERE ts>=%s AND ts<%s", session_bounds(day))
        n_min, n_sym = c2.fetchone()
    health = {"minutes_with_candles": n_min, "symbols": n_sym, "expected_minutes": "375 on a live day (09:15-15:29), 360 on a backfilled day (Kite equity bars end 15:14)"}
    ctx = desk.review_context(day, alerts=al.alerts_for_day(con, day), patterns=pats, verdicts=verdicts, lots=lots_map(con),
                              candles=health, spend=desk.spent(R, day))
    rec = desk.run_job(cur, R, day=day, kind="review", ts=at, ref="review", symbol=None, system=desk.REVIEW_SYSTEM, context=ctx,
                       schema=desk.REVIEW_SCHEMA, model=desk.MODEL, max_tokens=10000)
    return rec


_R = None


def desk_redis():
    global _R
    if _R is None:
        import redis
        _R = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    return _R


async def run(connect, send, prepare=None) -> None:
    if not desk.enabled():
        log.info("desk off (no ANTHROPIC_API_KEY or DESK=0)")
        return
    log.info("desk loop up: model=%s budget=₹%.0f min_score=%d", desk.MODEL, desk.BUDGET_INR, desk.MIN_SCORE)
    loop = asyncio.get_running_loop()
    while True:
        now = dt.datetime.now(IST)
        try:
            if should_run(now):
                done = await loop.run_in_executor(None, tick, connect, now, send, prepare)
                if any(done.values()):
                    log.info("desk %s: %s", now.strftime("%H:%M"), done)
        except Exception:  # noqa: BLE001
            log.exception("desk tick failed")
        await asyncio.sleep(max(5, 80 - dt.datetime.now(IST).second))      # ~20 s after the minute, after the pattern tick
