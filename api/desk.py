"""The Desk: Claude as an intraday desk on top of the scanner.

Four jobs, all fixed structures (structured outputs), all inside a daily rupee budget:
  verdict(trigger)  take / wait / skip on a pattern trigger or rule alert
  brief()           what the tape is doing, every 15 minutes
  ask(question)     a question about the day as the slider shows it
  review()          end-of-day review of rules, verdicts and what went wrong
The model never sets entry / stop / target / size: those come from the system.
Pure helpers (pricing, budget maths, prompts, context shaping, message text) are
separated from I/O so they are testable without a key.
"""
import os, json, time, logging, datetime as dt
import pandas as pd

log = logging.getLogger("desk")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# ---------- configuration ----------
MODEL = os.environ.get("DESK_MODEL", "claude-opus-5-5")
BRIEF_MODEL = os.environ.get("DESK_BRIEF_MODEL") or MODEL
EFFORT = os.environ.get("DESK_EFFORT", "medium")
BUDGET_INR = float(os.environ.get("DESK_BUDGET_INR", "100"))
BRIEF_SHARE = 0.7                       # briefs stop at 70 % of the budget
ALERT_SHARE = 0.6                       # rule-alert verdicts stop at 60 %: pattern triggers and the review keep the rest
VERDICT_SHARE = 0.95                    # verdicts stop at 95 %: the review always has ₹5
ALERT_MIN_RR = float(os.environ.get("DESK_ALERT_MIN_RR", "1.5"))
MAX_AGE_MIN = int(os.environ.get("DESK_MAX_AGE_MIN", "10"))     # a trigger older than this gets no verdict (restart backlog)
ASK_CAP_INR = float(os.environ.get("DESK_ASK_INR", "3"))
USDINR = float(os.environ.get("DESK_USDINR", "88"))
MIN_SCORE = int(os.environ.get("DESK_MIN_SCORE", "0"))
MIN_RR = float(os.environ.get("DESK_MIN_RR", "0"))
RISK_INR = float(os.environ.get("DESK_RISK_INR", "2000"))
BRIEF_EVERY_MIN = int(os.environ.get("DESK_BRIEF_MIN", "15"))
BLIND_TO = "news · index level · order book"

# USD per million tokens: input, output, cache read, cache write (5 min)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("DESK", "1") != "0"


# ---------- cost ----------
def cost_inr(model: str, usage: dict, usdinr: float = USDINR) -> float:
    p = PRICES.get(model) or PRICES.get(next((k for k in PRICES if k.split("-")[1] in model), "claude-opus-5-5"))
    inp, out, cr, cw = p
    usd = (usage.get("input_tokens", 0) * inp + usage.get("output_tokens", 0) * out
           + usage.get("cache_read_input_tokens", 0) * cr + usage.get("cache_creation_input_tokens", 0) * cw) / 1e6
    return round(usd * usdinr, 4)


def budget_allows(kind: str, spent: float, budget: float = BUDGET_INR) -> bool:
    """Verdicts and reviews may spend the whole budget; briefs stop at BRIEF_SHARE; asks need a cap left."""
    if budget <= 0:
        return False
    if kind == "brief":
        return spent < budget * BRIEF_SHARE
    if kind == "alert":
        return spent < budget * ALERT_SHARE
    if kind == "verdict":
        return spent < budget * VERDICT_SHARE
    if kind == "ask":
        return spent + ASK_CAP_INR <= budget
    return spent < budget


# ---------- schemas (the API accepts a JSON-schema subset: no numeric bounds, lengths live in the prompt) ----------
REASON = {"type": "object", "additionalProperties": False, "required": ["mark", "text"],
          "properties": {"mark": {"type": "string", "enum": ["ok", "no", "warn"]}, "text": {"type": "string"}}}
