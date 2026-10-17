# History, replay and the real dashboard — design

Date: 17 Oct 2026. Owner: Saurabh Verma. Status: builder-checked; awaiting Saurabh's approval.

## Intent

ScanRadar went live on Saturday 17 Oct 2026 with an empty database and a
dashboard that shows nothing while the market is closed. Saurabh wants the
dashboard to be useful at any time: pick a past session and see exactly what the
scanner would have shown, scrub through that day minute by minute, and look at
any stock on timeframes from 1 minute to 1 day. This is also the foundation for
two roadmap items: replay/backtest of rule scans (item 4) and, later,
self-grading (item 3).

Decision made in conversation: **the timeframe selector is a chart-only
control.** The ranking scanner keeps its fixed basis (1-minute data, 30-minute
OI trend) and each rule scan keeps the timeframe declared in its JSON. A ranking
that changes when the chart zoom changes is a ranking a trader cannot act on.
Multi-timeframe confirmation belongs on the card later (roadmap item 5), not on
a global knob.

## Goals

1. Backfill ~60 trading days of 1-minute candles (equity OHLCV, futures OI
   merged in) and ~1 year of daily candles for the whole F&O universe, and keep
   them topped up so a day missed by live ingest can be filled later.
2. Replay: compute the ranking snapshot for any stored day *as of* any minute,
   and the rule alerts that would have fired that day, using the same analytics
   code the live engine uses.
3. Dashboard: date selector, time slider, live/replay indication, chart
   timeframes 1m/3m/5m/15m/30m/60m/1D, the rule-alert list for the selected
   day, and the look of the reference artifact (light and dark themes).
4. Everything idempotent and safe to run on the production VPS (2 vCPU, 8 GB,
   78 GB free) without touching the live path during market hours.

## Non-goals

- Scan builder UI (to-do item 2, second half). Scans stay as JSON files.
- Self-grading / hit-rate display (roadmap item 3). The data model must not
  block it, but no UI or outcome computation is built here.
- Re-ranking or re-evaluating scans on a user-chosen timeframe.
- Tick-level volume delta for backfilled days. The historical API has no ticks;
  backfilled candles carry `delta = 0` and the UI shows "—" for delta on those
  days instead of a misleading zero.
- Per-day correct futures contract for old days (see Constraints).

## Anti-goals (what would make this a failure even if it works)

- The replay disagreeing with what the live engine showed on the same day at the
  same minute. There must be one analytics code path.
- The backfill overwriting live-captured candles, which carry real volume
  delta. Backfill only fills gaps.
- A dashboard that is slower or less clear on a live Monday morning than the
  current minimal page. Live mode remains the default during market hours and
  the slider must not get in the way.
- Blowing the Kite historical rate limit (3 req/s) or the account's login
  budget. Backfill reuses the cached access token and never logs in by itself
  outside the existing `kite_auth.session()` path.

## Constraints and facts the builder should know

- Kite historical API: `minute` interval returns at most 60 days per request,
  `day` up to 2000 days; `oi=1` works for futures on both intervals. Rate limit
  3 req/s; the existing `_hist()` helper in `ingest/main.py` paces and retries.
- The instrument dump only lists *current* contracts, so futures OI history can
  only be fetched for the contracts that exist today. Early in the 60-day window
  the Oct-2026 contract was not the front month and its OI was thin. Accept
  this: OI % and OI labels on days before the contract became front month are
  computed against that same contract's previous day, which is internally
  consistent but less meaningful. Document it in the UI as a footnote on days
  older than the previous expiry; do not try to reconstruct expired contracts.
- Candle timestamps are minute **start**, IST, stored as `timestamptz`.
  Resampling anchors at 09:15 (existing `rules.resample`).
- Trading-day calendar: derive from the daily candles of a reference symbol
  (RELIANCE) rather than a holiday list. A date exists in the date picker only if
  it has daily candles.
- `candles_1m` primary key is `(symbol, ts)` with `ON CONFLICT DO NOTHING`
  semantics already used by live ingest. Backfill uses the same statement, which
  is what guarantees "fill gaps only".
- VPS: 2 vCPU. Anything that walks 60 days × 213 symbols × 375 minutes through
  pandas per minute is too slow; see the replay strategy below.
- Saurabh pastes command blocks verbatim; any operator command in docs must be
  runnable as-is with no placeholders.

## Architecture changes

### 1. Shared analytics package `core/`

Move `engine/analytics.py` and `engine/rules.py` into a top-level `core/`
package (plain modules, no new dependencies) and add:

