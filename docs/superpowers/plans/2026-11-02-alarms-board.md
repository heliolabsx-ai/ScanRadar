# Alarms Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An **Alarms** tab: one column per setup with the stocks stacking under it as they fire, a stock panel on click (chart with every level, tiles, level ladder, ticket, story timeline), the setup's record on each header, past-12 dots, a breadth line, the NIFTY 50 level, "did it ring" per row, unread badges per column.

**Architecture:** One new endpoint `GET /api/alarms?day&at` (`api/alarms.py`, pure `assemble()` over rows the other modules already produce: alerts, trade rows, desk calls, plus a new `deliveries` table) feeds one new helper `web/alarms.js` and the tab in `web/index.html`. Telegram sends return `(ok, error)` and are recorded in `deliveries`. Ingest subscribes the NIFTY 50 index token and keeps its 1-minute candle in a new `index_1m` table (never in `candles_1m`).

**Tech Stack:** Python 3.12, FastAPI, psycopg, pandas, Redis; vanilla JS + canvas; pytest, node:test.

**Spec:** `docs/superpowers/specs/2026-11-02-alarms-board-design.md`

## Global Constraints

- All times IST; a row's time is the bar-close minute every other tab uses; candle `ts` is the minute start.
- The scan files in `scans/` are **not touched** (their hash drives the replay rows). The board's scan list is `BOARD_SCANS` in `api/alarms.py`, default `PDH break, PDL break, PDH fade, PDL fade, B0101`, env `ALARMS_SCANS` overrides (comma-separated).
- `engine/notify.py` stays a byte copy of `core/notify.py`; `engine/rules.py` of `core/rules.py` (`cmp` both in every task that touches either).
- Secrets only in `.env`; the API reads `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` only to report configured or not; never log tokens.
- The index never enters `candles_1m`, `daily_ref`, the `universe` set or the `instruments` table; index ticks never update `ticks_seen` / `last_tick_at`.
- Web helpers are cache-busted: bump `?v=` in `web/index.html` for every helper .js changed (`alarms.js`, `notify.js` if touched).
- The dev venv lacks fastapi/redis/psycopg/requests: keep logic in pure functions; `api/main.py`, `engine/main.py`, `ingest/main.py` are not importable in tests.
- Tests: `.venv/bin/python -m pytest tests -q`; node tests by file: `node --test tests/web/test_alarms.mjs` etc.
- Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. A day with no board rows (holiday, pre-open, a replay minute before the first alarm): every column says "nothing yet", the fold line reads zero, header fields are dashes, nothing throws. Pinned in Task 2 (`test_assemble_empty_day`) and Task 4 (`group` on `[]`).
2. A live alert whose trade row has not arrived yet (the row comes about a minute later): the alarm row still shows, with no odds, no dots, shares "—". Pinned in Task 2 (`test_alert_without_trade_row`).
3. A ref sent twice (retry, or a desk ticket on the same ref): the row's delivery is the latest `telegram` channel row, never the `telegram-desk` one. Pinned in Task 1 (`test_for_day_latest_telegram_only`).
4. A Telegram send that raises: recorded as `(False, '<error>')`, the flush continues with the next message. Pinned in Task 1 (`test_flush_records_failure_and_continues`).
5. An index tick before 09:15 or after 15:30: no candle, no Redis write of a bogus minute; a stale `alarms.seen` key for a column that no longer exists: ignored. Pinned in Task 3 (`test_index_state_ignores_out_of_session`) and Task 4 (`unread` with an unknown column key).

---

### Task 1: deliveries table, `telegram()` returns `(ok, error)`, every alarm send recorded

**Files:**
- Create: `db/migrations/006_alarms.sql`, `api/deliveries.py`, `tests/test_deliveries.py`
- Modify: `db/init.sql` (append the two tables, same text as the migration), `core/notify.py`, `engine/notify.py` (byte copy), `api/live_trades.py:123-170` (`flush_deferred`, `tick`), `api/live_desk.py:240-256` (`tick`), `engine/main.py:21-35` (`defer` fallback and the `TRADES_LIVE=0` branch at 168-171), `tests/test_notify.py`, `tests/test_live_trades.py`

