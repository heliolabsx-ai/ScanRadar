# Calls loop: graded predictions that feed back into the prompt

Date: 28 Oct 2026. Owner: Saurabh. Decided in a design session on the day AMBER's head-and-shoulders short (79 % detector confidence, taken at 6,104) ran against a long build-up above VWAP with no Desk verdict because the budget was spent.

## Intent

Make the Desk's opinion a first-class, graded object. Today a pattern trigger gets a Desk **verdict** (take / wait / skip, confidence 1-10) that nothing scores and nothing learns from. From now on every trigger, and every stock on the Watch list on a 15-minute clock, gets a **call**: direction, confidence, horizon, three reasons, what proves it wrong. The system grades each call from the tape with no human input, keeps a ledger, computes calibration per setup and tape regime, and puts that calibration plus a weekly set of model-written rules back into every prompt. The model's weights never change; what it is shown does.

Saurabh's words for the benefit: "provide me technical edge so I can take informed decision and my chances are improved". The benefit is a second opinion that becomes measurably more careful over weeks, learns to say *no edge*, and stops sending the setups that lose.

## Decisions Saurabh made

- **Both sources of calls**: setup triggers (pattern triggers and rule alerts, as verdicts work today) **and** the Watch list every 15 minutes while a symbol is on it. Not every stock every 5 minutes (not an LLM job).
- **The call replaces the verdict.** One opinion per trigger, always graded. The verdict's ticket and exit rule move into the call. No two-numbers problem. Brief, Ask and Review stay.
- **Today's gates stay in front of a call** (29 Oct 2026). A trigger gets no call when the pattern has already failed, its score or R:R is under the gate, its kind records under `PATTERN_MIN_CONF` (45 %) in its bucket, a rule alert's R:R is under 1.5, the scan says `"desk": false`, or the trigger is older than 10 minutes. Exactly `process_minute`'s gates today; nothing loosened in v1. Gated triggers are recorded as skip rows with the reason (no model cost, as now) and the Calls tab shows them in grey, so nothing is dropped silently. Revisit one gate at a time after 200 graded calls.
- Chosen from a browser mockup (`.superpowers/brainstorm/58907-1793184065/content/calls-loop.html`, `desk-vs-call.html`) and an Archify workflow diagram (`.archify/workflow-calls-loop-20261028-163441/calls-loop.html`).

## Goals

1. Every call is graded automatically at +15, +30, +60 minutes and at stop or target.
2. Calibration per setup and regime, stated confidence vs realised hit rate, visible on the dashboard and injected into every call prompt.
3. Calls under the floor are logged but not sent, so Telegram carries fewer, better messages.
4. The ledger is the single honest scorecard of the Desk's advice.

## Non-goals

- No order placement, ever (unchanged).
- No model fine-tuning. "Update the model" means update the context it is shown.
- No retroactive calls on stored days (cost); the ledger starts the day this ships.
- No editing of the model-written rules from the dashboard in v1 (read-only pane; Saurabh edits `calls_rules` via SQL or asks Claude Code).
- No change to the ranking engine, the pattern detector, or the rule scans.

## Anti-goals (what would make this a failure even if it works)

- Two confidence numbers for one trigger from the same model.
- Telegram noise: four messages an hour per watched stock.
- Showing a stated confidence as if it were calibrated when the bucket has too little data.
- Spending beyond the daily budget, or starving the brief and review.

## Constraints

- Runs inside the api process beside `live_patterns` and `live_desk` (the VPS is small; no new service).
- Secrets only in `.env`. Structured outputs only (`output_config.format` json_schema subset: no numeric bounds, lengths in the prompt).
- Deploy with `docker compose up -d --build --no-deps api` after 15:30; migration 005 applied by hand like 004.
- All times IST; the loop runs on trading days (`core/calendar.py`) 09:20-15:40.

## The call

A call is one row in a new table `calls` (migration `db/migrations/005_calls.sql`), plus the full model record in `desk_calls` with `kind = 'call'` (the existing storage of context, output, usage, cost). `calls` carries the graded summary; `desk_calls` carries the evidence.

