
"""Dry run of the desk pipeline on a stored day with a fake model: exercises context
building, storage, budget and the review maths against real data. Rows are tagged
model='dry-run' so they can be deleted afterwards."""
import os, sys, json, datetime as dt
sys.path.insert(0, "/app")
import psycopg, desk, live_desk as ld
IST = desk.IST
day = dt.date.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-10-16")
sizes = []
def fake(system, user, schema, model, max_tokens=1500, effort=None):
    n = len(json.dumps(user)); sizes.append((schema is desk.VERDICT_SCHEMA and "verdict" or schema is desk.BRIEF_SCHEMA and "brief" or schema is desk.REVIEW_SCHEMA and "review" or "ask", n, len(system)))
    if schema is desk.VERDICT_SCHEMA:
        out = {"call": "take", "confidence": 7, "reasons": [{"mark": "ok", "text": f"dry run: context {n} chars"}, {"mark": "no", "text": "no model was called"}, {"mark": "warn", "text": "delete these rows"}], "exit_rule": "dry run", "book_half_at": None}
    elif schema is desk.BRIEF_SCHEMA:
        out = {"paragraphs": [f"Dry-run brief: context {n} chars, {len(user.get('patterns_since_last', []))} patterns since last, {len(user.get('verdicts_today', []))} verdicts."], "watch": ["dry run"]}
    else:
        out = {"rules_said": f"dry run: {json.dumps(user.get('pnl_summary'))}", "desk_wrong": f"{len(user.get('verdicts', []))} verdicts graded", "tomorrow": "-", "health": [json.dumps(user.get("data_health"))]}
    return {"output": out, "usage": {"input_tokens": n // 4, "output_tokens": 120, "cache_read_input_tokens": len(system) // 4}, "model": "dry-run", "latency_ms": 1}
desk.call_model = fake
R = ld.desk_redis()
with psycopg.connect(os.environ["DATABASE_URL"]) as con:
    for hhmm in ("10:00", "10:30", "11:00", "11:15", "15:40"):
        at = dt.datetime.combine(day, dt.time.fromisoformat(hhmm), IST)
        done, sent = ld.process_minute(con, R, day, at, None, include_replay=True)
        print(hhmm, done, "sent", len(sent))
        for rec in sent[:1]:
            c = rec["context"]; print("   ", rec["ref"], "bars", len(c["bars_5m"]), "rank_row", bool(c["ranking_row"]), "size", c["ticket"]["size"], "record", c["setup_record"])
            print("   TG:", desk.telegram_text(rec).replace("\n", " | "))
    with con.cursor() as cur:
        rows = desk.rows_for_day(cur, day)
    print("stored", {k: sum(1 for r in rows if r["kind"] == k) for k in ("verdict", "brief", "review")}, "errors", [r["error"] for r in rows if r["error"]][:5])
    print("spend ₹", desk.spent(R, day), "context sizes (kind, user chars, system chars):", sorted(set(sizes))[:6], "max", max(sizes, key=lambda x: x[1]))
    rv = next((r for r in rows if r["kind"] == "review"), None)
    if rv: print("review ctx pnl:", rv["context"]["pnl_summary"], rv["context"]["data_health"], "verdicts graded:", sum(1 for v in rv["context"]["verdicts"] if v.get("hit")))