**Interfaces:**
- Produces: `core.notify.telegram(text: str, silent: bool = False) -> tuple[bool, str | None]`: `(True, None)` on 2xx; `(False, "not configured")` without token/chat (still logs the would-send line); `(False, str(e))` on any exception (still logs the warning).
- Produces: `api.deliveries.record(con, day: dt.date, ref: str, channel: str, ok: bool, error: str | None, silent: bool, sent_at: dt.datetime) -> None` (one INSERT, caller commits) and `api.deliveries.for_day(con, day: dt.date) -> dict[str, dict]`: ref → `{"sent_at": iso str, "ok": bool, "error": str|None, "silent": bool}` of the **latest** row with `channel = 'telegram'`.
- Produces: `api.live_trades.flush_deferred(R, day, send, at, record=None) -> int`: `send(text, silent=...)` may return `(ok, error)` or `None` (treated as `(True, None)`); an exception from `send` is `(False, str(e))`; after every attempt `record(ref, ok, error, silent)` is called when given, and a raising `record` is logged, never re-raised.
- Produces: `api.live_desk.tick(connect, now, send, prepare=None)` records each verdict ticket with `channel='telegram-desk'` and `ref=rec["ref"]` through `deliveries.record` on `connect()`.
- Produces: engine `record_delivery(ref, ok, error, silent, now)` in `engine/main.py` (its own psycopg connection from `DATABASE_URL`, swallow and log errors) called by `defer()`'s direct fallback and by the `TRADES_LIVE=0` branch.

Migration text (`006_alarms.sql`, idempotent, applied with `docker compose exec -T postgres psql -U scanner -d scanner < db/migrations/006_alarms.sql`):
```sql
CREATE TABLE IF NOT EXISTS deliveries (
  id       BIGSERIAL PRIMARY KEY,
  day      DATE NOT NULL,
  ref      TEXT NOT NULL,            -- alert:ID · pattern:SYM|kind|formed_at (as in desk_calls / trade_rows)
  channel  TEXT NOT NULL,            -- telegram (the alarm's own message) · telegram-desk (the desk ticket)
  sent_at  TIMESTAMPTZ NOT NULL,
  ok       BOOLEAN NOT NULL,
  error    TEXT,                     -- 'not configured' when the token/chat id are empty
  silent   BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS deliveries_day_ref ON deliveries (day, ref);
CREATE TABLE IF NOT EXISTS index_1m (
  ts      TIMESTAMPTZ NOT NULL,
  symbol  TEXT NOT NULL,             -- NIFTY50
  open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,
  PRIMARY KEY (symbol, ts)
);
```