VERDICT_SCHEMA = {"type": "object", "additionalProperties": False,
                  "required": ["call", "confidence", "reasons", "exit_rule", "book_half_at"],
                  "properties": {"call": {"type": "string", "enum": ["take", "wait", "skip"]},
                                 "confidence": {"type": "integer"},
                                 "reasons": {"type": "array", "items": REASON},
                                 "exit_rule": {"type": "string"},
                                 "book_half_at": {"type": ["number", "null"]}}}
BRIEF_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["paragraphs", "watch"],
                "properties": {"paragraphs": {"type": "array", "items": {"type": "string"}},
                               "watch": {"type": "array", "items": {"type": "string"}}}}
ASK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer", "used"],
              "properties": {"answer": {"type": "string"}, "used": {"type": "array", "items": {"type": "string"}}}}
REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["rules_said", "desk_wrong", "tomorrow", "health"],
                 "properties": {"rules_said": {"type": "string"}, "desk_wrong": {"type": "string"},
                                "tomorrow": {"type": "string"}, "health": {"type": "array", "items": {"type": "string"}}}}

# ---------- prompts (stable text, cached) ----------
HOUSE = """You are the desk at ScanRadar, an intraday scanner for NSE stock futures run by one retail trader in India. \
You act as a disciplined intraday trader whose only inputs are the numbers in the message: 1-minute and 5-minute candles with \
volume and futures open interest, the scanner's ranking row (% change vs previous close, VWAP side, OI label from the \
price x OI matrix over the last 30 min: long build-up / short build-up / long unwinding / short covering, RVOL vs the 20-day \
average, pivots S1/S2/R1/R2 from the previous day, opening range, order block), the sector's average % change, market breadth, \
and the recorded hit rate of the setup over the last sessions. You cannot see news, the index level, the order book or \
anything outside the message. Never guess at those; if they would matter, say so inside a reason.
Market hours 09:15-15:30 IST. Trades are closed by 15:20 at the latest. The trader risks a fixed rupee amount per trade, \
sizes in lots and cannot average down. You never place orders and never tell the trader to add to a loser.
Be blunt and concrete: every reason must cite a number from the message (a price, a percentage, a time, a count). \
No greetings, no hedging phrases. Judge the setup, never the size: the size box is informational, and lot size or the rupee \
risk budget is never a reason for a call."""

VERDICT_SYSTEM = HOUSE + """

Task: judge one trigger. Return the structure only.
- call: take = the trader should enter now at the entry shown; wait = re-check at a stated condition; skip = do not trade this.
- confidence: an integer 1-10, how sure you are of the call (not of the trade's success).
- reasons: exactly three, each marked ok (supports the trade), no (against), warn (caveat); each at most 90 characters, cite numbers.
- exit_rule: one sentence, a price or bar condition, earlier than the stop when the pattern is already failing.
- book_half_at: a price to book half the position before the target when a known level (previous close/high/low, pivot, \
VWAP, opening range) sits in the way, else null.
Context fields: pattern triggers carry score 0-8 (checks, in order: day moved >=1 % by the second point, OI lower at the \
second point, trigger volume >=1.5x the day's 5-minute average, sector agrees, trigger close on the right side of VWAP, \
exhaustion = the second point's bars traded less than the first's, breakout volume = the confirming close traded >=1.5x the \
median of the pattern's own bars, breakout delta = tick-rule delta with the direction) and the setup's recorded hit rate; rule alerts carry the scan's rules and its recorded hit rate. The ticket (entry, stop, target, size) is fixed."""

BRIEF_SYSTEM = HOUSE + """

Task: a market brief for the trader, read in 30 seconds. Return the structure only.
- paragraphs: two or three, each under 90 words. First: breadth and which sectors are doing the work, naming the stocks holding the top ranks and \
for how long. Second: what the pattern desk did since the last brief (triggered, failed, forming) and how the desk's verdicts \
fared so far. Optional third: one risk for the next 15 minutes drawn from the numbers.
- watch: up to four one-line items (under 20 words each) "SYMBOL - condition - why", for the next quarter hour.
Continue from the previous brief when given; do not repeat what has not changed."""