- `core/ranking.py` — `rank(frames: dict[str, DataFrame], refs: dict[str, dict], ts) -> snapshot`
  extracted from the sector-average / score / rank / picked logic currently
  inline in `engine/main.py:run_once()`. The live engine and the replay API both
  call this. The snapshot shape is unchanged (`ts, sectors, n_bull, n_bear,
  bulls[:40], bears[:40]`) plus a new `mode: "live" | "replay"` and `delta_available: bool`.
- `core/backtest.py` — `fires(scan, df1m, ctx, cooldown_min) -> list[Timestamp]`:
  evaluates a rule **at every minute of the day with the same view the live
  engine had at that minute** (see "As-of semantics" below), honouring cooldown.
  Implementation note for the builder: do not call `resample()` 375 times per
  symbol. Precompute, per 1-minute index, the partial aggregates of the
  resampled window it falls in (first open, running high/low, running volume,
  last close/oi); then the bar list "as seen at minute m" is the completed
  windows before m plus that partial row, and each evaluation is array lookups.
  This is the seed of roadmap item 4.

### As-of semantics (one definition, used by live, replay and backtest)

"As of HH:MM" means the view the live engine produces when the HH:MM−1 bar
closes and the engine runs at about HH:MM:01:

- complete 1-minute bars: every bar with `ts < HH:MM`;
- forming bar (`off 0`): the bar with `ts == HH:MM`, which live sees as a
  stub holding roughly one second of ticks. Replay reproduces it as a stub built
  from the stored HH:MM bar: `open = high = low = close = that bar's open`,
  `volume = 0`, `delta = 0`, `oi = previous bar's oi`. Replay never lets the
  completed HH:MM bar leak into the `off 0` slot.
- rule scans on a resampled timeframe see the forming resampled bar built from
  the complete 1-minute bars inside the current window plus that stub, exactly
  as `rules.resample` builds it live.

The live engine's behaviour is the canonical semantic and is not changed by this
project. Replay and live may still differ by whatever happened in the first
second of a minute; that is accepted and is why the de-duplication of alerts
below is tolerant.

Both the `engine` and `api` images need `core/`. Change their compose `build:`
to `{context: ., dockerfile: engine/Dockerfile}` / `{context: ., dockerfile: api/Dockerfile}`
and `COPY core/ ./core/`. The api image gains `pandas` and `numpy` (already
pinned versions in the engine Dockerfile). Keep `ingest/` self-contained as
today (broker adapter boundary).

### 2. Schema additions (`db/init.sql`, plus a migration file applied on deploy)

```sql
CREATE TABLE IF NOT EXISTS candles_1d (
  day     DATE NOT NULL, symbol TEXT NOT NULL,
  open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,
  volume BIGINT, oi BIGINT,
  PRIMARY KEY (symbol, day)
);
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'live';  -- 'live' | 'replay'
CREATE INDEX IF NOT EXISTS alerts_day ON alerts ((ts AT TIME ZONE 'Asia/Kolkata')::date);
```

`daily_ref` already exists keyed `(day, symbol)`; backfill fills it for every
historical day from `candles_1d` (prev close/high/low from the previous trading
day's equity bar, prev OI from the previous day's futures bar, avg 1-minute
volume = mean of the prior 20 daily volumes / 375). Live ingest keeps writing
today's row at 08:55 exactly as now.

Because `init.sql` only runs on an empty volume, ship the additions also as
`db/migrations/001_history.sql` and apply with
`docker compose exec -T postgres psql -U scanner -d scanner -f /migrations/001_history.sql`
(mount `./db/migrations` read-only into the postgres service). Builder's call
whether to add a tiny `migrate.sh`; there is no migration framework and none is
wanted.

### 3. Backfill (`ingest/backfill.py`)

Run as `docker compose run --rm ingest python backfill.py` (defaults: 60
**calendar** days of minute data, about 41 trading days and one minute-request
per instrument; 400 calendar days of daily data). Deeper minute history is not
worth it: the Oct-2026 futures were only listed in late July, so older days
would have no OI at all, and live capture adds a day every session. Steps:

1. `kite_auth.session(R)` for a client (reuses the cached token; never a fresh
   login while a live socket could be using the token — same rule as preflight).
2. Universe from the `instruments` table if populated today, else
   `universe.build()`.
3. For each symbol, in universe order, paced by `_hist()` (move `_hist` into a
   small `ingest/hist.py` so both `main.py` and `backfill.py` share it):
   - equity `minute` candles for the window (one request if ≤ 60 days, else
     chunked), futures `minute` with `oi=1`;
   - merge futures OI onto equity minutes by timestamp (forward-fill within a
     day; 0 where the contract has no bar);
   - insert into `candles_1m` with `delta = 0`, `ON CONFLICT DO NOTHING`;
   - equity `day` candles and futures `day` with `oi=1` → `candles_1d`.
     Equity OHLCV may be upserted (`ON CONFLICT DO UPDATE` on those columns),
     but **futures OI is written once**: `oi = COALESCE(NULLIF(candles_1d.oi, 0), EXCLUDED.oi)`.
     After the 27 Oct roll a rerun fetches the Nov contract's history; without
     this rule it would overwrite every past day's OI and silently change
     replay labels for September. The 1-minute path is already write-once.
     `daily_ref` is rebuilt from the preserved values, so it stays stable too;
   - log one line per symbol: rows inserted, rows skipped (already present).
