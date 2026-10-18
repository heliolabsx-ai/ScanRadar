# Desk (Claude integration) — design, 18 Oct 2026

Goal: Claude acts as an intraday desk on top of the scanner for the first live session
(Mon 19 Oct 2026). It never trades. It reads the same numbers the dashboard shows and
returns fixed structures, never free text, inside a hard daily rupee budget.

## What it does

| Job | When | Input | Output (structured) |
|---|---|---|---|
| **Verdict** | every new pattern trigger and every rule alert, within ~20 s of the minute | trigger + the symbol's ranking row, sector, breadth, prev-day refs, last 12 five-minute bars, setup record (hit rate), risk ₹ and lot | `call` take/wait/skip, `confidence` 1–10, three `reasons` (ok/no/warn + ≤90 chars), `exit_rule`, optional `book_half_at` |
| **Brief** | 09:30 and every 15 min to 15:15 | breadth, sectors, top 5 bulls/bears, patterns since last brief, verdicts so far, previous brief | 2–3 `paragraphs`, `watch` list |
| **Ask** | on demand from the tab | question + snapshot/patterns/alerts at the slider minute | `answer` ≤120 words, `used` |
| **Review** | 15:40 on a trading day, or on demand for any stored day | alerts and triggers with outcomes, verdicts vs outcomes, P&L at shown size, candle count, spend | `rules_said`, `desk_wrong`, `tomorrow`, `health` |

Ticket numbers (entry/stop/target/size/R:R) are the system's, never the model's.
Every card shows `blind to: news · index level · order book` — the model only sees what is listed above.

## Cost control

- `DESK_BUDGET_INR` (default 100) per day in Redis `desk:spend:{day}`; cost per call from `usage` × price table × `DESK_USDINR` (default 88). Hard stop: verdicts stop at 100 %, briefs at 70 % so verdicts keep the rest. Review and ask run only if the budget allows (ask: `DESK_ASK_INR` cap 3).
- `DESK_MODEL` default `claude-opus-5-5` (full potential for the test); `DESK_BRIEF_MODEL` defaults to the same. `DESK_EFFORT` default `medium`. One stable, cached system prompt per job.
- `DESK_MIN_SCORE` (default 0 = every trigger) and `DESK_MIN_RR` (default 0) gate verdicts.
- No API key or `DESK=0` → the tab shows "desk off"; everything else unchanged.

## Storage

`desk_calls(id, day, kind verdict|brief|ask|review, ts, symbol, ref, model, context JSONB, output JSONB, usage JSONB, cost_inr, latency_ms, error)`; `ref` = `pattern:{symbol}|{kind}|{formed_at}` or `alert:{id}` or `brief:{HH:MM}` or `review`. UNIQUE (day, kind, ref) so a verdict is made once.

## Delivery

- Telegram per verdict: `TAKE 7/10 · TATASTEEL · BUY · double bottom / Entry … Stop … Target … Size … / ✓ … · ✗ … · ! … / Exit: … / HH:MM · scanradar.tech`.
- Dashboard **Desk** tab: budget meter, verdict cards (newest first, follow the day/at slider), latest brief + ask box, review. Mac notification per verdict when the bell is on. `desk:updated` on the WebSocket.
- API: `GET /api/desk?day&at` → `{day, at, enabled, model, spend, budget, verdicts, briefs, review}`; `POST /api/desk/ask {q, day, at}`; `POST /api/desk/review?day=`.

## Out of scope

Order placement, news, option chain, index level, anything outside the stored candles.
