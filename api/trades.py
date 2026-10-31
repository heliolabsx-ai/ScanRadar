"""Trade rows: for every trigger the desk would judge (pattern triggers and rule alerts), the
odds from the Time Machine, the size in cash shares, the day gate and, once the day has
moved on, the settled outcome. One `trade_rows` row per (day, ref); settings in
`trade_settings('current')`."""
import os, json, datetime as dt
import pandas as pd
import desk
import live_desk
import patterns as pt
import alerts as al
from replay import IST, session_bounds
from core import timemachine as tm
from core.gate import ledger, gate, settle
from core.patterns import LABELS
from core.sizing import DEFAULTS, validate, shares

RULES_FROM = dt.date.fromisoformat(os.environ.get("TRADES_RULES_FROM", "2026-11-03"))   # gate and ledger apply from this day
REPEAT_MIN = 60                     # a repeat alert of the same (scan, symbol) within this many minutes gets no row
HIST_DAYS = 120                     # calendar days of candles read for the replay history
HIST_CACHE_MAX = 300
_HIST: dict = {}                    # (symbol, day) -> history()

COLS = ["id", "day", "ts", "ref", "symbol", "side", "source", "entry", "stop", "target", "target2", "rr",
        "odds", "size", "gate", "gate_reason", "outcome", "paper"]
INSERT_COLS = ["day", "ts", "ref", "symbol", "side", "source", "entry", "stop", "target", "target2", "rr",
               "odds", "size", "gate", "gate_reason"]
JSON_COLS = ("odds", "size", "outcome", "paper")
INSERT_SQL = (f"INSERT INTO trade_rows({','.join(INSERT_COLS)}) VALUES({','.join(['%s'] * len(INSERT_COLS))}) "
              "ON CONFLICT (day, ref) DO NOTHING RETURNING id")


def _dumps(v) -> str | None:
    return None if v is None else json.dumps(v, default=float)


# ---------- settings ----------
def get_settings(con) -> dict:
    """The stored settings filled in with DEFAULTS; DEFAULTS when none are stored."""
    with con.cursor() as cur:
        cur.execute("SELECT value FROM trade_settings WHERE key='current'")
        rec = cur.fetchone()
    value = rec[0] if rec else None
    return validate(value) if isinstance(value, dict) else dict(DEFAULTS)


def put_settings(con, body: dict) -> dict:
    """Validate (ValueError names the field) and store as the current settings."""
    s = validate(body)
    with con.cursor() as cur:
        cur.execute("INSERT INTO trade_settings(key, value, updated_at) VALUES('current', %s, now()) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at", (_dumps(s),))
    con.commit()
    return s


# ---------- the queue ----------
def _minute(ts) -> dt.datetime:
    if isinstance(ts, str):
        ts = dt.datetime.fromisoformat(ts)
    return ts.astimezone(IST).replace(second=0, microsecond=0)