```
calls
  id            BIGSERIAL PK
  desk_call_id  BIGINT REFERENCES desk_calls(id)   -- null when the model did not run (budget, error)
  day           DATE, ts TIMESTAMPTZ               -- the minute the call is about
  symbol        TEXT
  source        TEXT      -- 'pattern' | 'alert' | 'watch'
  ref           TEXT      -- pattern:SYM|kind|formed_at · alert:ID · watch:SYM|HH:MM  (unique with day)
  setup         TEXT      -- pattern kind, scan name, or 'watch'
  regime        TEXT      -- see Regime
  side          TEXT      -- the trigger's side for pattern/alert; null for watch (model chooses)
  direction     TEXT      -- 'up' | 'down' | 'no_edge'
  confidence    INT       -- 0-100 as stated by the model
  horizon_min   INT       -- 60, or minutes to 15:20 when fewer remain
  wrong_if      DOUBLE    -- price level the model says invalidates the call (null for no_edge)
  right_if      DOUBLE    -- price level that confirms it (null for no_edge)
  entry, stop, target, target1  DOUBLE  -- the system's ticket (pattern/alert) or null (watch)
  sent          BOOL      -- Telegram went out
  floor         INT       -- the floor applied at the time
  r15, r30, r60 DOUBLE    -- signed favourable % returns (positive = the call's direction paid); null until known
  r_h           DOUBLE    -- return at the horizon
  hit           TEXT      -- 'hit' | 'miss' | 'flat' | null (not yet) ; no_edge calls: 'n/a'
  first         TEXT      -- 'target' | 'stop' | 'none' | null  (only when a ticket exists)
  graded_at     TIMESTAMPTZ
  UNIQUE (day, ref)
```

**Output schema** (`CALL_SCHEMA`, replaces `VERDICT_SCHEMA`):

```
direction    enum up | down | no_edge
confidence   integer           (0-100, meaning: probability the horizon return is favourable; prompt says so)
reasons      exactly three {mark ok|no|warn, text ≤ 90 chars, cites a number}
wrong_if     {level: number|null, text ≤ 90 chars}   a price or bar condition that invalidates the call
right_if     {level: number|null, text ≤ 90 chars}   what confirms it
book_half_at number|null                               kept from the verdict
```

For a pattern or alert trigger the ticket (entry, stop, target, target1, size) stays the system's and is shown to the model, as today. The model's `wrong_if.level` is the exit the Calls tab shows beside the row's stop ("stop 6,141, not 6,220" in the mockup). For a watch call there is no ticket; the model gives `wrong_if` and `right_if` from the levels in the context.