- [ ] **Step 1: Failing tests for `telegram()`** in `tests/test_notify.py` (reuse `_notify`):
```python
def test_telegram_returns_ok(monkeypatch):
    mod, sent = _notify(monkeypatch)
    assert mod.telegram("x") == (True, None)

def test_telegram_not_configured(monkeypatch):
    mod, sent = _notify(monkeypatch); monkeypatch.setattr(mod, "TOKEN", None)
    assert mod.telegram("x") == (False, "not configured") and sent == []

def test_telegram_reports_the_error(monkeypatch):
    mod, sent = _notify(monkeypatch)
    def boom(url, json=None, timeout=None): raise RuntimeError("timeout")
    monkeypatch.setattr(mod.requests, "post", boom)
    assert mod.telegram("x") == (False, "timeout")
```
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_notify.py -q` → 3 FAIL (returns None).
- [ ] **Step 3: Implement** in `core/notify.py`; `cp core/notify.py engine/notify.py`; `cmp core/notify.py engine/notify.py`.
- [ ] **Step 4: Run** the file → PASS.
- [ ] **Step 5: Failing tests for deliveries and the flush** in `tests/test_deliveries.py` (a `FakeCon` whose cursor records `execute(sql, params)` and answers `fetchall()` from a list, as `tests/test_live_trades.py` does) and in `tests/test_live_trades.py`:
```python
def test_record_inserts_one_row():            # sql contains "INSERT INTO deliveries"; the params carry day, ref, channel, sent_at, ok, error, silent (the INSERT's own order)
def test_for_day_latest_telegram_only():      # rows: (ref a, telegram, 09:35, ok), (ref a, telegram, 09:36, fail), (ref a, telegram-desk, 09:37, ok) → {'a': {... 09:36, ok False}}
def test_flush_records_ok_and_not_configured():   # send returns (True,None) then (False,'not configured'); record sees both with the item's silent flag
def test_flush_records_failure_and_continues():   # send raises on the first item; record gets (False,'boom'); the second item is still sent; returns 1
def test_flush_without_record_still_works():      # record=None: behaviour of the existing tests unchanged
```
- [ ] **Step 6: Run** both files → FAIL (no module / no kwarg).
- [ ] **Step 7: Implement** `api/deliveries.py`; `flush_deferred(..., record=None)`; in `live_trades.tick` pass `record=lambda ref, ok, err, silent: _record(connect, day, ref, "telegram", ok, err, silent, n)` where `_record` opens `connect()`, calls `deliveries.record`, commits, logs on failure; in `live_desk.tick` wrap `send(desk.telegram_text(rec))` so its `(ok, error)` (or `None`) is recorded as `telegram-desk`; in `engine/main.py` add `record_delivery` and call it from `defer()`'s fallback and the `TRADES_LIVE=0` branch (both use `telegram()`'s return). `api/main.py` already passes `core.notify.telegram` as `send`: no change there.
- [ ] **Step 8: Run** `.venv/bin/python -m pytest tests -q` → all pass (existing flush tests included).
- [ ] **Step 9: Migration file + `db/init.sql`** appended with the same two tables under a `-- 006_alarms` comment.
- [ ] **Step 10: Commit** `feat(alarms): deliveries table, telegram() returns (ok, error), every alarm send recorded`.

---

### Task 2: `api/alarms.py` and `GET /api/alarms`

**Files:**
- Create: `api/alarms.py`, `tests/test_alarms.py`
- Modify: `api/alerts.py:194-206` (`alerts_for_day` output gains `"story": payload.get("story")`), `api/main.py` (route, next to `/api/trades`)

**Interfaces:**
- Consumes: `tr.rows_for_day(con, day, upto)` rows (`id, ts, ref, symbol, side, source, entry, stop, target, rr, odds, size, gate, gate_reason, outcome, paper, label`); `al.alerts_for_day` rows (`id, ts, scan, symbol, price, tf, source, side, stop, target, outcome, story`); `desk.SELECT` rows of `desk_calls` with `kind='verdict'`; `deliveries.for_day`; `al.scan_stats(con, 20)`; `pt.stats(con, 20)` (dict from `core.patterns.summarise`: use its pooled `hit_rate_t1`-style total if present, else the mean over kinds); `rp.snapshot_at(con, day, at)` (`n_bull`, `n_bear`, `sectors` sector→avg pct); Redis `scan:latest`, `index:NIFTY50` (JSON `{ltp, prev_close, pct, ts}`), `index:NIFTY50:prev_close`; table `index_1m`.
- Produces (pure): `columns(board_scans: list[str], scan_sides: dict[str, str]) -> list[dict]` with keys `key, name, sub, side, scan`; order and names:
  `PDH break` → `pdh_brk / PDH breakout / crossed above yesterday's high / short`; `PDL break` → `pdl_brk / PDL breakdown / crossed below yesterday's low / long`; then `pat / Patterns / trade-grade triggers / None / None`; `PDH fade` → `pdh_fade / PDH fade / short · first 5m close back below`; `PDL fade` → `pdl_fade / PDL fade / long · first 5m close back above`; any other board scan → `scan:<name> / <name> / rule scan / <side from scan json>`; a board scan missing from `scans/` is skipped with a log line.
- Produces (pure): `assemble(day: dt.date, at: dt.datetime, *, alerts, trade_rows, desk_calls, deliveries, board_scans, scan_sides, records, breadth, index, telegram_on: bool, live: bool, pending: bool, patterns_total: int) -> dict` with exactly the spec's shape. Rules: alert rows with `scan in board_scans` become rows (`col` from `columns`), cut at `ts <= at`; trade rows with `source == 'pattern'` become the `pat` rows (`setup = label`, `price = entry`, `ts = row ts`) ; every row joins its trade row by ref (`alert:<id>`) for `odds, past (= odds.past.last[-12:]), shares (= size.shares), risk_inr, gate, gate_reason, outcome`; `desk` from the verdict with the same ref (`{call: output.call, confidence: output.confidence, at: HH:MM}`), `delivery` from `deliveries[ref]` else `None`; `source` `'alert'|'pattern'`; rows sorted by `ts` ascending; `folded = {"alerts": n alerts of non-board scans ≤ at, "scans": that distinct count, "patterns": max(0, patterns_total - n pat rows)}`; `timeline` per symbol (every row of the symbol carries the same list) sorted by `at`, each `{at: "HH:MM", kind: break|alarm|desk|exit, text}`: break → `"above PDH {line} at {break_at} · ran to {run_px} ({run_pct:+.1f} %)"` (`below PDL` for the long one; without a story just `"above PDH"`/`"below PDL"`); alarm → `"{setup} · SELL at {price}"` / `BUY`; desk → `"desk {CALL} {confidence}"`; exit → from the trade row's outcome: `"target at {exit_px}"`, `"stop at {exit_px}"`, `"flat at {exit_px}"` at `settled_at`; events after `at` are dropped.
- Produces (pure): `break_record(rows: list[dict], break_scan: str, fade_scan: str, days: int) -> dict | None`: rows `{ts, scan, symbol}` → `{"per_day": fires/trading days seen, "fade_pct": % of break (day, symbol) pairs with a fade row on the same day and symbol, "n": fires}`; `record_text(col_key, rec) -> str`: `"54 % hit · +0.3 % @60m · n 520"`, breaks `"137/day · 31 % faded"`, patterns `"62 % T1 · n 1,420"`, `"no record yet"` when `n < 20` or rec is None.
- Produces (DB): `records(con) -> dict[str, dict]` keyed by column key, each dict carrying its numbers and `"text": record_text(key, rec)`; `assemble` copies the matching dict onto `columns[i]["record"]` (None → `{"text": "no record yet"}`); `index_at(con, R, day, at, live) -> dict | None`; `breadth_at(con, R, day, at, live) -> dict | None` (`{bulls, bears, up: [[sector, pct]]*2, down: [[sector, pct]]*2}`); `board(con, R, day, at, live, pending) -> dict` that loads everything and calls `assemble`.
- Produces (route): `GET /api/alarms?day&at` in `api/main.py`: `day` required, `at` optional (today without `at` = now, `live=True`); parse with `rp.parse_replay_args` when `at` is given; `pending` and the `_kick_prepare` exactly as `/api/alerts` does; runs `board` in the executor; `telegram_on = bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))`.

- [ ] **Step 1: Failing tests** in `tests/test_alarms.py` with hand-built dicts (no DB):
```python
def test_columns_order_and_names():          # the five defaults + 'B0101' → keys ['pdh_brk','pdl_brk','pat','pdh_fade','pdl_fade','scan:B0101'] and the names/subs above; unknown scan skipped
def test_assemble_empty_day():               # no rows → every column present, rows [], folded zeros, breadth/index None → header None, nothing raises
def test_assemble_assigns_columns_and_cuts_at():   # a PDH break 09:20, a PDH fade 09:35, a bounce-failure alert 09:40 (folded), a pattern trade row 09:40; at=09:36 → 2 rows, folded alerts 0 (09:40 is after at), pattern not yet
def test_alert_without_trade_row():          # fade row with no trade row → odds None, past [], shares None, gate None
def test_join_trade_desk_delivery():         # fade ref 'alert:7' with a trade row (odds.past.last 14 entries → past is the last 12), a desk verdict take 72, a delivery → all three on the row
def test_timeline_order_for_one_symbol():    # break 09:20 (story), fade 09:35, desk 09:36, exit target 10:12 → 4 events in order with the exact texts; at=09:50 → 3 events
def test_break_record_and_texts():           # 10 breaks over 2 days, 3 followed by a fade same day+symbol → per_day 5.0, fade_pct 30.0; record_text for the four shapes incl. "no record yet"
```
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_alarms.py -q` → FAIL (no module).
- [ ] **Step 3: Implement** `api/alarms.py` (pure parts first), the `story` key in `alerts_for_day`, the DB loaders, the route. Index for a replay day: last `index_1m` close `<= at` on `day`, prev close = last close of the previous stored session in `index_1m`; live: the Redis JSON. Breadth live: `scan:latest`; replay: `rp.snapshot_at`.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests -q` → all pass.
- [ ] **Step 5: Commit** `feat(alarms): /api/alarms assembles the setup board (columns, rows, timeline, records, breadth, index, deliveries)`.

---

### Task 3: NIFTY 50 in ingest and the backfill

**Files:**
- Create: `tests/test_index_candle.py`
- Modify: `ingest/candles.py`, `ingest/main.py` (`on_connect`, `on_ticks`, `flush`, the 08:55 setup after `load_daily_refs`), `ingest/backfill.py` (`--index`), `tests/test_backfill.py`

**Interfaces:**
- Produces: `ingest.candles.INDEX_TOKEN = 256265`, `INDEX_SYMBOL = "NIFTY50"`; `split_ticks(ticks: list[dict], index_token: int = INDEX_TOKEN) -> tuple[list[dict], list[dict]]` (stock ticks, index ticks); `class IndexState(symbol: str = INDEX_SYMBOL)` with `last_price: float`, `cur: Candle | None`, `on_tick(tick: dict, now: dt.datetime) -> None` (OHLC from `tick["last_price"]`, volume/oi/delta stay 0; a tick outside `09:15 <= now.time() < 15:30` only updates `last_price`), `pop_closed() -> list[Candle]`, `finalize()`.
- Produces in `ingest/main.py`: `index = IndexState()`; `on_connect` also `subscribe([INDEX_TOKEN])` + `set_mode(MODE_QUOTE, [INDEX_TOKEN])`; `on_ticks` calls `split_ticks` first, counts only stock ticks in `ticks_seen` / `last_tick_at`, feeds index ticks to `index.on_tick` under the lock; `flush` inserts closed index candles into `index_1m` (`ON CONFLICT DO NOTHING`) and sets Redis `index:NIFTY50` = `{"ltp", "prev_close", "pct", "ts"}` (`pct` = `(ltp/prev_close-1)*100` when prev_close known, else `None`); `end_of_day` finalizes the index too; `load_index_ref()` after `load_daily_refs` in the morning setup: `hist(kite, INDEX_TOKEN, today-35d, today-1d, "day")[-1]["close"]` → Redis `index:NIFTY50:prev_close` (TTL 86400; failures logged, never fatal).
- Produces in `ingest/backfill.py`: `--index` flag: for each minute chunk `hist(kite, INDEX_TOKEN, a, b, "minute")` → `INSERT INTO index_1m(ts,symbol,open,high,low,close) ... ON CONFLICT DO NOTHING`; with `--index` and no `--symbols`, the stock loop still runs unless `--index-only` is also given (add that flag too); the summary line reports the index rows inserted.

- [ ] **Step 1: Failing tests** `tests/test_index_candle.py`:
```python
def test_split_ticks_separates_the_index():          # 3 ticks, one with instrument_token 256265 → ([2 stock], [1 index])
def test_index_state_builds_ohlc_per_minute():       # ticks 100, 101, 99 at 09:20:10/20/30 then 102 at 09:21:05 → closed [Candle 09:20 o100 h101 l99 c99 v0] and cur 09:21 o102
def test_index_state_ignores_out_of_session():       # a tick at 09:10 and at 15:31 → cur None, last_price updated
def test_finalize_moves_the_forming_candle():
```
and in `tests/test_backfill.py` a test that the parser accepts `--index` / `--index-only` and that the index insert helper (`index_rows(bars) -> list[tuple]`, a pure function in `backfill.py`) maps Kite bars to `(ts, "NIFTY50", o, h, l, c)`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** as in Interfaces. In `on_ticks`, the out-of-session branch also feeds index ticks (they only update `last_price` there).
- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests -q` → pass. `python -m py_compile ingest/main.py ingest/backfill.py` → no error.
- [ ] **Step 5: Commit** `feat(ingest): NIFTY 50 index candle in index_1m, live value in Redis, backfill --index`.