ASK_SYSTEM = HOUSE + """

Task: answer the trader's question about the session as of the stated minute, from the data given. Return the structure only.
- answer: at most 120 words, numbers first, no preamble. If the data cannot answer, say what is missing.
- used: which parts of the context you relied on (e.g. "ranking 10:45", "TECHM 5m bars", "patterns")."""

REVIEW_SYSTEM = HOUSE + """

Task: an end-of-day review the trader reads once. Return the structure only.
- rules_said: what the rules and patterns produced today and what following them mechanically would have made, \
using the P&L figures given (at the shown size, before costs).
- desk_wrong: where the desk's verdicts were wrong (skips that paid, takes that stopped), with the reason it gave.
- tomorrow: one concrete thing for tomorrow, grounded in today's numbers.
- health: up to five short data-health lines from the figures (candle count, gaps, desk spend vs budget)."""


# ---------- context shaping (pure) ----------
def _r(x, n=2):
    return None if x is None else round(float(x), n)


def bars5m_tail(df1m: pd.DataFrame, at, n: int = 12) -> list[dict]:
    """Last n complete 5-minute bars before `at` (OHLCV + OI), 09:15-anchored, compact rows."""
    if df1m is None or df1m.empty:
        return []
    d = df1m[df1m.ts < pd.Timestamp(at)].set_index("ts")
    if d.empty:
        return []
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "oi" in d:
        agg["oi"] = "last"
    b = d.resample("5min", origin="start_day", offset="15min").agg(agg).dropna(subset=["close"]).tail(n)
    return [{"t": ts.strftime("%H:%M"), "o": _r(r.open), "h": _r(r.high), "l": _r(r.low), "c": _r(r.close),
             "v": int(r.volume), **({"oi": int(r.oi)} if "oi" in b and pd.notna(r.oi) else {})} for ts, r in b.iterrows()]


def rank_row(snap: dict | None, symbol: str) -> dict | None:
    if not snap:
        return None
    for side in ("bulls", "bears"):
        for a in snap.get(side, []):
            if a["symbol"] == symbol:
                keep = ("rank", "side_rank", "picked", "score", "pct", "price", "vwap", "above_vwap", "oi_pct", "oi_label", "rvol",
                        "delta", "S1", "S2", "R1", "R2", "P", "orH", "orL", "stop", "target", "rr", "dir", "conv")
                row = {k: (_r(a[k]) if isinstance(a.get(k), float) else a.get(k)) for k in keep if k in a}
                ob = a.get("ob")
                if ob:
                    row["ob"] = {"lo": _r(ob.get("lo")), "hi": _r(ob.get("hi")), "dir": ob.get("dir")}
                row["side"] = "bull" if side == "bulls" else "bear"
                return row
    return None