**Direction vs side.** A pattern or alert has a side. The model may answer `no_edge`, or even the opposite direction; that is the point. The grade uses the model's direction, not the trigger's side. `first` (target/stop) is graded on the ticket's side and entry, as today, so the detector's own scorecard is unchanged. **Entry for the call's own returns** (`r15/r30/r60/r_h`) is always the close of the call's minute, the last price the model saw; this holds for trigger calls and watch calls alike (my call, for one consistent definition). When the direction opposes the trigger's side, the card and the Telegram text carry no ticket line: only `wrong_if` / `right_if`, and the headline says "against the SHORT setup". `review_context`'s P&L stays on the ticket for pattern/alert rows (that is the detector's P&L); the calls' own scorecard is the ledger.

**Horizon.** 60 minutes. A call made after 14:20 has horizon = minutes to 15:20. The horizon is fixed by the system, not chosen by the model.

## Regime

Computed by the system at call time from the same context the model sees, so calibration can split by it. Pure function `regime(direction, snap, ranking_row)`:

- `tape`: `with` if breadth agrees with the direction (bulls > bears for up, bears > bulls for down), else `against`, `mixed` within 10 % of each other.
- `oi`: `with` if the ranking row's OI label is long build-up or short covering for up (short build-up or long unwinding for down), `against` for the mirror labels, `none` when no label.
- `vwap`: `with` if price is on the direction's side of VWAP, else `against`.

Stored as one string, e.g. `tape:against|oi:against|vwap:against` (AMBER). Calibration keys on `(setup, tape)` first and the full string second, so buckets fill.

## Grading

A grader runs every minute inside the desk tick after the calls, over `calls` rows whose `ts + horizon_min ≤ now` and `graded_at IS NULL` (plus a pass at 15:25 for everything still open, which grades r15/r30 with what exists and marks `r60`/`r_h` from the last bar). Uses `core.backtest.outcome()` for `r15/r30/r60/first` with `side` = the model's direction (`up` = long), and the ticket's levels when present; for watch calls `first` stays null.

- `hit` = `r_h ≥ +0.15 %`, `miss` = `r_h ≤ −0.15 %`, `flat` between. The band is `CALLS_FLAT_PCT`, default 0.15.
- `no_edge` calls get `r15/r30/r60` recorded for the long side (so a missed move is visible) and `hit = 'n/a'`.
- A call made with fewer than 15 minutes to 15:20 is graded `flat` with whatever returns exist; it still counts in n.

`core.backtest.outcome()` gives `r15/r30/r60`; `r_h` for a non-standard horizon comes from a small new helper `ret_after(df1m, at, side, entry, minutes)` added to `core/backtest.py` (core-only module, no engine copy to keep in sync; my call).

Grading is idempotent and re-runnable (`scripts/grade_calls.py --day`), so a restart loses nothing.

**Declined calls are graded too (my call, from the 28 Oct review).** On 28 Oct the desk skipped every VWAP-reclaim long with the same lead reason (the scan's 29.46 % historical hit rate plus breadth); those skips would have made +40,848 while the takes lost. A `no_edge` call therefore still gets `r15/r30/r60`, `r_h` and `first` on the **trigger's** side (the ticket it declined), with `hit = 'n/a'` so it never enters the calibration numbers. The ledger shows a separate line, "declined calls that would have hit: X of Y", and the three most recent of these go into the prompt beside the three recent misses. Saying no has a measured cost, not just saying yes. Watch-list `no_edge` rows have no trigger side and stay ungraded.

## Calibration

`calls_stats(con, days=30)` in `api/calls.py` (pure over rows, so it is testable), returns per setup:

- overall: n, hit rate (hits / (hits + misses), flats excluded), avg r60, avg stated confidence, **calibration gap** = stated − realised.
- by `tape` regime (with / against / mixed), same fields.
- by stated-confidence bucket of 10 (50-59, 60-69, …): n and realised hit rate.
- for the ledger row: `calibrated` = realised hit rate of the (setup, tape, bucket) cell when its n ≥ 10, else of (setup, tape) when n ≥ 10, else null (shown as "uncalibrated").

**The floor.** A call is sent (Telegram, and marked `sent`) when `direction ≠ no_edge` and `confidence ≥ floor`. `floor = CALLS_FLOOR` (default 55), raised per (setup, tape) to `max(floor, 100 − realised + 5)`? No: keep it simple and explainable. The floor is one number from `.env`; calibration changes the *model's* confidence through the prompt, and the ledger shows whether it is learning. Revisit after the first 200 calls.

**What the prompt gets.** `calibration` block in every call context: the setup's overall and per-tape-regime hit rates with n, the model's gap, the three most recent misses on this setup (symbol, day, direction, confidence, r60, one-line reason it gave), and the active rules (below). This is what "continuous update" means here. **One lesson from 28 Oct:** a hit-rate number handed to the model became a reason by itself (every reclaim long skipped on "29.46 % over 2378 fires" while the scan went 44 of 97 that day). The prompt must present calibration as a correction to the model's *own* confidence on *its own* past calls, never as the setup's verdict, and the per-call context must say what the setup did **today so far** (fires, targets, stops by the as-of minute) next to the long-run rate. Builder's wording, but both numbers must be there. Cached-prompt friendly: the stable text stays in the system prompt; the calibration block is part of the per-call user message.

## Weekly lessons

Job `kind = 'lessons'`, run on the last trading day of the week after the review (15:40), model = `DESK_MODEL`. Input: the week's ledger (every graded call, compact) plus the current rules. Output schema: `rules` (up to 6, each ≤ 120 chars, concrete: a setup, a regime, a number, an instruction such as "say no_edge" or "cap confidence at 45"), `dropped` (rule ids no longer supported, with one line why), `note` (≤ 60 words). The output replaces the active rule set, stored in `calls_rules` (`id, week_of DATE, rules JSONB, desk_call_id`); the newest row is active. Rules go into every call prompt as "Rules you wrote from your own misses". A rule can only lower confidence or say no_edge; the prompt says so, and the review of a week shows how often each rule fired (counted when a call's reasons mention the rule id; best effort).

## Watch-list calls

- The dashboard's Watch list lives in `localStorage` today. Add `PUT /api/watchlist` (`{symbols: [...]}`) called by `web/watch.js` whenever the list changes and on page load; the api keeps it in Redis `watch:list` (JSON, no TTL). `GET /api/watchlist` returns it. The browser copy stays the source of truth; the server copy is what the loop reads.
- Every 15 minutes from 09:32 to 15:02 (xx:02, :17, :32, :47, offset so the pass never lands on the brief's minute), **one model request judges the whole list** and fans out into one `calls` row per symbol (`source = 'watch'`, `setup = 'watch'`, `ref = watch:SYM|HH:MM`, all rows sharing one `desk_call_id`). Per-symbol requests would be 230 a day for 10 names and blow the budget by mid-morning (reviewer's arithmetic; my call to batch). `WATCH_SCHEMA` = `{calls: [{symbol, direction, confidence, reasons, wrong_if, right_if}]}`, one entry per symbol in the request, the prompt says so. Context per symbol = the `watch_row` (analyse row + rank), last 12 five-minute bars, prev-day levels, **the previous call on this symbol today** (so it can change its mind and say so); shared once per request: breadth, sectors, calibration for `watch`, the rules. The list is capped at `WATCH_MAX` (default 12) symbols per request; beyond that the oldest entries are dropped from the pass, not from the list.
- Telegram for watch calls only when the direction differs from the previous call on that symbol, or the call crosses the floor in either direction. The Calls tab shows them all.
- Budget share: watch calls stop at 60 % of the budget (`WATCH_SHARE`), so trigger calls keep the rest. 23 requests a day at roughly ₹3-5 each on Opus for a 10-name list.

## Budget and model

**What 28 Oct 2026 actually cost** (checked in `desk_calls` on 29 Oct, the paste of the 15:40 review prompted the check): 47 paid verdicts between 09:20 and 10:30 cost ₹81.27, about ₹1.7 each on Opus 5.5 with cached prompts. The rule-alert share (60 %) was reached at 09:47 and 237 rule alerts after that got no verdict; the whole budget was reached at 10:30 (₹97 of ₹100) and 24 pattern triggers after that got none, while the detector kept triggering until 14:00 (191 live triggers in the day) and rule alerts ran to 15:29 (1,080 in the day). So "no desk coverage after 10:30" in the review is the budget, not a fault in the loop. The earlier assumption in this document ("40-80 calls a day fit ₹200") was wrong.

Decisions:

- `DESK_BUDGET_INR` moves from 100 to 200 in Saurabh's `.env` (his call). Shares: watch 60 %, alert calls 60 %, brief 70 %, all calls 95 %, review and lessons always have the rest.
- **One call per (scan, symbol) per day for rule alerts, unless 60 minutes have passed since that symbol's last call on that scan** (my call). Bounce-failure short fired 204 times and decline-below-SMA13 396 times on 28 Oct, mostly repeat fires on the same names; today each repeat competes for the share. Repeats inside the hour are recorded as skip rows with reason `repeat`. Pattern triggers are already one per (symbol, kind) per day.
- `DESK_CALL_MODEL` (default = `DESK_MODEL`, Opus 5.5) picks the model for calls and lessons, so moving calls to Sonnet 5.5 is a `.env` change, not a deploy. Expect that on a day like 28 Oct, Opus at ₹200 still runs out around midday once watch passes are added: roughly 70 gated pattern triggers plus the deduped rule alerts at ₹1.7 and 23 watch passes at ₹3-5. The builder prints the first live day's per-kind cost in `scripts/day_report.py`; the model decision is Saurabh's after that day, with numbers.
- `DESK_BRIEF_MODEL` may drop to Sonnet 5.5 (builder's call; brief quality is not graded).
- Budget exhaustion is recorded per skipped trigger as today (`skip_row`), and the Calls tab shows "N triggers had no call: budget" so the 28 Oct silence cannot repeat unnoticed.

## Dashboard

The Desk tab (`#desk`, `web/desk.js`) becomes the Calls tab in content; the tab label reads "Desk · Calls" (my call, keeps the URL). Three panes from the mockup:

1. **Live calls** (today, as-of minute): one card per call, direction pill, confidence (with the calibrated figure beside it when it exists, grey "uncalibrated" when not), three reasons, wrong-if / right-if line, "if you take it" line from the ticket with the model's exit beside the row's stop, and the running grade line (+15 ✓ · +30 ✗ · +60 pending). No-edge calls shown collapsed. Budget-skipped triggers listed in grey.
2. **Ledger** (last 30 sessions, `GET /api/calls?days=30` and `?day=`): day, symbol, setup, direction, confidence, r15/r30/r60, first, grade. Click a row → `enterReplay(day, hh:mm)` on the Scanner tab with the call's reasons in the detail panel (reuse the alert-jump path).
3. **Calibration** (`GET /api/calls/stats?days=30`): per setup, stated tick vs realised bar, n; per tape regime; the active rules with "fired N×" counts; the lessons note.

Mac notifications (`web/notify.js`) fire for sent calls, as they do for verdicts now. The Watch drawer's "Desk" button opens the Calls tab filtered to that symbol.

## Routes

- `GET /api/desk?day&at` keeps its shape for brief/ask/review; verdict rows are gone from it after the switch (the day of the switch is the boundary; older days still render verdicts from `desk_calls` as today, read-only).
- `GET /api/calls?day&at` → `{rows, skipped, floor}`; `GET /api/calls?days=N` → ledger; `GET /api/calls/stats?days=N` → calibration + rules.
- `PUT /api/watchlist`, `GET /api/watchlist`.
- `WS /ws/scan` keeps forwarding `desk:updated`; the payload gains `calls` counts.

## Telegram text

```
AMBER ↓ DOWN 52 % (floor 55: not sent)        <- never sent, shown here for shape
KEI ↑ UP 71 % · B0101 09:19 · 60 min
 ok  bar at the day's low on 3.4x volume
 ok  sector +0.8 %, tape 128/85 long
 warn 09h B0101 longs record 19 %
wrong if 5m close < 4,312 · right if > 4,380 on volume
ticket 4,331 / stop 4,312 / T1 4,369 · 1 lot risk ₹1,425
```

## What changes in existing code

- `api/desk.py`: `CALL_SCHEMA`, `CALL_SYSTEM` (verdict prompt rewritten: direction/confidence-as-probability/wrong-if/right-if, the calibration block explained, the rules), `call_context()` (verdict_context + calibration + previous watch call), `telegram_text` for calls, `review_context` reads calls (`desk_wrong` becomes "calls that missed and why"). Verdict code paths are removed, not kept behind a flag; tests for them are rewritten for calls.
- `api/live_desk.py`: `process_minute` makes calls instead of verdicts, adds the watch pass on the quarter hour, runs the grader, runs lessons on the week's last trading day after the review.
- `api/calls.py` (new): `regime()`, `grade()`, `calls_stats()`, `floor_gate()`, `watch_due()`, `watch_telegram_due()`, row mapping, SQL.
- `db/migrations/005_calls.sql`: `calls`, `calls_rules`.
- `web/desk.js` + `web/index.html`: the three panes; `web/watch.js`: watchlist sync. Bump the `?v=` cache tokens.
- `scripts/grade_calls.py`: re-grade a day; `scripts/day_report.py` adds the calls line.
- `README.md`, `CLAUDE.md` (Desk section), `.env.example` (`DESK_BUDGET_INR=200`, `CALLS_FLOOR=55`, `CALLS_FLAT_PCT=0.15`, `WATCH_SHARE=0.6`).

## Tests

- `tests/test_calls.py`: regime for the AMBER facts; grade hit/miss/flat/n-a and the 15:20 cut; `calls_stats` on a synthetic ledger (buckets, gap, the n ≥ 10 rule); floor gate; watch due on the quarter hour only; watch Telegram dedupe (direction change, floor crossing); the lessons output replaces rules.
- `tests/test_desk.py`: rewritten for `CALL_SCHEMA`/`call_context` (schema subset: no bounds), budget shares including watch.
- `tests/web/test_desk.mjs`: card headline, calibrated-vs-uncalibrated rendering, ledger row, notification text.
- Suite stays green: `.venv/bin/python -m pytest tests -q`, `node --test tests/web/`.

## Rollout

1. Deploy after 15:30 with `--no-deps api`; apply migration 005; set the env values.
2. First live day: watch spend per kind; on a 28 Oct-like day expect 60-90 trigger calls after the repeat rule plus 23 watch passes, and expect Opus to hit ₹200 before the close. Decide the call model from that day's numbers.
3. After ~200 graded calls (three to four weeks) read the calibration pane and decide: keep Opus, move to Sonnet, change the floor, or rework regime keys. The ledger makes that a numbers decision.

## Noticed in the 28 Oct review, not part of this design

- The review context lists patterns only to 11:05 and out of time order, and its pattern-row P&L differs from the verdict-row P&L for the same trade (LT -10,710 vs -10,535). The pattern rows grade T1 from the detector's levels while the verdict grades the ticket; the ordering and the cut-off are in `review_context`. Worth a separate small fix so the 15:40 review stops reporting them as health problems.
- Briefs kept running to 15:15 on 28 Oct with spend past 70 %; check the brief share logic separately.

## Left to the builder

- Exact SQL for the stats query vs computing in pandas over the rows (rows are few; pandas is fine).
- How the ledger row jump reuses the alert-jump code path.
- Whether the lessons job also runs when the last trading day of the week is a holiday-shifted Thursday (use `core/calendar.py`'s next-trading-day logic; any correct choice is fine).
- Prompt wording, within the rules above.