---

### Task 4: `web/alarms.js` pure helpers

**Files:**
- Create: `web/alarms.js`, `tests/web/test_alarms.mjs`

**Interfaces:**
- Produces `root.ScanAlarms = { group, unread, ladder, chartLevels, positionWords, deliveryMark, pastDots, timelineRows, fmtTime }` (same IIFE/export pattern as `web/watch.js`; node-importable via `createRequire`/`globalThis` the way `tests/web/test_watch.mjs` loads `watch.js`):
  - `group(rows, columns) -> {[key]: rows}` newest first (by `ts` desc), every column key present even when empty.
  - `unread(seen, rows, columns, visible) -> {counts: {[key]: n}, seen: {[key]: ts}}`: `seen` = `{key: newest ts seen}`; when `visible` is true, counts are all 0 and `seen` becomes each column's newest `ts`; when false, `counts[key]` = rows of that column with `ts > seen[key]` (`seen[key]` missing → every row counts) and `seen` is returned unchanged; keys in `seen` not in `columns` are dropped from the result.
  - `ladder(levels, price) -> [{name, value, now: bool}]`: `levels` = `[[name, value]]`; sorted high to low with one `{name:'NOW', value: price, now:true}` entry inserted at its place (above all when price > every level, below all when price < every level).
  - `chartLevels(levels, lo, hi, last) -> {near: [[name, value]], far: [[name, value]]}`: near when `v > lo - 0.5*(hi-lo)` and `v < hi + 0.5*(hi-lo)`, or `|v/last - 1| < 0.012`, or name is `STOP`/`TGT`; `far` the rest.
  - `positionWords(price, vwap, prev_high, prev_low, ladderOut) -> string[]`: `['above VWAP'|'below VWAP', 'between R1 and R2'|'above R2'|'below S2', 'above PDH'|'below PDL'|"inside yesterday's range"]`.
  - `deliveryMark(row) -> {text, cls}`: `delivery.ok` → `{"sent HH:MM:SS", "ok"}`; `delivery.error === 'not configured'` → `{"not configured","warn"}`; other error → `{"failed: <error>","bad"}`; no delivery and `source === 'replay'` → `{"replay","mut"}`; no delivery otherwise → `{"not recorded","mut"}`.
  - `pastDots(past) -> [{cls: 'ok'|'bad'|'mut', title: "2026-10-23 target 75 min"}]` for `[[day, result, minutes]]`, `result` target→ok, stop→bad, else mut; `[]` for null.
  - `timelineRows(timeline, at) -> [{at, kind, text}]` cut at `at` (HH:MM) and sorted.
  - `fmtTime(ts) -> "HH:MM"`.

