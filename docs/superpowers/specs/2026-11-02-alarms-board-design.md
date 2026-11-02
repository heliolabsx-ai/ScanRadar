# Alarms board: one column per setup, the stock panel on click

Date: 2 Nov 2026 (brainstormed the same evening). Owner: Saurabh Verma. Status: approved in chat ("add all and commit the deployment").

## Intent

On the first evening of the PD fade alarms Saurabh could not tell what had fired, when, or whether anything
had reached his phone. The rail on the Scanner tab lists alerts, the Patterns tab lists triggers, the Desk
tab lists verdicts, the Trades tab lists rows: four places, none of them answering "what rang today, when,
and what came of it". He asked for a notifications page with the time of every trigger, rejected a flat log
(Monday produced about 1,300 events), chose the animated **setup board** (a header per setup, the stocks
stacking under it as they fire) and the **stock panel** that opens on click with the chart and the levels,
and then said yes to all seven additions I proposed. The mockups are
`.superpowers/brainstorm/38214-1793640111/content/notifications-board-v10.html` (board + panel, built from
the real 2 Nov rows) and `notifications-animated-v4.html` (the four earlier looks, for the record).

## What Saurabh decided

- A board, not a log: **one column per setup**, newest stock on top, the minute it fired on every row.
- Clicking a stock opens **the stock panel**: the chart up to the current minute with PDH, PDL, VWAP, R1,
  R2, S1, S2, previous close, the alarm's stop and target, the alarm bar marked; the price now, % change,
  VWAP side, OI vs yesterday, RVOL, the day range; a level ladder showing which two levels the price sits
  between; the alarm ticket (stop / entry / target, R:R bar, now-vs-entry in R, odds bar, shares, desk).
- "Make it more beautiful": the v10 panel look (header with the big price, four tiles, chart + ladder,
  ticket card) is the reference.
- The seven additions, all in: (1) the setup's record on the column header, (2) the last 12 replays as
  dots on every row, (3) the stock's story timeline in the panel with every event marked on the chart,
  (4) a breadth line at the top of the board, (5) "did it ring": the Telegram delivery mark per row,
  (6) the NIFTY 50 level in the board header, (7) a chime per alarm and an unread badge per column.
- Ship tonight (2–3 Nov 2026, after the close, before the 08:55 login).

## My calls (Saurabh to check while reading)

- The tab is called **Alarms** (`#alarms`, after Trades in the header). "Notifications" is what he said;
  "Alarms" is what the rows are, and the word already names the PD fades in this repo.