def _rr(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    return abs(target - entry) / risk if risk else 0.0


def _pattern_rows_at(con, day, at, include_replay: bool) -> list[dict]:
    """Pattern triggers up to `at` (live only unless include_replay), each as the dashboard showed it
    at its own trigger minute, the minute the desk judges it: a trigger that failed later still counts."""
    stored = [r for r in pt.stored_rows(con, day)
              if (include_replay or r["source"] == "live") and r.get("triggered_at") is not None and r["triggered_at"] <= at]
    stored.sort(key=lambda r: r["source"] != "live")          # a live row wins over its replay twin
    return [x for r in stored for x in pt.rows_at([r], r["triggered_at"]) if x["state"] != "forming" and x["triggered_at"] is not None]


def _alert_rows(con, day) -> list[dict]:
    """The day's alerts, live and replay merged (`alerts_for_day`, which carries the alert id)."""
    return al.alerts_for_day(con, day)


def _pattern_candidates(rows: list[dict], at: dt.datetime, max_age: int | None, pat_stats: dict) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        ref = live_desk.pattern_ref(r)
        if ref in seen or r["state"] == "failed" or r.get("triggered_at") is None or r.get("entry") is None:
            continue
        if not live_desk.gate(r.get("score"), r.get("rr")):
            continue
        conf = pt.confidence(pat_stats, r["kind"], r.get("score"), r["state"])
        if conf and conf.get("hit_rate") is not None and conf["hit_rate"] < live_desk.MIN_CONF:
            continue
        ts = _minute(r["triggered_at"])
        if ts > at or (max_age is not None and live_desk.stale(ts, at, max_age)):
            continue
        seen.add(ref)
        t1 = (r.get("facts") or {}).get("target1")
        entry, stop = float(r["entry"]), float(r["stop"])
        target = float(t1 if t1 is not None else r["target"])
        out.append({"ref": ref, "source": "pattern", "symbol": r["symbol"], "side": r["side"], "ts": ts, "bar": tm.trigger_bar(ts),
                    "entry": entry, "stop": stop, "target": target, "target2": float(r["target"]) if r.get("target") is not None else None,
                    "rr": _rr(entry, stop, target), "score": r.get("score"), "label": LABELS.get(r["kind"], r["kind"]), "kind_or_scan": r["kind"]})
    return out


def _alert_candidates(rows: list[dict], at: dt.datetime, include_replay: bool, max_age: int | None, scans: dict) -> list[dict]:
    rows = sorted((dict(a, ts=_minute(a["ts"])) for a in rows if include_replay or a.get("source") == "live"), key=lambda a: a["ts"])
    out, last = [], {}
    for a in rows:
        pair = (a["scan"], a["symbol"])
        prev = last.get(pair)                                 # the pair's previous alert that got a row
        if a["ts"] > at:
            continue
        if prev is not None and (a["ts"] - prev).total_seconds() < REPEAT_MIN * 60:
            continue                                          # repeat within the hour of the pair's last row
        sc = scans.get(a["scan"]) or {}
        side = sc.get("side") or a.get("side")
        if sc.get("desk") is False or not side or a.get("stop") is None or a.get("target") is None or a.get("price") is None:
            continue
        entry, stop, target = float(a["price"]), float(a["stop"]), float(a["target"])
        rr = _rr(entry, stop, target)
        if rr < desk.ALERT_MIN_RR:
            continue
        last[pair] = a["ts"]                                  # it got its row when it was fresh, so it anchors even when stale now
        if max_age is not None and live_desk.stale(a["ts"], at, max_age):
            continue
        out.append({"ref": f"alert:{a['id']}", "source": "alert", "symbol": a["symbol"], "side": side, "ts": a["ts"], "bar": tm.trigger_bar(a["ts"]),
                    "entry": entry, "stop": stop, "target": target, "target2": None, "rr": rr, "score": None,
                    "label": a["scan"], "kind_or_scan": a["scan"]})
    return out


def candidates(con, day, at: dt.datetime, *, include_replay: bool, max_age: int | None, pat_stats: dict, scans: dict) -> list[dict]:
    """The triggers that get a row at `at`: exactly the desk's filters, sorted by (ts, -rr, -score, symbol)."""
    out = (_pattern_candidates(_pattern_rows_at(con, day, at, include_replay), at, max_age, pat_stats)
           + _alert_candidates(_alert_rows(con, day), at, include_replay, max_age, scans))
    return sorted(out, key=lambda c: (c["ts"], -c["rr"], -(c["score"] or 0), c["symbol"]))


# ---------- odds, size, gate ----------
def load_history(con, symbol: str, day: dt.date, sessions: int = 60) -> dict:
    """history() of the symbol's stored 1-minute candles for the `sessions` stored days before `day`."""
    key = (symbol, day)
    if key in _HIST:
        return _HIST[key]
    start, _ = session_bounds(day)
    since = dt.datetime.combine(day - dt.timedelta(days=HIST_DAYS), dt.time(0), IST)
    with con.cursor() as cur:
        cur.execute("SELECT ts, open, high, low, close FROM candles_1m WHERE symbol=%s AND ts >= %s AND ts < %s ORDER BY ts",
                    (symbol, since, start))
        rows = cur.fetchall()
    frames = {}
    if rows:
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
        df["ts"] = pd.to_datetime(df.ts, utc=True).dt.tz_convert("Asia/Kolkata")
        groups = {d.isoformat(): g.reset_index(drop=True) for d, g in df.groupby(df.ts.dt.date)}
        frames = {d: groups[d] for d in sorted(groups)[-sessions:]}
    hist = tm.history(frames)
    _HIST[key] = hist
    while len(_HIST) > HIST_CACHE_MAX:
        _HIST.pop(next(iter(_HIST)))
    return hist


def build_row(cand: dict, settings: dict, state: dict, hist: dict, *, apply_rules: bool) -> dict:
    """The full trade_rows record for a candidate: odds (None without history), size, gate."""
    ts = cand["ts"]
    day = ts.astimezone(IST).date()
    plan = {"symbol": cand["symbol"], "day": day.isoformat(), "at": cand["bar"], "side": cand["side"],
            "entry": cand["entry"], "stop": cand["stop"], "target": cand["target"], "flat_by": settings["flat_by"]}
    size = shares(cand["entry"], cand["stop"], settings, side=cand["side"])
    size["settings"] = {k: settings[k] for k in DEFAULTS}
    row = {"day": day, "ts": ts, "ref": cand["ref"], "symbol": cand["symbol"], "side": cand["side"], "source": cand["source"],
           "entry": cand["entry"], "stop": cand["stop"], "target": cand["target"], "target2": cand.get("target2"),
           "rr": _rr(cand["entry"], cand["stop"], cand["target"]), "odds": tm.replay(plan, hist), "size": size,
           "gate": None, "gate_reason": None, "outcome": None, "paper": None}
    if apply_rules:
        row["gate"], row["gate_reason"] = gate(row, state, settings)
    return row


# ---------- storage ----------
def insert_row(cur, row: dict) -> int | None:
    """Insert once per (day, ref); the new id, or None when the row already exists."""
    vals = [_dumps(row.get(c)) if c in JSON_COLS else row.get(c) for c in INSERT_COLS]
    cur.execute(INSERT_SQL, vals)
    rec = cur.fetchone()
    return rec[0] if rec else None


def _json_row(rec) -> dict:
    r = dict(zip(COLS, rec))
    if r.get("day") is not None:
        r["day"] = r["day"].isoformat()
    if r.get("ts") is not None:
        r["ts"] = r["ts"].astimezone(IST).isoformat()
    return r


def rows_for_day(con, day, upto: dt.datetime | None = None) -> list[dict]:
    """JSON-safe rows of the day ordered by (ts, id); with `upto`, nothing after that minute leaks."""
    with con.cursor() as cur:
        cur.execute(f"SELECT {','.join(COLS)} FROM trade_rows WHERE day=%s ORDER BY ts, id", (day,))
        rows = [_json_row(rec) for rec in cur.fetchall()]
    return rows if upto is None else _cut(rows, upto)


def _cut(rows: list[dict], upto: dt.datetime) -> list[dict]:
    """Rows as of `upto`: later rows dropped, a later settlement or paper log cleared."""
    out = []
    for r in rows:
        if dt.datetime.fromisoformat(r["ts"]) > upto:
            continue
        r = dict(r)
        o, p = r.get("outcome"), r.get("paper")
        if o and o.get("settled_at") and dt.datetime.fromisoformat(o["settled_at"]) > upto:
            r["outcome"] = None
        if p and p.get("logged_at") and dt.datetime.fromisoformat(p["logged_at"]) > upto:
            r["paper"] = None
        out.append(r)
    return out


def odds_by_ref(con, day, upto: dt.datetime | None = None) -> dict[str, dict]:
    """Trade rows of the day by ref; with `upto`, only rows whose minute is at or before it (as rows_for_day)."""
    sql, params = "SELECT ref, id, odds, size, gate, gate_reason FROM trade_rows WHERE day=%s", (day,)
    if upto is not None:
        sql, params = sql + " AND ts <= %s", (day, upto)
    with con.cursor() as cur:
        cur.execute(sql, params)
        return {ref: {"id": i, "odds": o, "size": s, "gate": g, "gate_reason": gr} for ref, i, o, s, g, gr in cur.fetchall()}


# ---------- views (pure) ----------
def trades_view(rows: list[dict], settings: dict, day: dt.date, at: str) -> dict:
    """The /api/trades body minus `pending`: rows already cut at `at`, the ledger over them."""
    return {"day": day.isoformat(), "at": at, "applied": day >= RULES_FROM, "rules_from": RULES_FROM.isoformat(),
            "settings": settings, "ledger": ledger(rows, settings), "rows": rows}


def attach(by_ref: dict[str, dict], rows: list[dict], kind: str) -> list[dict]:
    """New dicts of `rows` with `odds` and `trade` joined by ref (kind 'pattern' or 'alert'); None when no trade row."""
    out = []
    for r in rows:
        ref = live_desk.pattern_ref(r) if kind == "pattern" else f"alert:{r['id']}"
        t = by_ref.get(ref)
        if t is None:
            out.append({**r, "odds": None, "trade": None})
            continue
        size = t.get("size") or {}
        out.append({**r, "odds": t.get("odds"), "trade": {"id": t["id"], "shares": size.get("shares"), "risk_inr": size.get("risk_inr"),
                                                       "gate": t.get("gate"), "gate_reason": t.get("gate_reason")}})
    return out


def row_by_id(con, row_id: int) -> dict | None:
    with con.cursor() as cur:
        cur.execute(f"SELECT {','.join(COLS)} FROM trade_rows WHERE id=%s", (row_id,))
        rec = cur.fetchone()
    return _json_row(rec) if rec else None


def log_paper(con, row_id: int, note: str) -> dict | None:
    """Log the row as a paper trade once; the row, or None when it is missing or already logged."""
    paper = {"logged_at": dt.datetime.now(IST).isoformat(), "note": note}
    with con.cursor() as cur:
        cur.execute(f"UPDATE trade_rows SET paper=%s WHERE id=%s AND paper IS NULL RETURNING {','.join(COLS)}", (_dumps(paper), row_id))
        rec = cur.fetchone()
    con.commit()
    return _json_row(rec) if rec else None


def update_outcome(cur, row_id: int, outcome: dict) -> None:
    cur.execute("UPDATE trade_rows SET outcome=%s WHERE id=%s", (_dumps(outcome), row_id))


def settle_open(con, day, at: dt.datetime, flat_by: str, final: bool) -> int:
    """Settle the day's open rows on candles before `at`; the number settled."""
    todo = [r for r in rows_for_day(con, day) if r.get("outcome") is None]
    if not todo:
        return 0
    start, _ = session_bounds(day)
    with con.cursor() as cur:
        cur.execute("SELECT symbol, ts, open, high, low, close FROM candles_1m WHERE symbol = ANY(%s) AND ts >= %s AND ts < %s ORDER BY symbol, ts",
                    (sorted({r["symbol"] for r in todo}), start, at))
        recs = cur.fetchall()
    frames = {}
    if recs:
        df = pd.DataFrame(recs, columns=["symbol", "ts", "open", "high", "low", "close"])
        df["ts"] = pd.to_datetime(df.ts, utc=True).dt.tz_convert("Asia/Kolkata")
        frames = {s: g.drop(columns="symbol").reset_index(drop=True) for s, g in df.groupby("symbol")}
    n = 0
    with con.cursor() as cur:
        for r in todo:
            df = frames.get(r["symbol"])
            if df is None:
                continue                                      # no candles to grade on yet
            o = settle(r, df, flat_by, final)
            if o is not None:
                update_outcome(cur, r["id"], o); n += 1
    con.commit()
    return n


def backfill_day(con, day, settings) -> int:
    """Rows for a settled day (odds and size; gate only from RULES_FROM), then settle them all."""
    pat_stats, _ = live_desk.setup_stats(con, dt.datetime.now(IST))
    scans = {s["name"]: s for s, _ in al.scan_files()}
    _, end = session_bounds(day)
    apply_rules = day >= RULES_FROM
    have = {r["ref"] for r in rows_for_day(con, day)}
    n = 0
    for cand in candidates(con, day, end, include_replay=True, max_age=None, pat_stats=pat_stats, scans=scans):
        if cand["ref"] in have:
            continue
        state = ledger(rows_for_day(con, day, upto=cand["ts"]), settings)
        row = build_row(cand, settings, state, load_history(con, cand["symbol"], day), apply_rules=apply_rules)
        with con.cursor() as cur:
            if insert_row(cur, row) is not None:
                n += 1
        con.commit()
    settle_open(con, day, end, settings["flat_by"], final=True)
    return n