- [ ] **Step 1: Failing tests** `tests/web/test_alarms.mjs` (node:test, one `it` per bullet above with the exact values; include `group([])`, `unread` with a stale key, `ladder` above-all and below-all, `chartLevels` with a far S2 and a near STOP, the three `positionWords` shapes, all five `deliveryMark` cases, `pastDots(null)`).
- [ ] **Step 2: Run** `node --test tests/web/test_alarms.mjs` → FAIL (cannot load).
- [ ] **Step 3: Implement** `web/alarms.js`.
- [ ] **Step 4: Run** → pass; also `node --test tests/web/test_desk.mjs tests/web/test_filter.mjs tests/web/test_notify.mjs tests/web/test_patterns.mjs tests/web/test_watch.mjs tests/web/test_trades.mjs tests/web/test_alarms.mjs` → pass.
- [ ] **Step 5: Commit** `feat(web): ScanAlarms helpers (grouping, unread badges, ladder, chart levels, delivery marks, past dots)`.

---

### Task 5: the Alarms tab and the stock panel in `web/index.html`

**Files:**
- Modify: `web/index.html` (script tags lines 7-12: add `<script src="alarms.js?v=<short sha>"></script>`; tabs line 224; the view blocks near 285-308; `setView` 796-803 and the hash restore at 793; `panelEl` 804; `wireChart` 491 (markers); the WS handler 1405; the per-minute live path and the slider/day change path that call `loadTrades`/`loadWatch`), `web/notify.js` only if a token bump is needed (it is not: no behaviour change)
- Test: none automated beyond Task 4; the implementer verifies through the dev proxy (below) and attaches a screenshot path in the report.

