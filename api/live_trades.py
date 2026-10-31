"""Live trades loop (API process): the one minute loop of the API from 28 Oct 2026 on.

Every minute at ~:20 s, in a worker thread: a trade row (odds, size in cash shares, the day
gate) for every new trigger the desk would judge, settlement of the open rows, then the
deferred Telegram queue (`tg:defer:{day}`, filled by the engine's rule alerts and the
pattern loop) goes out with the row's odds line appended, then the desk's minute."""
import os, json, logging, asyncio, datetime as dt
import desk
import live_desk
import alerts as al
import trades
from core.calendar import is_trading_day
from core.gate import ledger

log = logging.getLogger("live_trades")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
START, END = dt.time(9, 20), dt.time(15, 30)
TTL = 86400
ODDS_KEYS = ("odds", "size", "gate", "gate_reason")
WAIT_S = 90                        # a deferred message waits this long for its trade row
GIVE_UP_S = 30 * 60                # older than this it is dropped, never sent late
WORDS = {"flat": "cannot resolve by close", "coin": "coin flip", "edge": "odds favour T1", "bad": "odds favour stop"}


def should_run(now: dt.datetime) -> bool:
    n = now.astimezone(IST)
    return is_trading_day(n.date()) and START <= n.time() <= END


def _pct(x) -> int:
    return int(float(x) + 0.5)


def odds_line(row: dict | None) -> str:
    """One line for the Telegram message: odds, size, gate."""
    if row is None:
        return ""
    parts = []
    odds = row.get("odds")
    if odds:
        mc = odds.get("mc") or {}
        parts += [f"T1 {_pct(mc['target_pct'])} %", f"stop {_pct(mc['stop_pct'])} %", WORDS.get(odds.get("verdict"), odds.get("verdict") or "")]
    else:
        parts.append("no history")
    size = row.get("size") or {}
    if size.get("shares") is not None:
        parts.append(f"{size['shares']} sh")
    g = row.get("gate")
    if g == "BLOCK":
        parts.append(f"BLOCK: {row['gate_reason']}" if row.get("gate_reason") else "BLOCK")
    elif g:
        parts.append(g)
    return " · ".join(p for p in parts if p)


def _set_odds(R, day, ref: str, row: dict) -> None:
    try:
        R.set(f"trades:{day}:{ref}", json.dumps({k: row.get(k) for k in ODDS_KEYS}, default=float), ex=TTL)
    except Exception as e:  # noqa: BLE001  the key is backfilled from the stored row next minute
        log.warning("odds key for %s not written: %s", ref, e)


def _tombstones(R, day, alerts: list[dict], skip: set[str]) -> None:
    """An empty key for every alert that gets no trade row, so its deferred message goes out at once."""
    for a in alerts:
        ref = f"alert:{a['id']}"
        if ref in skip:
            continue
        try:
            if not R.exists(f"trades:{day}:{ref}"):
                R.set(f"trades:{day}:{ref}", "", ex=TTL)
        except Exception as e:  # noqa: BLE001  the message then waits WAIT_S, as before
            log.warning("tombstone for %s not written: %s", ref, e)


def process_minute(con, R, day, at: dt.datetime, settings: dict) -> dict:
    """Rows for the minute's new triggers, then settle the open rows; {"rows", "settled"}."""
    trades.forget_other_days(day)
    pat_stats, _ = live_desk.setup_stats(con, at)
    scans = {s["name"]: s for s, _ in al.scan_files()}
    have = trades.odds_by_ref(con, day)
    apply_rules = day >= trades.RULES_FROM
    try:                                                      # read before candidates(): an alert inserted in between is judged next minute
        alerts = [a for a in trades._alert_rows(con, day) if a.get("source") == "live" and trades._minute(a["ts"]) <= at]
    except Exception:  # noqa: BLE001
        log.exception("alert rows for tombstones failed")
        alerts = []
    cands = trades.candidates(con, day, at, include_replay=False, max_age=desk.MAX_AGE_MIN, pat_stats=pat_stats, scans=scans)
    n = 0
    for cand in cands:
        ref = cand["ref"]
        if ref in have:
            continue
        try:
            state = ledger(trades.rows_for_day(con, day, at), settings)
            row = trades.build_row(cand, settings, state, trades.load_history(con, cand["symbol"], day), apply_rules=apply_rules)
            with con.cursor() as cur:
                rid = trades.insert_row(cur, row)
            con.commit()
        except Exception:  # noqa: BLE001  one bad trigger never holds up the rest of the minute
            con.rollback()
            log.exception("trade row failed for %s", ref)
            continue
        if rid is None:
            continue
        n += 1
        _set_odds(R, day, ref, row)
    try:                                                      # a row whose key never got written (Redis hiccup, insert race)
        for ref, row in trades.odds_by_ref(con, day).items():
            if not R.exists(f"trades:{day}:{ref}"):
                _set_odds(R, day, ref, row)
    except Exception:  # noqa: BLE001
        log.exception("odds key backfill failed")
    _tombstones(R, day, alerts, {c["ref"] for c in cands} | set(have))
    flat_by = dt.time.fromisoformat(settings["flat_by"])
    settled = trades.settle_open(con, day, at, settings["flat_by"], final=at.time() >= flat_by)
    log.info("trades %s: %d new rows, %d settled", at.strftime("%H:%M"), n, settled)
    if n or settled:
        R.publish("trades:updated", json.dumps({"at": at.strftime("%H:%M"), "new": n}))
    return {"rows": n, "settled": settled}