def size_for(entry: float, stop: float, lot: int, risk: float = RISK_INR) -> dict:
    dist = abs(float(entry) - float(stop))
    if not lot or not dist:
        return {"qty": 0, "lots": 0, "risk_inr": risk}
    lots = int(risk // (dist * lot))
    return {"qty": lots * lot, "lots": lots, "lot": lot, "risk_inr": risk, "rupees_at_stop": round(lots * lot * dist, 0),
            "one_lot_risk": round(lot * dist, 0)}


def verdict_context(trigger: dict, *, snap, sector_pct, bars, ref, lot, setup_stats, now_hhmm) -> dict:
    """Everything the model sees for one verdict. `trigger` is a pattern row or a rule alert row (see live_desk)."""
    entry, stop, target = float(trigger["entry"]), float(trigger["stop"]), float(trigger["target"])
    risk = abs(entry - stop)
    ctx = {"now": now_hhmm, "minutes_since_open": _mins(now_hhmm), "symbol": trigger["symbol"], "side": trigger["side"],
           "trigger": {k: trigger[k] for k in ("source", "kind", "label", "at", "score", "checks", "scan_rules", "tf", "levels") if k in trigger},
           "ticket": {"entry": _r(entry), "stop": _r(stop), "target": _r(target), "rr": _r(abs(target - entry) / risk if risk else 0, 2),
                      "risk_pct": _r(risk / entry * 100 if entry else 0, 2), "size": size_for(entry, stop, lot)},
           "setup_record": setup_stats, "ranking_row": rank_row(snap, trigger["symbol"]),
           "sector": {"name": (ref or {}).get("sector"), "pct": _r(sector_pct)},
           "breadth": {"bulls": (snap or {}).get("n_bull"), "bears": (snap or {}).get("n_bear")} if snap else None,
           "prev_day": {k: _r((ref or {}).get(k)) for k in ("prev_close", "prev_high", "prev_low")} if ref else None,
           "bars_5m": bars, "blind_to": BLIND_TO}
    return ctx


def _mins(hhmm: str) -> int:
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    return (h - 9) * 60 + (m - 15)


def brief_context(snap: dict | None, patterns: list[dict], verdicts: list[dict], prev_brief: dict | None, now_hhmm: str,
                  since_hhmm: str | None, rank_hold: dict | None = None) -> dict:
    top = lambda side: [{k: (_r(a[k]) if isinstance(a.get(k), float) else a.get(k)) for k in ("symbol", "pct", "oi_label", "rvol", "rr", "picked")}  # noqa: E731
                        | ({"held_since": rank_hold.get(a["symbol"])} if rank_hold and a["symbol"] in rank_hold else {})
                        for a in (snap or {}).get(side, [])[:5]]
    pats = [{"symbol": p["symbol"], "kind": p["kind"], "side": p["side"], "state": p["state"], "score": p.get("score"),
             "entry": _r(p.get("entry")), "target": _r(p.get("target")), "at": _hhmm(p.get("triggered_at") or p.get("formed_at"))}
            for p in patterns]
    judged = [v for v in verdicts if v.get("output")]
    counts = {"take": 0, "wait": 0, "skip": 0}
    for v in judged:
        counts[v["output"]["call"]] = counts.get(v["output"]["call"], 0) + 1
    recent = [v for v in judged if since_hhmm is None or _hhmm(v["ts"]) > since_hhmm][-20:]
    vds = [{"symbol": v["symbol"], "call": v["output"]["call"], "confidence": v["output"]["confidence"], "at": _hhmm(v["ts"]),
            "side": v["context"].get("side"), "trigger": (v["context"].get("trigger") or {}).get("label"), "outcome": v.get("outcome")} for v in recent]
    return {"now": now_hhmm, "since": since_hhmm, "breadth": {"bulls": (snap or {}).get("n_bull"), "bears": (snap or {}).get("n_bear")},
            "sectors": {k: _r(v) for k, v in sorted(((snap or {}).get("sectors") or {}).items(), key=lambda kv: -kv[1])},
            "top_bulls": top("bulls"), "top_bears": top("bears"), "patterns_since_last": pats[:30], "verdicts_since_last": vds,
            "verdicts_today_counts": counts, "previous_brief": prev_brief, "blind_to": BLIND_TO}


def _hhmm(ts) -> str | None:
    if ts is None:
        return None
    s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    return s[11:16]


# ---------- rendering (pure) ----------
from core.patterns import LABELS as _LABELS
LABEL = {k: v[0].lower() + v[1:] for k, v in _LABELS.items()}
MARK = {"ok": "✓", "no": "✗", "warn": "!"}


def telegram_text(v: dict) -> str:
    """One verdict as a Telegram message (HTML)."""
    o, c = v["output"], v["context"]
    side = "BUY" if c["side"] == "long" else "SELL"
    t = c["ticket"]
    size = t["size"]
    head = f"<b>{o['call'].upper()} {o['confidence']}/10 · {c['symbol']} · {side} · {c['trigger'].get('label', '')}</b>"
    ticket = f"Entry {t['entry']:,.2f} · Stop {t['stop']:,.2f} · Target {t['target']:,.2f}" + (f" · Size {size['qty']}" if size.get("qty") else "")
    reasons = " · ".join(f"{MARK[r['mark']]} {r['text']}" for r in o["reasons"])
    extra = f"\nBook half at {o['book_half_at']:,.2f}" if o.get("book_half_at") else ""
    return f"{head}\n{ticket}\n{reasons}\nExit: {o['exit_rule']}{extra}\n{_hhmm(v['ts'])} · scanradar.tech"


# ---------- the call ----------
_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=2, timeout=60.0)
    return _client


