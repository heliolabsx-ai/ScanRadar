# Patterns page: double and triple bottoms and tops — design

Date: 18 Oct 2026. Owner: Saurabh Verma. Status: builder-checked 18 Oct; decisions below marked *(decided after review)*.
Approved target: the demo at `scratchpad/patterns-demo/index.html` shown on 18 Oct (simulated data), reproduced below where it matters.

## Intent

Saurabh's edge is catching reversals. The ranking scanner rewards momentum, so a
stock that is down and turning stays in the bear list until it is already green.
This page detects the cleanest reversal shapes, double and triple bottoms and
tops, on 5-minute bars for every stock in the universe, shows them in three
states (forming, triggered, failed), gives the trade levels and a confirmation
checklist, alerts BUY/SELL on the trigger bar, and grades every trigger so the
page carries a hit rate per pattern from day one.

Out of scope now (Saurabh's call, 18 Oct): breakouts, Claude-in-the-loop
analysis. Timeframes other than 5m are a later option; the detector takes a
timeframe parameter but only 5m is wired.

## Goals

1. `core/patterns.py`: a pure, tested detector that, given one symbol-day of
   1-minute candles, returns every double/triple bottom/top on 5m bars with its
   full lifecycle for the day, and an `as_of(at)` view of state at any minute,
   using the same as-of semantic as everything else (`core/asof.view_at`).
2. Backtest first: a CLI that runs the detector over the stored days and prints
   per-pattern triggers, hit rate, average returns, and median time to target.
   The numbers decide the default tolerances before the page ships.
3. API: patterns for a day as of a minute, prepared in the existing background
   job and cached like replay alerts; per-pattern stats; live rows for today.
4. Dashboard: a **Patterns** tab beside the Scanner, same header, date and
   slider, with the table, quick filters, detail chart with the pattern drawn,
   and the checklist, as in the demo. Triggers raise the same Mac notification.
5. Telegram BUY/SELL on triggers during live hours, without touching the engine
   before Phase 2: a small live loop in the API process (see Architecture).

## Non-goals

- Breakout/breakdown patterns. Head and shoulders, flags, wedges.
- Changing the ranking scanner or the rule-scan engine.
- Order placement.
- A separate "reversal score" column in the Scanner (the forming rows on this
  page are the watchlist; revisit after the hit rates are in).

## Anti-goals

- A pattern that looks different in replay from what live showed at the same
  minute. One detector, one as-of definition.
- Repainting: a row's levels, state or score at minute M must never change when
  recomputed later with more data. Swing points are confirmed only after the
  confirming bars exist; a pattern enters *forming* only once its second (or
  third) low/high is confirmed.
- Telegram noise: at most one trigger alert per symbol per pattern kind per day;
  no alerts for forming or failed rows.
- Slowing the live dashboard: detection for 213 symbols × one day must run well
  under two seconds on the VPS.

## Definitions (5m bars built from 1-minute data, 09:15 anchor, as-of semantic)

**Swing low / high.** A 5m bar whose low is strictly below the lows of the `K=2`
bars on each side (high strictly above for a swing high). It is *confirmed* at
the close of the second bar after it; before that it does not exist.

**Double bottom.** Two confirmed swing lows `L1` then `L2` with
`|L2 − L1| / L1 ≤ TOL` (default 0.4%), separated by at least `MIN_SEP=4` bars
(20 min) and at most `MAX_SEP=36` bars (3 h), with at least one confirmed swing
high between them. **Neckline** = the highest high between `L1` and `L2`.
Between the bottoms the price must have retraced at least `MIN_RETRACE=0.35%`
from `L1` to the neckline (filters flat noise). A later low within `TOL` of
both extends it to a **triple bottom** (kind changes, neckline = highest high
across the whole span, stop = lowest of the three). A bottom whose `L2` is
more than `TOL` *below* `L1` is not a double bottom (it is a lower low), and a
pattern is **abandoned** (never shown) if price closes more than `TOL` below
the lowest low before triggering.

**Double / triple top.** The mirror image with swing highs and the neckline as
the lowest low between them.

**Pairing rule** *(decided after review)*. When a swing low is confirmed it is
paired with the **most recent earlier** qualifying swing low only, so each
confirming bar creates at most one pattern per kind, which is what the storage
key assumes. If a double bottom already exists on `(L1, L2)` and a third low
within `TOL` of both confirms, a **new triple-bottom row** is created with
`formed_at` at the third low's confirmation and the double row gets
`superseded_at` set to that minute; rows are never mutated otherwise, so a
stored row reads the same at every earlier minute (anti-goal 1). A separate
double on `(L2, L3)` is not created. If the middle low broke the structure
(closed more than `TOL` below the first), the first pattern was abandoned at
that bar and the next pattern can only start from the middle low.

**States.**
- *forming*: the shape exists and is confirmed; no trigger yet. Starts at the
  confirming bar of `L2` (`formed_at`).
- *triggered*: the first 5m bar after `formed_at` that **closes** above the
  neckline (bottoms) or below it (tops). `triggered_at` is that bar's close
  time (the minute the live engine would have seen the bar complete).
  **Entry** = that bar's close. **Stop** = lowest low of the pattern (highest
  high for tops). **Target** = neckline ± (neckline − stop), the measured move.
  R:R is computed from entry.