4. Rebuild `daily_ref` for every day in the window from `candles_1d`.
5. Print a summary: trading days covered, symbols done, failures to retry.

Resumable: because inserts skip existing rows, rerunning after a failure just
continues. A `--symbols RELIANCE,TCS` flag and `--days N` flag are enough
control. Expected wall time ≈ 213 symbols × 4 requests × 0.4 s ≈ 6 min.

Top-up: the same script with `--days 3` run any evening fills whatever live
ingest missed. The builder may schedule it from `ingest/main.py` at 16:00 on
weekdays; if so it must skip silently when the token is stale rather than log
in.

### 4. Replay in the API

New endpoints in `api/main.py` (all read-only, all IST):

- `GET /api/days` → `{"days": ["2026-10-16", ...], "latest": "2026-10-16", "live": bool}`
  from `candles_1d` for RELIANCE, newest first, limited to days that also have
  at least one `candles_1m` row. `live` is true when `ingest:ws == connected`.
- `GET /api/replay?day=YYYY-MM-DD&at=HH:MM` → the ranking snapshot as of that
  minute: load `candles_1m` for the day with `ts <= at` (the bar starting at
  `at` is the "forming" one, matching the live engine's convention), load
  `daily_ref` for that day, call `core.ranking.rank()`. Cache the JSON in Redis
  under `replay:{day}:{HH:MM}` with a 7-day TTL. Cold cost should be around one
  second on the VPS; the builder should measure and, if it is over two seconds,
  pre-warm the 15:30 frame of each day in the backfill step.
- `GET /api/alerts?day=YYYY-MM-DD` → alerts for that day, union of stored live
  alerts and replay alerts. Replay alerts are cached in the `alerts` table per
  `(day, scan)` with the scan file's content hash in `payload.scan_hash`: if no
  rows exist for that day and scan, or the stored hash differs from the current
  file's, delete that day's replay rows for the scan, recompute with
  `core.backtest.fires()`, insert with `source='replay'`. Editing or adding a
  scan therefore refreshes history on next view, which is what roadmap item 4
  needs. Replay alerts are **never computed for the current day while it is in
  progress**: only for days before today, or for today after 15:35 IST. Live
  alerts win over replay alerts for the same scan and symbol within ±1 minute.
  Existing `?limit=` behaviour without `day` is unchanged. Each alert carries
  `price`, `tf` and `source`.
- `GET /api/candles/{symbol}?day=` unchanged (1-minute, one day). Add
  `GET /api/candles/{symbol}?tf=1d&days=250` returning daily bars from
  `candles_1d`. Intraday resampling (3m…60m) is done in the browser from the
  1-minute response; the API does not resample.
- `GET /api/scan` and `/ws/scan` unchanged: live only.

Mark every replay snapshot `mode: "replay"` and set `delta_available` false when
all `delta` values for the day are zero, so the UI can grey out Vol Δ.

### 5. Dashboard (`web/index.html`, still a single static file, vanilla JS)

Bring the page up to the reference artifact ("Intraday Flow Scanner",
https://claude.ai/artifact/374M9Chnq9UXNQ3ChZDT6C), whose CSS and layout should
be adopted nearly verbatim: IBM Plex Sans + JetBrains Mono, the four-column grid
(breadth/sectors rail, bulls, bears, detail panel), card anatomy (symbol, PICK
chip, % change, OI label chip, OI %, VWAP side, RVOL, rank/percentile),
conviction dots, the k/v table in the detail panel, canvas chart with OB zone,
opening range band, pivots, previous close, VWAP, last-price tag. Light and dark
via `prefers-color-scheme` with an explicit toggle persisted in `localStorage`
(this delivers the "light theme" to-do).

Header controls, left to right: title · **date selector** (a `<select>` of
`/api/days`, default = today if live else latest) · **clock** showing the
"as of" time · **status chip**: `LIVE` (green, pulsing dot) or `REPLAY` (muted)
· **Live button** (shown only when not following live; returns to live and
pins the slider) · theme toggle.

Below the header, full width: the **time slider**, `min=09:15 max=15:30 step=1 min`
rendered as a range input with tick labels every hour. In live mode it follows
the latest snapshot; the moment the user drags it, the page enters replay of
the current day as-of that minute (`/api/replay?day=today&at=`), and the Live
button appears. In replay of a past day it defaults to 15:30. Keyboard: ←/→
step one minute, Shift+←/→ five minutes. Debounce requests to one in flight;
show the pending time in the clock immediately and swap data when it arrives.

Detail panel additions: a **timeframe row** `1m 3m 5m 15m 30m 60m 1D`
(buttons, persisted in `localStorage`). Intraday frames resample the 1-minute
day in the browser anchored at 09:15 and show candles up to the as-of minute,
with VWAP, pivots, OR band and OB drawn exactly as on 1m (all computed from
1-minute data of the same day, never from the resampled bars). `1D` shows up to
250 daily bars from `/api/candles/{sym}?tf=1d`, marks the selected day with a
vertical line, and draws no intraday overlays. Vol Δ reads "—" when
`delta_available` is false.

Left rail: breadth and sector bars as today, then a **Rule alerts** list for
the selected day, newest first, each row `HH:MM · scan · SYMBOL · ₹price`,
clickable to select that symbol and move the slider to that minute. In live mode
new alerts prepend as they arrive. A small `replay` tag on alerts whose
`source` is replay.

Empty states: no days at all → a single sentence telling the operator to run
the backfill command (verbatim, copyable). Day selected but no candles for the
chosen symbol → "no data for SYMBOL on DATE".

Keep the page responsive down to phone width as the artifact already is.

### 6. Engine changes

`engine/main.py` shrinks: `run_once()` calls `core.ranking.rank()` and
`core.rules.evaluate()` from the shared package; behaviour is unchanged. Its
alert inserts set `source='live'` (the column default). No replay work runs in
the engine.

## Phasing (pending Saurabh's confirmation)

Monday 19 Oct 2026 is the first-ever live session, and roadmap item 1 ("fix
whatever breaks in ingest") has not happened yet. To keep a Monday failure
unambiguous, ship in two phases:

- **Phase 1, before Monday, additive only:** schema migration, `core/` package,
  `ingest/backfill.py`, the new API endpoints (the api image imports `core/`),
  the new `web/index.html`. The engine is **not touched**: it keeps its own
  `analytics.py`/`rules.py`, which are byte-identical to `core/` at this point.
  Nothing in the live path (ingest → engine → Redis → `/ws/scan`) changes.
- **Phase 2, after one clean live day:** switch `engine/main.py` to import from
  `core/`, delete the engine's copies, add `source='live'` explicitly. A single
  small commit.

A few days with two identical copies of the analytics is accepted against the
"one code path" anti-goal because the alternative risks the first live day.
Any change to the analytics during those days must be made in both places.

## Operational notes for the builder

- Deploy order on the VPS: `git pull` → apply migration → `docker compose up -d --build`
  → run the backfill once (outside market hours; it only reads from Kite and
  writes gap rows) → open the dashboard and pick a date.
- The backfill and the replay endpoints must never call `kite_auth.login()`.
  If no valid cached token exists, the backfill exits with the one-line
  instruction to run it after 08:55 on a trading day or to run the preflight.
- Memory: load one symbol-day at a time in the replay endpoint; never the whole
  universe for the whole window.
- README gets a "History and replay" section with the three operator commands
  and the limitations (no delta on backfilled days, OI caveat before the last
  expiry).

## Acceptance

1. After backfill, `/api/days` lists ≥ 40 trading days and the date picker
   shows them.
2. For a past day, the 15:30 replay snapshot renders bull/bear cards with
   sensible OI labels for liquid names, and dragging the slider to 10:00
   re-renders in under two seconds warm, under three cold.
3. On Monday 19 Oct during market hours the live view behaves exactly as the
   current page does, with the slider pinned to the latest minute; dragging it
   back shows the day as-of that minute; the Live button returns.
4. For a past day, the alert list shows replay alerts for both existing scans
   where their conditions held, and clicking one moves the chart to that minute
   with the candle visible.
5. Timeframe buttons change the chart without changing the cards; `1D` shows
   daily bars with the selected day marked.
6. Light and dark themes both match the artifact's palette.
7. Re-running the backfill reports zero inserted and zero OI-updated rows and
   finishes in under a minute of database work after the Kite fetches;
   live-captured rows keep their non-zero delta.
8. Replay at 10:00 for a liquid stock shows the same `off 0` convention as
   live: the 10:00 bar appears as a flat stub at its open, not as a completed
   candle.

## Left to the builder

- Exact migration mechanics and file naming.
- Where to put the replay cache warm-up (backfill vs. first request).
- Internal structure of `web/index.html` (modules vs. one script block), as long
  as it stays a single static file with no build step.
- Chart rendering details beyond what the artifact shows (hover readout,
  crosshair) are welcome but optional.
- Whether to schedule the evening top-up from `ingest/main.py` or leave it manual.