def call_model(system: str, user: dict | str, schema: dict, model: str, max_tokens: int = 4000, effort: str = EFFORT) -> dict:
    """One structured call. Returns {output, usage, model, latency_ms, stop_reason} or raises.
    max_tokens covers the model's thinking as well as the JSON, so it must be several times the output size."""
    import anthropic
    t0 = time.time()
    content = user if isinstance(user, str) else json.dumps(user, separators=(",", ":"), default=str)
    resp = client().messages.create(
        model=model, max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    usage = resp.usage.to_dict() if hasattr(resp.usage, "to_dict") else dict(resp.usage)
    usage = {k: v for k, v in usage.items() if isinstance(v, int)}
    out = {"usage": usage, "model": resp.model, "latency_ms": int((time.time() - t0) * 1000), "stop_reason": resp.stop_reason}
    if resp.stop_reason == "refusal":
        raise DeskDeclined(getattr(getattr(resp, "stop_details", None), "category", None) or "refusal", out)
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if resp.stop_reason == "max_tokens":
        raise DeskDeclined("max_tokens", out)
    out["output"] = json.loads(text)
    if schema is VERDICT_SCHEMA:
        o = out["output"]
        o["confidence"] = max(1, min(10, int(o.get("confidence") or 5)))
        o["reasons"] = (o.get("reasons") or [])[:3]
    return out


class DeskDeclined(Exception):
    def __init__(self, reason: str, meta: dict):
        super().__init__(reason)
        self.reason, self.meta = reason, meta


# ---------- spend (Redis) ----------
def spend_key(day) -> str:
    return f"desk:spend:{day}"


def spent(R, day) -> float:
    v = R.get(spend_key(day))
    return float(v) if v else 0.0


def add_spend(R, day, inr: float) -> float:
    k = spend_key(day)
    v = R.incrbyfloat(k, inr)
    R.expire(k, 3 * 86400)
    return float(v)


# ---------- storage ----------
INSERT = ("INSERT INTO desk_calls(day,kind,ts,symbol,ref,model,context,output,usage,cost_inr,latency_ms,error) "
          "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (day, kind, ref) DO UPDATE SET "
          "output=EXCLUDED.output, usage=EXCLUDED.usage, cost_inr=EXCLUDED.cost_inr, latency_ms=EXCLUDED.latency_ms, "
          "error=EXCLUDED.error, model=EXCLUDED.model, context=EXCLUDED.context RETURNING id")
SELECT = "SELECT id,day,kind,ts,symbol,ref,model,context,output,usage,cost_inr,latency_ms,error FROM desk_calls"
COLS = ["id", "day", "kind", "ts", "symbol", "ref", "model", "context", "output", "usage", "cost_inr", "latency_ms", "error"]


def store(cur, rec: dict) -> int:
    cur.execute(INSERT, (rec["day"], rec["kind"], rec["ts"], rec.get("symbol"), rec["ref"], rec.get("model"),
                         json.dumps(rec.get("context"), default=str), json.dumps(rec.get("output"), default=str) if rec.get("output") is not None else None,
                         json.dumps(rec.get("usage")) if rec.get("usage") else None, rec.get("cost_inr", 0.0), rec.get("latency_ms"), rec.get("error")))
    return cur.fetchone()[0]


FINAL_ERRORS = ("budget", "skipped", "stale", "declined")


def exists(cur, day, kind: str, ref: str) -> bool:
    """A job is done when it has output or a final error; an API/network failure is retried next minute."""
    cur.execute("SELECT output IS NOT NULL, error FROM desk_calls WHERE day=%s AND kind=%s AND ref=%s", (day, kind, ref))
    row = cur.fetchone()
    if row is None:
        return False
    has_output, err = row
    return bool(has_output) or (err or "").startswith(FINAL_ERRORS)


def rows_for_day(cur, day, upto=None) -> list[dict]:
    if upto is None:
        cur.execute(SELECT + " WHERE day=%s ORDER BY ts, id", (day,))
    else:
        cur.execute(SELECT + " WHERE day=%s AND ts<=%s ORDER BY ts, id", (day, upto))
    out = []
    for rec in cur.fetchall():
        r = dict(zip(COLS, rec))
        r["ts"] = r["ts"].astimezone(IST).isoformat()
        r["day"] = str(r["day"])
        out.append(r)
    return out


def run_job(cur, R, *, day, kind, ts, ref, symbol, system, context, schema, model, max_tokens=1500) -> dict:
    """Budget check → model → cost → store. Always stores a row (with `error` when it did not run)."""
    rec = {"day": day, "kind": kind, "ts": ts, "ref": ref, "symbol": symbol, "model": model, "context": context, "output": None}
    used = spent(R, day)
    if not budget_allows(kind, used):
        rec["error"] = f"budget reached (₹{used:.0f} of ₹{BUDGET_INR:.0f})"
    else:
        try:
            res = call_model(system, context, schema, model, max_tokens=max_tokens)
            rec.update(output=res["output"], usage=res["usage"], model=res["model"], latency_ms=res["latency_ms"],
                       cost_inr=cost_inr(res["model"], res["usage"]))
            add_spend(R, day, rec["cost_inr"])
        except DeskDeclined as e:
            rec.update(error=f"declined: {e.reason}", usage=e.meta.get("usage"), latency_ms=e.meta.get("latency_ms"),
                       cost_inr=cost_inr(e.meta.get("model") or model, e.meta.get("usage") or {}))
            add_spend(R, day, rec["cost_inr"])
        except Exception as e:  # noqa: BLE001 — API/network errors are recorded, never raised into the loop
            rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            log.warning("desk %s %s failed: %s", kind, ref, rec["error"])
    rec["id"] = store(cur, rec)
    return rec


# ---------- day view ----------
def day_view(rows: list[dict], at_iso: str | None = None) -> dict:
    """Split stored rows into what the tab shows; nothing after `at` leaks in."""
    sel = [r for r in rows if at_iso is None or r["ts"] <= at_iso]
    skipped = [r for r in sel if r["kind"] == "verdict" and (r.get("error") or "").startswith(("skipped", "stale"))]
    verdicts = [r for r in sel if r["kind"] == "verdict" and r not in skipped]
    briefs = [r for r in sel if r["kind"] == "brief"]
    asks = [r for r in sel if r["kind"] == "ask"]
    review = next((r for r in rows if r["kind"] == "review"), None)
    spend = round(sum(r.get("cost_inr") or 0 for r in rows), 2)
    calls = {"take": 0, "wait": 0, "skip": 0}
    for v in verdicts:
        if v.get("output"):
            calls[v["output"]["call"]] = calls.get(v["output"]["call"], 0) + 1
    return {"verdicts": list(reversed(verdicts)), "briefs": briefs[-3:], "asks": asks[-5:], "review": review,
            "spend": spend, "calls": calls, "skipped": len(skipped)}


# ---------- review maths (pure) ----------
def raw_row(snap: dict | None, symbol: str) -> dict | None:
    """The untrimmed ranking row (analyse() output) for a symbol, if the snapshot carries it."""
    for side in ("bulls", "bears"):
        for a in (snap or {}).get(side, []):
            if a["symbol"] == symbol:
                return a
    return None


def pnl_for(outcome: dict | None, side: str, entry: float, stop: float, target: float, qty: int) -> float | None:
    """Rupees at the shown size, before costs: target → full move, stop → full risk, open → the 60-min return."""
    if not outcome or not qty:
        return None
    hit = outcome.get("hit")
    if hit == "target":
        return round(qty * abs(float(target) - float(entry)), 0)
    if hit == "stop":
        return round(-qty * abs(float(entry) - float(stop)), 0)
    r60 = outcome.get("r60")
    if r60 is None:
        return None
    return round(qty * float(entry) * float(r60) / 100, 0)        # r60 is signed favourable %


def review_context(day, *, alerts: list[dict], patterns: list[dict], verdicts: list[dict], lots: dict,
                   candles: dict, spend: float, budget: float = BUDGET_INR) -> dict:
    """What the model sees for the end-of-day review. Every P&L is for ONE LOT, before costs
    (the risk-based size is often zero lots, which would hide the information)."""
    def pl(side, entry, stop, target, sym, outcome):
        if entry is None or stop is None or target is None:
            return None
        return pnl_for(outcome, side, entry, stop, target, lots.get(sym, 0))

    pats = []
    for p in patterns:
        if not p.get("triggered_at"):
            continue
        pats.append({"symbol": p["symbol"], "kind": p["kind"], "side": p["side"], "score": p.get("score"), "at": _hhmm(p["triggered_at"]),
                     "hit": (p.get("outcome") or {}).get("hit"), "r30": _r((p.get("outcome") or {}).get("r30")),
                     "pnl": pl(p["side"], p.get("entry"), p.get("stop"), p.get("target"), p["symbol"], p.get("outcome"))})
    als = [{"scan": a["scan"], "symbol": a["symbol"], "side": a.get("side"), "at": _hhmm(a["ts"]), "hit": (a.get("outcome") or {}).get("hit"),
            "r30": _r((a.get("outcome") or {}).get("r30")),
            "pnl": pl(a.get("side"), a.get("price"), a.get("stop"), a.get("target"), a["symbol"], a.get("outcome"))} for a in alerts]
    by_scan: dict = {}
    for a in als:                                   # hundreds of rule alerts a day: the model gets the per-scan tally + the 30 largest
        t = by_scan.setdefault(a["scan"], {"fires": 0, "target": 0, "stop": 0, "pnl_one_lot": 0.0})
        t["fires"] += 1
        if a["hit"] in ("target", "stop"):
            t[a["hit"]] += 1
        if a["pnl"] is not None:
            t["pnl_one_lot"] += a["pnl"]
    biggest = sorted([a for a in als if a["pnl"] is not None], key=lambda a: -abs(a["pnl"]))[:30]
    vds = []
    for v in verdicts:
        if not v.get("output"):
            continue
        c, o = v["context"], v["output"]
        t = c["ticket"]
        vds.append({"symbol": c["symbol"], "side": c["side"], "trigger": c["trigger"].get("label"), "at": _hhmm(v["ts"]),
                    "call": o["call"], "confidence": o["confidence"], "reasons": [r["text"] for r in o["reasons"]],
                    "hit": (v.get("outcome") or {}).get("hit"), "pnl": pnl_for(v.get("outcome"), c["side"], t["entry"], t["stop"], t["target"], lots.get(c["symbol"], 0))})
    tot = lambda rows: round(sum(r["pnl"] for r in rows if r.get("pnl") is not None), 0)   # noqa: E731
    return {"day": str(day), "patterns_triggered": pats[:60], "rule_alerts_by_scan": by_scan, "rule_alerts_largest": biggest, "verdicts": vds,
            "pnl_summary": {"all_pattern_triggers": tot(pats), "patterns_score_ge3": tot([p for p in pats if (p.get("score") or 0) >= 3]),
                            "all_rule_alerts": tot(als), "verdict_take": tot([v for v in vds if v["call"] == "take"]),
                            "verdict_skip_if_taken": tot([v for v in vds if v["call"] == "skip"]),
                            "verdict_wait_if_taken": tot([v for v in vds if v["call"] == "wait"])},
            "pnl_basis": "one lot per trade, before costs; target = full move, stop = full risk, open = 60-min return",
            "data_health": candles, "desk_spend_inr": round(spend, 2), "desk_budget_inr": budget, "blind_to": BLIND_TO}
