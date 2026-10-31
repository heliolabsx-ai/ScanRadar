# Trade loop: Time Machine odds, share sizing, day risk gate, Trades tab

Date: 30 Oct 2026. Owner: Saurabh Verma. Status: draft for approval.

## Intent

When a pattern triggers or a rule scan fires with a stop and a target, Saurabh wants to know, in the
same minute and on the same screen, three things ScanRadar cannot tell him today:

1. **The odds of this exact plan on this stock.** Not the setup's average hit rate across all stocks
   (the Conf column), but how often *this* stock, from *this* minute, has gone the distance to *this*
   target before *this* stop. Answered by replaying the rest of the session from the stock's own past
   sessions (the "Time Machine", borrowed from Jake Schincariol's day-trader-skill pack, MIT) and by
   applying the same plan to each real past session.
2. **How many shares.** Saurabh trades cash equity shares of F&O-listed stocks, never lots. Size =
   max loss per trade ÷ distance to stop, capped by capital. The dashboard's lot-based "qty" and
   "one-lot risk" are wrong for him and go away.
3. **Whether his own rules still allow a trade.** A day risk limit and a trades-per-day cap, applied
   across everything taken so far, visible before the entry, not discovered in the P&L at 15:30.

A new **Trades** tab is the money view: settings, the day's risk ledger, every trigger of the day
sized and gated, a paper log. The odds also appear wherever a trigger already shows (Patterns table,
detail drawers, Telegram, the desk's verdict context) so nobody has to change tabs to see them.

The demo that motivated this (28 Oct, real candles, throwaway code): AMBER head-and-shoulders short
at 10:55 replayed as T1 first 30 %, stop first 4 %, flat 66 %; the desk said TAKE 68 and the day
stopped it out. KFINTECH triple bottom at 11:05: T1 39 %, stop 42 %, a coin flip at R:R 0.84; it hit.
LT triple top at 13:25: 92 % of replays reached 15:20 touching neither level; it did exactly that.

## Decisions Saurabh made

- Build the trade loop **inside ScanRadar**, not as separate chat skills (30 Oct 2026).
- **Shares, not lots.** He trades cash shares of stocks on the F&O list only; the 213-name universe
  is exactly his tradable set and ingest does not change (30 Oct 2026).
- Everything existing stays as it is: ingest, scanners, detector, Patterns/Scanner/Desk/Watch tabs,
  Telegram triggers, desk verdicts and briefs. The work is additive; the one change to existing
  screens is lots → shares (30 Oct 2026, "does it change my old setup" → no).
- A new Trades tab plus the odds in the existing places (mockup `trades-tab.html` shown 30 Oct;
  "build it").

## My calls (Saurabh to check while reading)

Saurabh did not give his capital, max loss per trade, day limit, trades-per-day cap, or whether he
trades intraday with leverage or delivery. These are **settings**, editable on the Trades tab, so the
build does not depend on the answers. Defaults until he sets them:

| Setting | Default | Note |
|---|---|---|
| `capital` | ₹5,00,000 | position value never exceeds `capital × position_cap` |
| `max_loss` per trade | ₹2,000 | same number as the header's risk-per-trade input today; the two become one setting |
| `day_loss` limit | ₹6,000 | 3 × max loss |
| `max_trades` per day | 5 | |
| `position_cap` | 60 % of capital in one name | |
| `product` | intraday | intraday allows short rows; delivery hides shorts from sizing (they still show with odds) |
| `flat_by` | 15:20 | matches pattern grading and the detector's `last_trigger` logic |

Other calls:

- The gate **blocks**: sizing reads "0 shares, day limit reached", the desk is told not to write
  TAKE, and the Telegram trigger line says BLOCK. Triggers, odds and charts still show.
- Odds feed the desk's verdict context (option B on the demo page).
- Ranking picks are **not** sized or replayed; only pattern triggers and rule alerts carrying a stop
  and a target.
- **Which triggers enter the queue (reviewer's question 1).** Exactly the ones the desk would judge
  today, i.e. `live_desk.process_minute`'s own filters, the same "keep all filters" decision Saurabh
  made for the calls loop on 29 Oct: patterns with state not failed, score/R:R gate, confidence ≥
  `PATTERN_MIN_CONF` (45), not stale; rule alerts from scans without `"desk": false`, R:R ≥
  `DESK_ALERT_MIN_RR` (1.5), and **one row per (scan, symbol) per day unless 60 minutes have passed**
  (the repeat rule from the calls-loop design, applied here too). That is roughly 40–80 rows a day
  instead of the raw 400–600, well inside the per-minute replay budget. Triggers outside the set get
  no odds, no row, and their Telegram text is unchanged. The desk budget is *not* a queue filter: a
  trigger the desk skips for money still gets a row.
- **Target for a pattern row is T1** (`facts.target1`, the measured move), the level the 42-day hit
  rates are quoted on. T2 (`target`, the pivot) is shown in the ticket as the runner and is not graded
  in the ledger. Rule alerts have one target. `trade_rows.target` therefore holds T1 for patterns.
- **Order inside one minute:** rows are processed by R:R descending, then score descending, then
  symbol. The odds verdict does **not** feed the gate; the gate is Saurabh's rules, the odds are
  information. A `bad` or `flat` row can PASS.
- **A second trigger on a symbol that already has an open row** (pattern plus B0101, double top then
  triple top, two scans) is BLOCK with reason "already in LT since 10:55". The open set is per symbol;
  `position_cap` is checked against the symbol's open position plus the new row.
- **Past days get odds only.** `_prepare_day` backfills `trade_rows` for a settled day with odds and
  size but `gate = NULL` and no ledger; the Trades tab shows such a day with the strip reading "rules
  not applied (before 3 Nov 2026)". Live rows written from go-live are never re-gated when settings
  change. Rows come from stored pattern rows deduplicated by `ref` and from `merge_live_replay`'d
  alerts, so a fire is never counted twice. `backtest_odds.py` reads the same.
- **No stop editor in v1.** `/api/odds` is dropped; the drawers read odds from the stored row.
- Monte Carlo paths: 4,000 per plan (the demo used 10,000; 4,000 gives the same first decimal and
  keeps a trigger-minute batch under a second per plan on the VPS).

## Goals

- Odds and a one-word verdict for every trigger that carries stop and target, within the minute it
  fires, in the Patterns table, the detail drawers, the Trades tab, Telegram and the desk context.
- Share sizing from max loss everywhere a size is shown; "lot" disappears from the UI.
- A day ledger: risk committed, realised P&L at stop/T1 fills, trades taken, gate state, and the
  count of blocked triggers that went on to hit their target (the gate's honest cost).
- Replay days work the same way at any as-of minute, with the ledger rebuilt from that day's rows.
- Every odds computation is stored, so the end-of-day review and the weekly stats can grade the
  odds themselves (did 40 % plans hit 40 % of the time?).

## Non-goals

- No order placement. No broker integration. The ticket is text; the buttons are "Log as paper
  trade" and "Ask the desk".
- No news, results calendar, insider or bulk-deal feeds (second project).
- No change to ingest, the universe, the detector, the rule DSL or the ranking.
- No per-stock tuning of the replay (same method for every symbol).

## Anti-goals

- Presenting the odds as a forecast. The verdict text must say "replays" and "past sessions", never
  "will".
- A gate that silently drops triggers. Blocked rows stay visible, grey, with the reason.
- Sizing that refuses a trade because of a lot; or any UI that still says "lot".
- A replay that leaks the future: paths start at the trigger bar's close and borrow only bars from
  sessions **before** the trigger day; the "same plan on past sessions" check uses only each past
  session's own bars.

## Constraints

- VPS is KVM 2 (2 vCPU, 8 GB). The API process already runs the pattern loop, the desk loop and
  request handling. The replay must be cheap: pure numpy on pre-aggregated 5-minute bars, cached per
  (symbol, day).
- Deploy pattern: web files ship on `git pull`; the API rebuilds with `--no-deps` in session or after
  15:30; ingest is never restarted.
- Web helper scripts are cache-busted with `?v=<hash>` in `index.html`; bump on every change.
- All times IST; candle `ts` is the minute start.

## The replay (`core/timemachine.py`)

Input: `plan = {symbol, day, at (bar close minute, HH:MM), side, entry, stop, target}` and the
stock's 1-minute candles for the day up to `at` plus its previous sessions.

**Bars.** 1-minute → 5-minute, 09:15 anchor, key = bar start. Past sessions = the previous
`sessions` stored days for the symbol (default 60, uses what exists; today 51). Cached per
(symbol, day) in the API process as a dict `{HH:MM: ndarray[n_sessions, (o,h,l,c)]}`; built on the
first trigger of the symbol that day, ~19k rows read once.

**Monte Carlo.** `N` paths. Each path starts at `entry`. For each future 5-minute slot `k` from the
bar after `at` to the last bar before `flat_by`: draw a random past session that has slot `k`, take
its bar's ratios `h/o, l/o, c/o`, apply to the running price to get the path's `(h, l, c)` for that
slot, set the running price to `c`. First touch decides: long hits stop if `l ≤ stop`, target if
`h ≥ target`; short mirrored; both in one bar = stop. Untouched at `flat_by` = `none`, exit at the
last close. Seeded (`seed = 19`, fixed) so the same plan always gives the same numbers.

Output: `{target_pct, stop_pct, none_pct, avg_r, close_r: {p10, p50, p90}, median_tt_min, paths,
sessions, bars_left}` where `avg_r` is the mean signed R multiple at exit (target = +rr, stop = −1,
none = signed distance at the close ÷ risk).

**Same plan on past sessions.** For each past session that has a bar at `at`: enter at that bar's
close, stop and target at the same **percentage** distances as the plan, same first-touch rule to
`flat_by`. Output `{target, stop, none}` counts and the last 12 as `[day, result, minutes]`.

**Verdict word** (`verdict(mc, rr)`), one of four, with a fixed sentence template:

| Condition (checked in order) | Word | Sentence |
|---|---|---|
| `none_pct ≥ 75` | `flat` | Does not resolve before the close in {none} % of replays: they reach {flat_by} with neither level touched. |
| `|target_pct − stop_pct| < 8` | `coin` | Close to a coin flip: T1 first {t} %, stop first {s} %, average {avg_r} R. |
| `target_pct > stop_pct` | `edge` | Odds favour T1: {t} % against {s} %, average {avg_r} R. |
| else | `bad` | Odds favour the stop: {s} % against {t} %. |

**Honesty check.** `tests/test_timemachine.py` asserts: no future leak (a path never uses a bar
from the trigger day or later); seed determinism; both-touched-is-stop; a plan whose target is
inside the first bar's typical range gets `target_pct` near 100; the "same plan on past sessions"
result for one hand-built session equals `core.backtest.outcome`.

## Sizing (`core/sizing.py`)

```
shares(entry, stop, settings) -> {shares, risk_inr, position_inr, limited_by}
```
`shares = floor(max_loss / |entry − stop|)`, then `min(shares, floor(capital × position_cap /
entry))`. `limited_by` ∈ {`max_loss`, `position_cap`, `zero_distance`}. Delivery product and a short
side → `shares = 0`, `limited_by = 'delivery_no_short'`. No lots anywhere. Replaces
`desk.size_for` (the desk's ticket shows shares) and the two lot computations in `index.html`
(detail panel line ~363, pattern detail line ~801) and the Watch add-box "lot" hint.

## The day gate (`core/gate.py` + Redis)

State per day in Redis hash `trades:{day}`: `committed` (sum of `risk_inr` of logged, still-open
rows), `realised` (P&L of closed rows at stop/T1 fills), `taken` (count of PASS), plus a list of the
PASSed refs. Rebuilt from the `trade_rows` table on API start and for any replay day (the Redis hash
is a cache of the table, never the source).

`gate(row, state, settings) -> ('PASS'|'BLOCK', reason)`:

1. `taken ≥ max_trades` → BLOCK "5 trades today".
2. `committed + realised_losses + row.risk_inr > day_loss` → BLOCK "₹5,966 of ₹6,000 day risk committed".
3. `shares == 0` → BLOCK with `limited_by` text.
4. an open logged row on the same symbol → BLOCK "already in LT since 10:55".
5. else PASS. Nothing changes in the ledger until Saurabh logs the row; then `committed += risk_inr`,
   `taken += 1`, the symbol joins the open set.

A row leaves `committed` when its outcome settles (stop, target, or `flat_by`), moving its P&L at
the fill into `realised`. Outcome grading reuses `core.backtest.outcome` on the day's candles, run
each minute for open rows by the Trades loop. `realised_losses` = negative realised only (a win does
not buy back room; Saurabh can change that in settings later, not in v1).

**Pending Saurabh's answer (reviewer's question 2): what does the ledger count?**
Option A, *passed triggers*: every PASS consumes a slot and day room whether or not he takes it; with
40–80 rows a day the gate closes by count within the first hour and measures the rules, not his
trading. Option B, *logged paper trades* (recommended): the ledger counts only rows he clicked "Log
as paper trade" on; PASS then means "your rules allow this now, if you take it", `blocked_won`
measures what the rules would have cost him given what he actually took, and replay days with no
clicks show odds only. The gate functions are the same; only the source of `committed`, `taken` and
the per-symbol open set differs. The document below is written for option B.

PASS/BLOCK is **advisory in v1**: it does not prevent anything mechanical because nothing is placed.
It does change three things: the row's size reads 0 on BLOCK; the desk context carries
`gate: {state, reason}` and the verdict system prompt says a BLOCK means no TAKE; the Telegram
trigger line carries BLOCK.

"Taken" means **logged as a paper trade** (option B above). A row is open from its log click until
its outcome settles at stop, T1 or `flat_by`; grading slices the day's candles at `flat_by` before
calling `core.backtest.outcome`, which otherwise runs to the last candle.

## Storage (`db/migrations/005_trades.sql`, idempotent, mirrored into `db/init.sql`)

```
trade_rows (
  id BIGSERIAL PRIMARY KEY,
  day DATE NOT NULL, ts TIMESTAMPTZ NOT NULL,          -- trigger minute
  ref TEXT NOT NULL,                                    -- pattern:SYM|kind|formed_at · alert:ID (same refs as desk_calls)
  symbol TEXT NOT NULL, side TEXT NOT NULL, source TEXT NOT NULL,   -- pattern | alert
  entry DOUBLE PRECISION, stop DOUBLE PRECISION, target DOUBLE PRECISION, rr DOUBLE PRECISION,
  odds JSONB,                                           -- replay output + past counts + verdict word
  size JSONB,                                           -- shares, risk_inr, position_inr, limited_by, settings snapshot
  gate TEXT, gate_reason TEXT,
  outcome JSONB,                                        -- core.backtest.outcome + pnl_inr at fill, settled_at
  paper JSONB,                                          -- {logged_at, note} when Saurabh clicks "Log as paper trade"
  UNIQUE (day, ref)
);
CREATE INDEX trade_rows_day ON trade_rows (day, ts);
trade_settings (key TEXT PRIMARY KEY, value JSONB, updated_at TIMESTAMPTZ)   -- one row 'current'
```

As shipped (31 Oct 2026): `trade_rows` also has `target2 DOUBLE PRECISION` (the pattern's pivot target, the runner, not graded; `target` holds T1), and `rows_for_day` adds a computed `label` (pattern display name or scan name), not a stored column.

Settings live in Postgres (not `localStorage`) because the API loop needs them. The header's
risk-per-trade input is removed; `max_loss` on the Trades tab replaces it, and the detail panels read
it from `/api/trades/settings`.

## Loops and routes (API process)

- **Trades loop** (`api/live_trades.py`): runs each minute right after the pattern tick and before
  the desk tick (so the desk sees odds and gate). For every new pattern trigger and rule alert with
  stop and target since the last minute (same selectors as `live_desk.new_pattern_triggers` /
  `new_alerts`): replay, size, gate, insert `trade_rows`, update Redis, publish `{"type":"trades"}`
  on the WebSocket. Then grade open rows. Hours 09:20–15:30 on trading days.
- `GET /api/trades?day&at` → `{settings, ledger: {committed, realised, taken, gate_state,
  blocked_won, blocked_total}, rows}` cut at `at`; replay days computed from the stored rows (the
  first request for a settled day runs the replay for that day's triggers if missing, in the same
  background `_prepare_day`).
- `GET /api/trades/settings`, `PUT /api/trades/settings` (JSON body; validation: all positive,
  `day_loss ≥ max_loss`, `product ∈ {intraday, delivery}`).
- `POST /api/trades/{id}/paper` body `{note}` → sets `paper`.
- `GET /api/odds?symbol&day&at&side&entry&stop&target` → replay for an ad-hoc plan (used by the
  detail drawer when a user edits the stop in the panel; cheap, cached by its arguments 5 min).
- `/api/patterns` rows gain `odds` (joined from `trade_rows` by ref) so the Patterns table column
  needs no second request. `/api/alerts` rows likewise.
- Desk: `verdict_context` gains `odds` and `gate`; `size` becomes the shares dict. Verdict system
  prompt gains two sentences: the odds are replays of the stock's own past, to be argued with, not
  obeyed; a BLOCK means the day's rules are spent and the call cannot be TAKE.
- Telegram pattern-trigger and rule-alert messages gain one line:
  `T1 39 % · stop 42 % · coin flip · 229 sh · PASS` (or `BLOCK: 5 trades today`).
- As shipped (31 Oct 2026): `trades:{day}:{ref}` holds the row's odds/size/gate JSON, or an empty string (tombstone) for a live alert that gets no row, so its deferred message in `tg:defer:{day}` goes out at once without the odds line; `tradesdone:{day}` marks a settled day whose backfill finished.

## Dashboard

**Trades tab** (`#trades`, helpers in `web/trades.js` with node tests `tests/web/test_trades.mjs`):

- Settings strip: six tiles as in the mockup (capital, max loss, day risk used/limit with a meter,
  trades taken/cap with gate OPEN/CLOSED, paper P&L, blocked-that-won). Capital and max loss tiles
  are editable in place; a gear opens the full settings form (all seven settings).
- Queue table, newest on top in LIVE, time order in REPLAY: time, stock + side, setup + score or
  scan name, entry with stop/T1 under it, odds mini-bar (green/red on grey) with the verdict word,
  shares with position value, risk ₹, gate pill with reason, outcome with P&L once settled. Blocked
  rows grey. Rows respect the view filter strip (sector, price band...) like cards and alerts do.
- Drawer on row click: the chart via `wireChart`/`panelEl()` as the Watch drawer does, with the
  pattern or alert levels drawn; full odds bar; "same plan on past sessions" counts and 12 squares;
  verdict sentence; plain-text ticket; buttons **Log as paper trade** (note field) and **Ask the
  desk** (opens the Desk tab's ask box pre-filled with the ticket). No order button, ever.
- As-of slider and date selector apply as on other tabs.

**Elsewhere:**

- Patterns table: one new column **Odds** (`39/42` with the verdict word's colour; title shows the
  sentence). Patterns detail panel: odds bar under the chart; Qty line becomes shares.
- Scanner detail panel: Size block becomes shares (`229 sh · ₹1,95,703 · loss at stop ₹1,992`).
  Alert list rows carry the odds pair.
- Watch cards: event chips already show triggers; add the odds pair to the chip text. The add-box
  hint drops "lot".
- Desk tab verdict cards: size reads shares; a line shows the odds and gate the model saw.
- `web/notify.js`: trigger notifications include the odds pair.
- Header: the risk input is removed; a small "₹2,000 / trade" chip links to the Trades tab settings.

## Tests

- `tests/test_timemachine.py`, `tests/test_sizing.py`, `tests/test_gate.py` (pure functions, pandas
  fixtures; the gate test walks the 28 Oct sequence from the mockup and asserts the same PASS/BLOCK
  pattern and ledger numbers).
- `tests/web/test_trades.mjs`: ledger tile formatting, row ordering, odds pair formatting, filter
  application.
- `scripts/backtest_odds.py --days 42`: for every stored pattern trigger and rule alert with levels,
  compute the odds as of the trigger and compare with the graded outcome; prints calibration by
  decile (predicted T1 % vs realised) and per verdict word. Run once before approval of the Telegram
  line; its note goes to `docs/superpowers/notes/`.

## Rollout

1. Migration 005, `core/timemachine.py`, `core/sizing.py`, `core/gate.py` with tests. Run
   `scripts/backtest_odds.py` on the 51 stored days; record calibration.
2. Trades loop + routes; odds on `/api/patterns` and `/api/alerts`; desk context and prompt;
   Telegram line. Deploy after 15:30 with `--no-deps api`.
3. Trades tab + lots → shares everywhere + odds column/bars; `git pull` ships it.
4. One week of live use. Then revisit: gate counts logged paper trades instead of PASSes? wins buy
   back day room? `N`, `sessions`, the 8-point coin-flip band.

## What changes in existing code

- `api/desk.py`: `size_for` → `core.sizing.shares`; `verdict_context` gains `odds`, `gate`;
  `VERDICT_SYSTEM` two sentences; `review_context`/`pnl_for` use shares.
- `api/live_desk.py`: `process_minute` reads the trade row for the ref (odds/gate) instead of
  computing size from lots; `lots_map` stays for the OI analytics only.
- `api/live_patterns.py::message` and the engine's rule-alert Telegram text (`engine/main.py`) gain
  the odds line. The engine does not compute odds; it reads `trades:{day}:{ref}` from Redis if the
  trades loop has written it within 20 s, else sends without the line (the engine fires first; the
  API loop follows within the minute; v1 accepts that the rule-alert Telegram may lack the line, and
  the dashboard row always has it).
- `web/index.html`: tab button, `#trades` view, lots → shares in three places, odds column and bars,
  header risk input removed, `?v=` bumps.
- `db/init.sql` mirrors migration 005.

## Facts from the code the builder should not rediscover

- There is no shared minute sequence today: the pattern tick runs in a process pool at about :05 s
  and the desk tick in a thread at about :20 s with independent sleeps. The trades tick is chained
  into the desk loop's coroutine before `process_minute`, so the desk always sees the row.
- `core.backtest.outcome` grades to the last candle; the ledger slices at `flat_by` first.
- The engine inserts alerts without `RETURNING id`, so it cannot form the `alert:ID` key for the
  Telegram line; the engine's insert gains `RETURNING id` and the Redis lookup `trades:{day}:{ref}`
  with a 20 s wait. Live alert `ts` is wall-clock insert time with seconds; the row's `ts` is the
  bar minute (`date_trunc('minute')`), the ref keeps the id.
- Live days hold both `source='live'` and `source='replay'` pattern rows under the same `ref`, and
  live and replay alerts for one fire under different ids; `merge_live_replay` resolves the latter.

## Left to the builder

- Exact numpy layout of the cached session bars and the vectorised first-touch scan.
- Odds colour thresholds in CSS; whether the mini-bar shows `none` as grey remainder (mockup) or a
  third segment.
- How the Patterns table column truncates on narrow screens.
- Whether the "same plan on past sessions" squares are 12 or the width allows more.

## Relation to the calls-loop design (28 Oct, pending)

Independent. If the calls loop is built later, its call rows reference the same `ref`, and `odds`
and `gate` go into the call prompt exactly as they go into the verdict prompt here.