**Interfaces:**
- Consumes: `GET /api/alarms?day=&at=` (Task 2), `GET /api/watch?symbols=SYM&day=&at=` (numbers for the panel: `price, pct, vwap, prev_high, prev_low, prev_close, P, R1, R2, S1, S2, oi_pct, oi_label, oi_cls, rvol, sector, stop, target, rank`), `GET /api/candles/{symbol}?day=` through `wireChart(a)` with `panelEl()` returning `$('abody')` when `S.view === 'alarms'`; `ScanAlarms.*` (Task 4); `ScanTrades.oddsPair`, `fmt`, `sg`, `esc`, `verdictCard`, `outcomeLabel` as the Watch drawer uses them.
- Produces: `S.view === 'alarms'` (`#alarms`), `loadAlarms()`, `renderAlarms()`, `openAlarmPanel(row)`, `closeAlarmPanel()`; `S.alarms` (the last response), `S.asel` (the open row's ref), `S.aseen` (`localStorage` key `alarms.seen`, JSON).

Layout (the mockup `notifications-board-v10.html` is the reference; reuse the dashboard's CSS variables, `.wdrawer`, `.chipx`, `.lvls`, `.kv`, `.mono`):
- Header line above the columns: `BREADTH 142 ▲ / 71 ▼ · up AUTO +1.2 % PHARMA +0.8 % · down IT −0.9 % METAL −0.6 %` · `NIFTY 50 24,312 +0.4 %` · `Telegram: on | not set up` · `as of HH:MM`; dashes when a value is `null`.
- Columns grid (`repeat(3, 1fr)`, two rows at the default width): header = name, sub, count, the record text (`column.record.text`, served by Task 2), an unread badge; the list newest first, each row: symbol + S/L + desk chip, time, line 2 = price · stop · target (break rows: price only), odds pair, the past dots, delivery mark, result chip (`outcomeLabel`); click → `openAlarmPanel`.
- Fold line under the grid: `"879 alerts from 3 other scans · 97 pattern triggers below trade grade are not on the board"`.
- Panel = a `.wdrawer` (`#adrawer` / `#abody`): header (symbol, SELL/BUY pill, setup pill, fired HH:MM · now HH:MM · position words · sector; price large + pct chip), four tiles (VWAP with above/below + distance; OI vs yesterday with `oi_label`; RVOL; day range bar from the candles' hi/lo), timeframe row + `<canvas class="chart">` via `wireChart(a)` where `a` is the watch row plus `a.markers = timeline events` (`wireChart` draws each marker as a small triangle at its minute: grey for `break`, red/green for `alarm` by side, blue dot for `desk`, hollow square for `exit`) and `a.levels_extra = [["STOP", stop], ["TGT", target]]` drawn dashed; the level ladder (`ScanAlarms.ladder`) with the NOW row highlighted; the ticket card (stop / entry / target, R:R, now-vs-entry in R, red/green risk bar, odds bar with `oddsPair`, shares, gate PASS/BLOCK, desk call); the story timeline as a vertical list from `row.timeline`; buttons `Open in Scanner at HH:MM`, `Open in Trades`.
- Behaviour: `setView('alarms')` loads; the live minute (`onLiveSnapshot`) reloads when following; the slider / day change reloads with `day` and `at`; WS `trades` and `patterns` messages reload when the view is `alarms`; the tab button shows the total unread from `ScanAlarms.unread(...)` computed on every load with `visible = (S.view === 'alarms' && !document.hidden)`; the per-column badges likewise; `Esc` closes the panel; the panel re-renders on each reload while open (chart grows live).
- Cache-busting: `alarms.js?v=` = first 7 chars of the commit that added it (or any new token).

- [ ] **Step 1: Add the script tag, tab button, view block, hash handling, `panelEl` branch.** `node --check` is not applicable; open the page through the dev proxy (below) and confirm the tab appears with "nothing yet" columns.
- [ ] **Step 2: `loadAlarms` / `renderAlarms`** (header line, columns, badges, fold line).
- [ ] **Step 3: `openAlarmPanel` / `closeAlarmPanel`** with the tiles, chart (+ markers and extra levels in `wireChart`), ladder, ticket, timeline, buttons.
- [ ] **Step 4: Wire the reloads** (live minute, slider, day, WS) and the unread bookkeeping.
- [ ] **Step 5: Verify through the dev proxy** on Mon 2 Nov: `ssh -f -N -L 18000:127.0.0.1:8000 scanradar-vps` (if not up), then the static server from the scratchpad `devproxy.py` (port 5173, proxies `/api` to 18000) → `http://localhost:5173/#alarms` with the date 2026-11-02 at 15:30: six columns filled (PDH breakout 137, PDL breakdown 19, PDH fade 43, PDL fade 2, B0101 6, Patterns = Monday's pattern trade rows), the fold line, "replay" marks, NIFTY header (dash until the backfill), a panel for ULTRACEMCO (PDH fade 09:35) with the chart, ladder and ticket. Take a screenshot (built-in browser) and name its path in the report.
- [ ] **Step 6: Run** every node test file and `.venv/bin/python -m pytest tests -q` → pass.
- [ ] **Step 7: Commit** `feat(web): Alarms tab — setup board, stock panel, badges, breadth/NIFTY/Telegram header`.

---

### Task 6: docs

**Files:**
- Modify: `CLAUDE.md` (an `### api/alarms.py + web/alarms.js (Alarms board, added 3 Nov 2026)` section under api/, the ingest paragraph for the index, the Deploy section's one-command rule noting migration 006 and that this deploy rebuilds ingest too, a Status bullet for 3 Nov), `README.md` only if it lists the tabs.

- [ ] **Step 1: Write** the section: the endpoint shape, `BOARD_SCANS`/`ALARMS_SCANS`, the Patterns-column rule, deliveries channels, the index table and Redis keys, `backfill.py --index`, the chime/badge rule, the dev-proxy check.
- [ ] **Step 2: Commit** `docs: Alarms board (api/alarms, deliveries, NIFTY 50 index, web tab)`.

---

Deploy (the controller does this after the final review, not a subagent): on the VPS `git pull` (branch `feat/alarms` or `main` after the merge), migration 006 via stdin, `docker compose up -d --build api engine ingest` (ingest included: it is idle at night), `docker compose run --rm --no-deps ingest python backfill.py --index --index-only --days 60`, then `curl /api/alarms?day=2026-11-02&at=15:30` and `/api/health`; check `docker compose logs --tail 40 ingest api engine` for tracebacks; the dashboard's Alarms tab on Monday's date; at 09:20 Tue the first live row and its delivery mark.