def flush_deferred(R, day, send, at: dt.datetime) -> int:
    """Send the deferred Telegram messages of `day`: with the odds line once the trade row
    exists, at once and plain when the key is an empty tombstone (no row will come), as they are
    after WAIT_S without a key, never after GIVE_UP_S. Returns the number sent."""
    key = f"tg:defer:{day}"
    later, n = [], 0
    try:
        while True:
            raw = R.lpop(key)
            if raw is None:
                break
            try:
                item = json.loads(raw)
                age = (at - dt.datetime.fromisoformat(item["ts"])).total_seconds()
                ref, text = item["ref"], item["text"]
            except (ValueError, KeyError, TypeError):
                log.warning("deferred message unreadable, dropped: %r", raw)
                continue
            if age > GIVE_UP_S:
                log.warning("deferred message for %s dropped: %.0f min old", ref, age / 60)
                continue
            try:
                row = R.get(f"trades:{day}:{ref}")
                if row == "":
                    pass                                       # tombstone: no trade row will come, send plain now
                elif row is not None:
                    text = text + "\n" + odds_line(json.loads(row))
                elif age < WAIT_S:
                    later.append(raw)                          # pushed back once the list is drained, so the loop ends
                    continue
            except Exception as e:  # noqa: BLE001  the message still goes out, without the line
                log.warning("odds line for %s failed, sending plain: %s", ref, e)
                text = item["text"]
            try:
                send(text)
                n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("telegram failed: %s", e)
    finally:
        for raw in later:
            R.rpush(key, raw)
        if later:
            R.expire(key, TTL)
    return n


def tick(connect, now: dt.datetime, send) -> dict:
    """The trades minute: rows and settlement inside the window, the deferred queue always."""
    R = live_desk.desk_redis()
    n = now.astimezone(IST)
    at = n.replace(second=0, microsecond=0)
    day = at.date()
    done = {"rows": 0, "settled": 0, "sent": 0}
    if should_run(at):                                        # the minute, not the wake-up second: 15:30:20 is the 15:30 minute
        try:
            with connect() as con:
                done.update(process_minute(con, R, day, at, trades.get_settings(con)))
        except Exception:  # noqa: BLE001
            log.exception("trades tick failed")
    try:
        done["sent"] = flush_deferred(R, day, send, n)
    except Exception:  # noqa: BLE001
        log.exception("deferred telegram flush failed")
    return done


async def run(connect, send, prepare=None) -> None:
    desk_on = desk.enabled()
    log.info("trades loop up (desk %s)", f"on: model={desk.MODEL} budget=₹{desk.BUDGET_INR:.0f}" if desk_on else "off: no ANTHROPIC_API_KEY or DESK=0")
    loop = asyncio.get_running_loop()
    while True:
        now = dt.datetime.now(IST)
        try:
            await loop.run_in_executor(None, tick, connect, now, send)
        except Exception:  # noqa: BLE001
            log.exception("trades tick failed")
        try:
            if desk.enabled() and live_desk.should_run(now):
                done = await loop.run_in_executor(None, live_desk.tick, connect, now, send, prepare)
                if any(done.values()):
                    log.info("desk %s: %s", now.strftime("%H:%M"), done)
        except Exception:  # noqa: BLE001
            log.exception("desk tick failed")
        await asyncio.sleep(max(5, 80 - dt.datetime.now(IST).second))      # ~20 s after the minute, after the pattern tick