- *failed*: within `FAIL_BARS=3` bars after the trigger, a 5m close back
  through the neckline before the target was touched.
- *done*: target or stop touched (graded by `core.backtest.outcome` with the
  entry, stop and target above, side long for bottoms, short for tops), or the
  day ended. `done` is shown as the outcome on the row, not as a state chip.

A pattern that never triggers stays *forming* until the day ends; if the price
abandons it (see above) it is dropped from the day's list from that minute on.

**Score (0–5)**, computed at the moment the row is shown (forming) and frozen
at the trigger: one point each for
1. **Extended**: day change at `L2` at least −1% for bottoms (+1% for tops).
2. **OI agrees**: futures OI at `L2` lower than at `L1` for bottoms (shorts
   covering), and **also lower** at `H2` than at `H1` for tops (longs
   unwinding: positions closing into the failed high). *(decided after review:
   the earlier text said "higher", which contradicted its own label.)* Needs OI
   in the bars; 0 if OI is missing or zero.
3. **Volume**: the 5m volume of the trigger bar (or, while forming, of the `L2`
   bar) at least 1.5× the day's average 5m volume so far.
4. **Sector agrees**: the sector's average % change at that minute is ≥ 0 for
   bottoms, ≤ 0 for tops (from the ranking snapshot's `sectors`).
5. **VWAP**: the trigger bar closes above VWAP for bottoms (below for tops);
   while forming, the last close is used.

The checklist shown in the detail panel is exactly these five plus "confirming
close", in that order, each with a tick or a dot.

## Architecture

### core/patterns.py (pure, tested)

```python
Params = dict  # tol, min_sep, max_sep, min_retrace, k, fail_bars (defaults above)
def detect_day(df1m, params=DEFAULTS, tf="5m") -> list[Pattern]
    # Pattern: {kind, side, points: [{ts, price}], neckline, formed_at, triggered_at|None,
    #           entry|None, stop, target, failed_at|None, abandoned_at|None, checks_at_trigger|None}
def as_of(patterns, at, ctx) -> list[Row]
    # Row = Pattern + state (forming|triggered|failed), score, checks, age_min,
    # computed with only information available at `at`; ctx supplies sector % and VWAP
def swings(bars5m, k=2) -> (lows, highs)   # confirmed swing indices
```

`detect_day` walks the day once (the same partial-window logic as
`core/backtest.fires`, so the forming 5m bar is the stub plus completed minutes)
and records events with the minute they became knowable. `as_of` is a filter
over those events, which is what makes repainting impossible by construction:
nothing in a Pattern is derived from bars after its own event minute.

Grading: `core.backtest.outcome(df1m, triggered_at, side, entry, stop, target)`
plus `time_to_target_min` (new small helper in `core/backtest.py`).

### Backtest CLI: `scripts/backtest_patterns.py`

Runs over `candles_1m` for the last N stored days (default all), **persists the
detected patterns with their outcomes into the `patterns` table**
(`source='replay'`, detector hash, idempotent on the unique key; this is how the
42 stored days are populated so the hit rates exist on launch *(decided after
review)*), and prints a table per kind: triggers, hit rate (target before stop), failed-within-3-bars rate,
avg r30 / r60, median minutes to target, and the same split by score ≥ 3 vs
< 3. Also writes `docs/superpowers/notes/patterns-backtest-<date>.md`. Run via
`docker compose run --rm api python scripts/backtest_patterns.py` (the api image
has core/ and pandas; mount `scripts/`). The defaults in the spec may be tuned
once, by hand, from this output before the page ships; the tuned values go into
`core/patterns.py` DEFAULTS and are recorded in the note.

### Storage

```sql
CREATE TABLE IF NOT EXISTS patterns (
  id BIGSERIAL PRIMARY KEY,
  day DATE NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL, tf TEXT NOT NULL DEFAULT '5m',
  side TEXT NOT NULL,                      -- long | short
  formed_at TIMESTAMPTZ NOT NULL, triggered_at TIMESTAMPTZ, failed_at TIMESTAMPTZ, abandoned_at TIMESTAMPTZ,
  neckline DOUBLE PRECISION, entry DOUBLE PRECISION, stop DOUBLE PRECISION, target DOUBLE PRECISION,
  points JSONB NOT NULL, checks JSONB, score INT, outcome JSONB,
  superseded_at TIMESTAMPTZ,               -- double extended into a triple at this minute
  source TEXT NOT NULL DEFAULT 'replay',   -- replay | live
  detector TEXT NOT NULL,                  -- version+params hash, like scan_hash
  UNIQUE (day, symbol, kind, formed_at)
);
CREATE INDEX IF NOT EXISTS patterns_day ON patterns (day);
```

Written for a settled day by `_prepare_day` (recomputed when `detector` differs,
same rule as replay alerts). Live rows (today) are written by the live loop
below with `source='live'`; the settled-day recompute keeps live rows and only
adds missing ones (`ON CONFLICT DO NOTHING` on the unique key).

### API

- `GET /api/patterns?day=YYYY-MM-DD&at=HH:MM` →
  `{"day", "at", "pending": bool, "rows": [Row...], "counts": {"forming","triggered","failed"}}`.
  For settled days: from the `patterns` table, state derived at `at` (a row is
  included if `formed_at ≤ at` and not abandoned by `at`; state by comparing
  `triggered_at`/`failed_at` with `at`), score/checks as stored at trigger or
  recomputed for forming rows. For today during the session: computed on
  demand from the day's candles with `detect_day` + `as_of`, cached 60 s in
  Redis (`patterns:today:{HH:MM}`), never persisted by this route.
- `GET /api/pattern-stats?days=20` → per kind `{triggers, hit_rate, fail_rate,
  avg_r30, avg_r60, median_tt_min, by_score: {">=3": {...}, "<3": {...}}}` from
  the `patterns` table (source precedence like alerts: live rows win).
- `GET /api/alerts?day=` is unchanged. Pattern triggers are **not** inserted into
  `alerts` and do **not** appear in the Scanner tab's alert rail *(decided after
  review: the Scanner tab stays exactly as it is)*; they live in `patterns` and
  on the Patterns tab, and their Mac notifications fire from the Patterns feed.