- **Which rows are alarms.** A scan is on the board only when its JSON carries `"board": true`; the four
  `PD*` scans and `B0101` get it tonight, the three older scans (879 alerts on 2 Nov) do not and are folded
  into one muted count line under the board ("879 alerts from 3 other scans"). Patterns: trade-grade
  triggers only (`PATTERN_MIN_CONF`, score ≥ 5, the Trade-grade pill's rule), the rest counted in the same
  line. Columns, in order: `PDH breakout` (scan "PDH break"), `PDL breakdown` ("PDL break"), `Patterns`,
  `PDH fade`, `PDL fade`, then one column per other board scan in file order (`B0101`). Column names for
  the PD scans are the mockup's; a scan may set `"board_name"` to override. Desk verdicts are not rows:
  the call sits on the row it judged.
- **One endpoint builds the board**: `GET /api/alarms?day&at` returns everything the tab needs in one
  call, cut at `at` (today without `at` = now, live). The tab never assembles rows from four feeds.
- **Delivery is recorded, not inferred.** A `deliveries` table (migration 006) gets one row per Telegram
  send attempt: `(day, ref, channel 'telegram', sent_at, ok, error, silent)`. `core/notify.telegram` and
  `engine/notify.telegram` return `(ok, error)` instead of nothing; every send site records (the trades
  loop's `flush_deferred`, the engine's direct sends, the desk ticket, the ingest watchdog is left alone).
  "Not configured" is a delivery row with `ok=false, error='not configured'`, so the board can say so per
  row. The board header shows `Telegram: on` / `not set up` from the API's environment.
- **Story timeline** is built server-side per symbol per day from rows that already exist: PD break
  alerts (`payload.story` gives the break minute and the run), fade / pattern triggers, desk verdicts
  (`desk_calls`), trade settlement (`trade_rows.outcome.settled_at`, `exit_px`), the failed/abandoned minute
  of a pattern. Each row carries the symbol's whole timeline; the panel draws it as a vertical chain and the
  chart marks each event (the chart already draws the day's alert markers for the symbol; pattern trigger,
  desk call and exit markers are added on the Alarms panel only).
- **Record on the header**: scans from `scan_stats` (the rail's numbers: fires, hit rate, avg R60) over
  20 sessions; the Patterns column from `pattern_stats` (T1 hit over the same window). Shown as
  `54 % · 1:1.2 · n 520`; "no record yet" under 20 decided rows.
- **Past-12 dots**: `trade_rows.odds.past.last` as it is (date, result, minutes); green target, red stop,
  grey neither; no row = no dots.
- **Breadth line**: `n_bull` / `n_bear` and the two strongest and two weakest sectors from the ranking
  snapshot at `at` (the replay cache, `core.ranking.rank` output), one line above the columns, refreshed
  with the minute.
- **NIFTY 50**: ingest subscribes instrument token **256265** (`NSE:NIFTY 50`) in QUOTE mode alongside
  the universe; index ticks carry `last_price` and `ohlc`, no volume. The ingest keeps a 1-minute candle
  for it and writes it to a **new table `index_1m`** (migration 006; `(symbol, ts)` key, OHLC only), never
  to `candles_1m`, because the engine and the replay load every symbol of `candles_1m` unscoped and the
  index must not enter the ranking. Live value in Redis `index:NIFTY50` = `{ltp, prev_close, pct, ts}`
  on every flush (and at most once a second between flushes). `daily_ref` is not touched: the previous
  close comes from the last `index_1m` bar of the previous session, else from Kite's daily historical for
  the token at 08:55 (`hist(kite, 256265, …, "day")`, stored in Redis `index:NIFTY50:prev_close`).
  `backfill.py --index` fills `index_1m` from Kite minute history (60 days default; the index token's
  minute candles exist). The board header shows `NIFTY 50 24,312 +0.4 %` live and the as-of value in replay.
  The watchdog's silent-feed rule is unchanged (it counts stock ticks).
- **Chime and badges**: `web/notify.js` gains `chime(kind)` with the Web Audio API (two short tones, no
  audio file), played when the bell is on for a new fade row, a new trade-grade pattern row and a new board
  scan row, never for a break note; the existing Mac notification stays. Each column shows an unread badge
  = rows that arrived while the Alarms tab was not the visible tab, cleared when it becomes visible
  (`localStorage` `alarms.seen` = the newest row `ts` per column at the last view). The header tab
  button carries the total.
- **Replay works the same way**: `day` and `at` from the header date and the time slider cut the rows,
  the breadth, the index and the panel's chart at that minute, as every other tab does.
- Nothing else changes: no new Telegram text, no change to scans, desk, odds, gate, sizing, ranking.

## Goals

- One screen that answers "what fired, when, what came of it, did it ring" for today and for any stored
  day, within a second of the minute.
- The stock panel gives the trade's whole picture (levels, position, story, ticket) without leaving the tab.
- Deployed tonight; live from 09:15 on Tue 3 Nov 2026.

## Non-goals

- No order placement, no change to what Telegram says, no change to the alarms themselves.
- No per-column Telegram routing (every board row still goes to the one chat).
- No mobile layout beyond what the existing CSS gives.

## Anti-goals

- A board that floods: the three old scans stay off it unless Saurabh marks them `"board": true`.
- A row with a time that is not the bar-close minute every other tab uses.
- A chart squashed by far-away pivots (far levels are named at the edge, as in the mockup).
- A chime for break notes.

## Constraints

- All times IST, bar-close minutes; `ts` of a candle is the minute start.
- Secrets only in `.env`; the API reads `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` only to say configured or not.
- `engine/rules.py`, `engine/notify.py` stay byte copies of `core/` (`cmp`).
- Web helpers are cache-busted: bump `?v=` in `web/index.html` for every helper .js touched
  (`notify.js`, the new `alarms.js`).
- Tests: pytest `.venv/bin/python -m pytest tests -q`; node tests run by file name; the local venv lacks
  fastapi/redis/psycopg/requests, so keep the assembly logic in pure functions (`api/alarms.py` with an
  `assemble(...)` that takes plain dicts; `ingest/candles.py` for the index candle).
- Deploy tonight: migration 006 via stdin (the postgres `/migrations` mount is stale), then
  `docker compose up -d --build api engine ingest` (ingest included on purpose: it is idle at night and must
  carry the index change before the 08:55 login), `backfill.py --index --days 60` with the cached token
  (valid until about 04:55), then `/api/alarms?day=2026-11-02` must show Monday's board with the deliveries
  column reading "not configured" and the NIFTY header filled from the backfill.

## The API

`GET /api/alarms?day=YYYY-MM-DD&at=HH:MM` →
```
{day, at, live, telegram: "on"|"not set up",
 index: {symbol:"NIFTY 50", ltp, prev_close, pct, at} | null,
 breadth: {bulls, bears, up: [[sector, pct]..2], down: [[sector, pct]..2]} | null,
 columns: [{key, name, sub, side, scan|null, record: {n, hit, avg_r, days} | null}],
 rows: [{ref, col, ts, symbol, side, setup, source: "alert"|"pattern", price, stop, target, rr,
         odds, past: [[date, result, minutes]..12], shares, risk_inr, gate, gate_reason,
         desk: {call, confidence, at} | null, outcome: {hit, r60, settled_at, exit_px} | null,
         story: {...} | null, delivery: {sent_at, ok, error, silent} | null,
         timeline: [{at, kind, text}]}],
 folded: {alerts: n, scans: n, patterns: n},
 pending: bool}
```
`pending` mirrors `/api/alerts` (a settled day being prepared). Rows are sorted by `ts` ascending; the tab
puts the newest on top. The panel's chart and levels come from the existing `/api/candles/{symbol}?day=`
and `wireChart` with `panelEl()` returning the Alarms panel; the tiles' numbers (VWAP, OI %, RVOL, range,
pivots) are computed in the browser from those candles and `ref`, exactly as the mockup does.

## Tests

- `tests/test_alarms.py`: `assemble()` on hand-built rows: column assignment (`board` flag, trade-grade
  rule, fold counts), the cut at `at`, the timeline order for a symbol with break → fade → desk → settle,
  delivery join, record formatting, the breadth and index values at `at`.
- `tests/test_deliveries.py`: `telegram()` returns `(False, 'not configured')` without env; `flush_deferred`
  and the engine's queue path record a delivery row (fake connection).
- `tests/test_index_candle.py`: `IndexState` builds OHLC from index ticks, flushes to `index_1m`, Redis
  value shape; the universe and the engine's symbol list never contain the index.
- `tests/web/test_alarms.mjs`: grouping into columns, unread badges across a view change, ladder placement
  (NOW between the right two levels, above R2, below S2), far-level selection for the chart, the position
  words, the timeline rendering order.
- `tests/web/test_notify.mjs`: `chime` chosen for fade/pattern/board-scan rows, never for breaks.

## Rollout

1. Migration 006 (`deliveries`, `index_1m`), `notify` returning `(ok, error)`, deliveries recorded.
2. `api/alarms.py` + route; `board` flag on the five scans.
3. Ingest: index subscription, candle, Redis, `backfill.py --index`.
4. `web/alarms.js` + the tab + the panel; `notify.js` chime and badges.
5. Deploy tonight as under Constraints; first live check at 09:20 Tue 3 Nov.

## Left to the builder

- Exact field names inside `story`/`timeline` text; the chime's tones; the ladder's exact CSS.
- Whether the API's `index` for a replay day reads the `index_1m` bar at `at` directly (it should).
- How the Alarms panel reuses `wireChart` (the Watch drawer's `panelEl()` route is the model).