### Live loop (API process, until Phase 2 moves it into the engine)

`api/live_patterns.py`: an asyncio task started on app startup when
`PATTERNS_LIVE=1` (default on). Every 60 s between 09:20 and 15:20 IST on a
trading day (`ingest/tradingday.py` logic duplicated in `core/calendar.py`,
single source), it runs `detect_day` for today in **its own single-worker
process pool** (`_live_pool`, separate from `_prep_pool`, so a viewer opening
an unprepared past day cannot delay a live trigger; about 100 MB more RSS on
the 8 GB box *(decided after review)*), upserts rows into `patterns` with `source='live'`, and for each
row whose `triggered_at` is new since the last run: sends Telegram
`<b>{kind} · {BUY|SELL}</b>\n{symbol} entry ₹e · stop ₹s · target ₹t · R:R 1:x · score n/5\n{HH:MM} · scanradar.tech`
(using `core/notify.py`, a copy of `engine/notify.py`; the engine keeps its own
until Phase 2) and publishes `patterns:updated` on Redis so the page refreshes.
Cooldown: one Telegram per (symbol, kind) per day. Telegram unset → log only
(today's behaviour). Phase 2 note: when the engine moves onto `core/`, this
loop moves with the rule scans and the API route stays read-only.

### Dashboard

Header gains a two-tab switch **Scanner | Patterns** left of the date selector;
the date, slider, status chip, Live button, risk input, theme and notify
controls are shared. `#patterns` view replaces the grid when active (URL hash
`#patterns` so it survives reload). Layout as the demo:

- Left/main panel: eyebrow `Pattern setups · 5m · as of HH:MM` and the counts;
  stats lines per kind from `/api/pattern-stats`; quick-filter pills
  `All · Bottoms (BUY) · Tops (SELL) · Triggered only · Needs OI confirmation
  (score includes OI) · Min R:R 1.5`; the table with columns
  `Stock · Pattern · State · Side · Trigger/Entry · Stop · Target · R:R · Score · Age`.
  The Scanner's view filter (sector, price) also applies here.
- Right panel: eyebrow `{kind} · {sector} · day ±x%` and the state chip; symbol
  and the BUY/SELL chip reading `BUY NOW` (triggered this minute),
  `BUY if 174.90 closes` (forming), `(failed)`; the kv block `Entry · Stop
  (with %) · Target · R:R · Qty @ risk · Pattern hit rate · 20d`; the chart
  (reuse `drawChart` with a `pattern` overlay: the lows/highs circled and
  labelled `1st low 10:20`, the neckline dashed from `L1` to the right edge,
  the trigger bar boxed with `TRIGGER HH:MM` or `TRIGGER → FAILED`, the stop
  and target zones shaded from the trigger bar); the "Why this row" checklist.
- Rows are clickable; the slider moves the as-of time and rows change state
  accordingly (a triggered row seen at an earlier minute shows as forming).
- Live mode: `patterns:updated` over the existing `/ws/scan` channel (the api
  forwards it as a `{"type":"patterns"}` message) → refetch; new triggers raise
  the Mac notification via `ScanNotify` with title `ScanRadar · {kind}`.
- Empty states: "no patterns formed by HH:MM" and the pending note while the
  background job runs.

## Constraints

- 2 vCPU VPS: `detect_day` for 213 symbols must take < 2 s; the live loop runs
  in its own process pool; the settled-day computation stays in `_prepare_day`.
- Backfilled days have no volume delta; the volume check uses total volume.
- OI before the front-month roll is thin (existing caveat); the OI check is
  simply 0 when OI is missing or zero.
- Equity bars end 15:14; a pattern that triggers after 14:45 has little room:
  show `late` next to the age when `triggered_at ≥ 14:45`, no other change.

## Acceptance

1. Unit tests: a synthetic double bottom is detected with the right neckline,
   stop and target; a lower-low is not; triple extends; tops mirror; state
   transitions forming → triggered → failed/done at the exact bars; `as_of`
   never shows information from after `at` (property test over random days:
   `as_of(detect_day(df), at) == as_of(detect_day(df[df.ts ≤ at + stub]), at)`
   for every minute); 213-symbol-day detection < 2 s.
2. Backtest note written for the 42 stored days with per-kind hit rates, and
   DEFAULTS in `core/patterns.py` match the note.
3. `/api/patterns` for 2026-10-16 at 15:14 returns rows in all three states;
   dragging the slider back shows triggered rows as forming before their trigger.
4. The page renders the demo layout with real rows; clicking a row draws the
   pattern on the chart with circles, neckline, trigger box and zones.
5. Live loop: on a trading day, a trigger produces a Telegram message (or a
   log line when Telegram is unset) within 90 s of the bar close, exactly once.
6. The Scanner tab is unchanged in behaviour and the live path (ingest, engine)
   is untouched.

## Left to the builder

- Exact swing-detection implementation (array-based is fine) and how
  `detect_day` reuses the window-partial machinery from `core/backtest.fires`.
- Whether `as_of` recomputes forming-row scores per minute or stores a per-minute
  score series.
- Table virtualisation if a day has more than ~200 rows (unlikely).
- Internal structure of the Patterns view inside `web/index.html` versus a
  `web/patterns.js` file (either is fine; no build step).
