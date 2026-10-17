# History, Replay and Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backfill ~41 trading days of 1-minute candles with OI, let the API replay the ranking and rule alerts for any stored day as of any minute, and rebuild the dashboard to the reference artifact's look with a date selector, time slider, chart timeframes 1m–1D and a per-day alert list.

**Architecture:** A new top-level `core/` package holds the analytics, rule engine, ranking and backtest (byte-identical copies of the engine's analytics for Phase 1; the engine switches to it in Phase 2 after one clean live day). `ingest/backfill.py` fills `candles_1m`, `candles_1d` and `daily_ref` from Kite's historical API, gap-fill only. The FastAPI service imports `core/` and serves `/api/days`, `/api/replay`, per-day `/api/alerts` and daily candles; the browser resamples intraday timeframes itself. `web/index.html` stays a single static file.

**Tech Stack:** Python 3.12, pandas 2.2.3 / numpy 2.1.3, FastAPI 0.115, psycopg 3, TimescaleDB 2.17 (PG16), Redis 7, kiteconnect 5.0.1, vanilla JS + canvas, Caddy 2.8. Tests: pytest 8.

**Spec:** `docs/superpowers/specs/2026-10-17-history-replay-dashboard-design.md`

## Global Constraints

- Phase 1 must not modify `engine/` or `ingest/main.py`'s live path (only the `_hist` extraction in Task 5). Phase 2 (Task 12) runs only after Saurabh confirms one clean live day.
- One as-of semantic everywhere: complete bars are `ts < at`; the `off 0` bar is a stub of the `ts == at` bar with `open=high=low=close=that bar's open, volume=0, delta=0, oi=previous bar's oi`. Never let the completed `at` bar into `off 0`.
- Backfilled rows carry `delta = 0`; inserts into `candles_1m` use `ON CONFLICT DO NOTHING`; `candles_1d.oi` is write-once (`COALESCE(NULLIF(candles_1d.oi,0), EXCLUDED.oi)`).
- No code path other than `kite_auth.session()` may log in to Kite; backfill and API never call `kite_auth.login()`.
- Kite historical pacing 0.4 s per call, retry on `NetworkException` (existing `_hist`).
- All times IST; `candles_1m.ts` is minute start.
- Replay alerts are never computed for the current day before 15:35 IST.
- Operator commands in docs must be runnable verbatim (no placeholders).
- No new runtime dependencies beyond pandas/numpy in the api image. No build step for the web page. External assets only Google Fonts (already used).
- Images must not contain `.env`: Dockerfiles use explicit `COPY <dir>/ ./` lines, never `COPY . .` from the repo root.

## Review Focus

1. A symbol with no 1-minute bar at `at` (illiquid, or a day it did not trade): `view_at` returns the complete bars with no stub and `rank()` still produces a row for it → pinned in Task 1 (`test_view_at_no_bar_at_minute`).
2. A replay day where a symbol has no `daily_ref` row: that symbol is skipped, the snapshot still returns → pinned in Task 2 (`test_rank_skips_symbol_without_ref`).
3. `/api/replay` with `at` outside 09:15–15:30, a weekend/holiday date, or a date with no candles → HTTP 400/404 with a one-line message, never a 500 → pinned in Task 6 (`test_parse_replay_args`).
4. Backfill run during market hours must not fetch today's partial day, otherwise its `delta=0` rows would win the `(symbol, ts)` conflict against live rows that arrive seconds later → pinned in Task 5 (`test_backfill_window_excludes_open_session`).
5. Per-day alert queries must use IST day boundaries on UTC-stored `timestamptz` → pinned in Task 7 (`test_ist_day_bounds`).

---

### Task 1: `core/` package, as-of view, test harness

**Files:**
- Create: `core/__init__.py`, `core/analytics.py` (copy of `engine/analytics.py`), `core/rules.py` (copy of `engine/rules.py`), `core/asof.py`
- Create: `requirements-dev.txt`, `tests/conftest.py`, `tests/test_asof.py`
- Modify: `.gitignore` (add `.pytest_cache/`)

**Interfaces:**
- Produces: `core.asof.view_at(df1m: pd.DataFrame, at: pd.Timestamp) -> pd.DataFrame` — `df1m` has columns `ts, open, high, low, close, volume, oi, delta`, ascending, one symbol, one day, tz-aware IST `ts`. Returns a new frame: rows with `ts < at`, plus one stub row when a row with `ts == at` exists.
- Produces: `tests.conftest.synthetic_day(seed: int = 1, start: str = "09:15", minutes: int = 375, with_gap_at: list[str] | None = None) -> pd.DataFrame` — deterministic random-walk day in the same column layout, `with_gap_at` removes those minutes.
- Produces: `core.analytics.analyse`, `core.analytics.score`, `core.rules.resample`, `core.rules.evaluate` — identical to the engine's.

- [ ] **Step 1: Create the package by copying, and the dev requirements**

`cp engine/analytics.py core/analytics.py && cp engine/rules.py core/rules.py`; empty `core/__init__.py`. `requirements-dev.txt`:

```
pandas==2.2.3
numpy==2.1.3
pytest==8.3.3
```

Set up: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`.

- [ ] **Step 2: Write the fixture and the failing tests**

`tests/conftest.py` defines `synthetic_day` as a plain function and a `day` fixture returning `synthetic_day()`. `tests/test_asof.py`:

```python
import pandas as pd
from core.asof import view_at
from conftest import synthetic_day

IST = "Asia/Kolkata"

def test_view_at_complete_bars_strictly_before_at(day):
    at = pd.Timestamp("2026-10-16 10:00", tz=IST)
    v = view_at(day, at)
    assert (v.ts.iloc[:-1] < at).all() and v.ts.iloc[-1] == at

def test_view_at_stub_is_flat_at_open_with_prev_oi(day):
    at = pd.Timestamp("2026-10-16 10:00", tz=IST)
    bar = day[day.ts == at].iloc[0]; prev = day[day.ts < at].iloc[-1]
    stub = view_at(day, at).iloc[-1]
    assert stub.open == stub.high == stub.low == stub.close == bar.open
    assert stub.volume == 0 and stub.delta == 0 and stub.oi == prev.oi

def test_view_at_no_bar_at_minute():
    d = synthetic_day(with_gap_at=["10:00"])
    at = pd.Timestamp("2026-10-16 10:00", tz=IST)
    v = view_at(d, at)
    assert len(v) == (d.ts < at).sum() and v.ts.iloc[-1] < at

def test_view_at_first_minute_stub_oi_is_bars_own(day):
    at = pd.Timestamp("2026-10-16 09:15", tz=IST)
    v = view_at(day, at)
    assert len(v) == 1 and v.iloc[0].oi == day.iloc[0].oi

def test_view_at_does_not_mutate_input(day):
    before = day.copy(); view_at(day, pd.Timestamp("2026-10-16 11:00", tz=IST))
    pd.testing.assert_frame_equal(before, day)
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_asof.py -v`
Expected: FAIL, `ModuleNotFoundError: core.asof`

- [ ] **Step 4: Implement `view_at` in `core/asof.py`**

Signature above. Use `df1m.ts.searchsorted(at)` for the split.

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_asof.py -v` → 5 PASS. Also `diff engine/analytics.py core/analytics.py && diff engine/rules.py core/rules.py` → no output.

- [ ] **Step 6: Commit**

```bash
git add core tests requirements-dev.txt .gitignore
git commit -m "core: shared analytics package and as-of view"
```

---

### Task 2: `core/ranking.rank`

**Files:**
- Create: `core/ranking.py`, `tests/test_ranking.py`

**Interfaces:**
- Consumes: `core.analytics.analyse(sym, df, ref) -> dict | None`, `core.analytics.score(a, sec_avg) -> float`.
- Produces: `core.ranking.rank(frames: dict[str, pd.DataFrame], refs: dict[str, dict], ts: datetime.datetime, mode: str = "live") -> dict | None`. Snapshot keys: `ts` (ISO), `mode`, `delta_available` (bool), `sectors` (dict sector→avg pct), `n_bull`, `n_bear`, `bulls` (≤40), `bears` (≤40). Each row is the `analyse()` dict plus `score, rank, pctile, conv, side_rank, picked`. Returns `None` when no symbol analyses. `TOP_N = 3`, picked requires `rr >= 1.5`. Logic is lifted verbatim from `engine/main.py:50-77`.

- [ ] **Step 1: Write the failing tests**

```python
def test_rank_matches_engine_formulas(three_symbol_frames, refs):
    snap = rank(three_symbol_frames, refs, ts=NOW)
    rows = snap["bulls"] + snap["bears"]
    assert [r["rank"] for r in sorted(rows, key=lambda r: -r["score"])] == [1, 2, 3]
    top = max(rows, key=lambda r: r["score"]); assert top["pctile"] == 100 and top["conv"] == 99
    assert all(r["picked"] == (r["side_rank"] <= 3 and r["rr"] >= 1.5) for r in rows)

def test_rank_skips_symbol_without_ref(three_symbol_frames, refs):
    refs = {k: v for k, v in refs.items() if k != "B"}
    snap = rank(three_symbol_frames, refs, ts=NOW)
    assert {r["symbol"] for r in snap["bulls"] + snap["bears"]} == {"A", "C"}

def test_rank_delta_available_false_when_all_zero(three_symbol_frames, refs):
    for f in three_symbol_frames.values(): f["delta"] = 0
    assert rank(three_symbol_frames, refs, ts=NOW, mode="replay")["delta_available"] is False

def test_rank_returns_none_without_rows():
    assert rank({}, {}, ts=NOW) is None
```

Fixtures in `tests/test_ranking.py`: three `synthetic_day(seed=i)` frames and refs with `prev_close/prev_high/prev_low/prev_oi/avg_vol_1m/sector`.

- [ ] **Step 2: Run to verify they fail** — `ModuleNotFoundError: core.ranking`.

- [ ] **Step 3: Implement `rank` in `core/ranking.py`** by moving the body of `engine/main.py:51-77` without changing arithmetic; add `mode` and `delta_available = any((f.delta != 0).any() for f in frames.values())`.

- [ ] **Step 4: Run to verify they pass** — 4 PASS.

- [ ] **Step 5: Commit** — `git commit -m "core: ranking snapshot extracted from engine"`

---

### Task 3: `core/backtest.fires`

**Files:**
- Create: `core/backtest.py`, `tests/test_backtest.py`

**Interfaces:**
- Consumes: `core.asof.view_at`, `core.rules.resample`, `core.rules.evaluate`.
- Produces: `core.backtest.fires(scan: dict, df1m: pd.DataFrame, ctx: dict, cooldown_min: int) -> list[pd.Timestamp]` — minutes at which the scan fires with the live as-of view, cooldown applied per symbol (a fire suppresses further fires for `cooldown_min` minutes).
- Produces: `core.backtest.fires_naive(...)` with the same signature: reference implementation `for each ts in df1m.ts: evaluate(scan, resample(view_at(df1m, ts), tf), ctx)`. Kept in the module (used by tests and as documentation of the semantic).
- Produces: `core.backtest.max_offset(scan) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
BOUNCE = json.load(open("scans/bounce_failure_short.json"))
VWAP_LONG = json.load(open("scans/vwap_reclaim_long.json"))

@pytest.mark.parametrize("scan", [BOUNCE, VWAP_LONG])
@pytest.mark.parametrize("seed", range(6))
def test_fires_equals_naive(scan, seed):
    d = synthetic_day(seed=seed); d["close"] *= 12  # push price > 1000 so BOUNCE can fire
    ctx = {"prev_day_close": float(d.close.iloc[0]) * 1.01, "prev_day_high": 0, "prev_day_low": 0}
    assert fires(scan, d, ctx, cooldown_min=0) == fires_naive(scan, d, ctx, cooldown_min=0)

def test_cooldown_suppresses_refires():
    scan = {"name": "always", "timeframe": "1m", "match": "all",
            "rules": [{"lhs": {"off": 0, "field": "close"}, "op": ">", "rhs": {"number": 0}}]}
    out = fires(scan, synthetic_day(), {}, cooldown_min=30)
    assert len(out) == 13 and (out[1] - out[0]) == pd.Timedelta(minutes=30)

def test_off0_is_stub_not_completed_bar():
    # rule: forming bar's high > forming bar's open can never hold for a stub
    scan = {"name": "x", "timeframe": "1m", "match": "all",
            "rules": [{"lhs": {"off": 0, "field": "high"}, "op": ">", "rhs": {"off": 0, "field": "open"}}]}
    assert fires(scan, synthetic_day(), {}, 0) == []

def test_fires_perf_budget():
    t0 = time.perf_counter(); fires(BOUNCE, synthetic_day(), {"prev_day_close": 1}, 30)
    assert time.perf_counter() - t0 < 0.25
```

- [ ] **Step 2: Run to verify they fail** — `ModuleNotFoundError: core.backtest`.

- [ ] **Step 3: Implement `fires_naive`, then `fires`**

`fires`: compute the window id of every minute (`(ts - 09:15) // tf`), build the completed resampled bars once with `resample(df1m, tf)` (their OHLCV and cumulative VWAP do not depend on `at`), and for minute index `i` assemble the forming bar from the window's bars with index `< i` plus the stub (`open` = first open in window or stub open, `high/low` = running max/min including stub open, `close` = stub open, `volume` = running sum, `oi` = stub oi), with `vwap` = (cumulative tp×vol of completed windows + forming tp×vol) / cumulative volume (same `where(cv>0,1)` guard as `resample`). Evaluate on a frame of the last `max_offset(scan)+1` bars. Apply cooldown. The test against `fires_naive` is the proof.

- [ ] **Step 4: Run to verify they pass** — all PASS, including the 0.25 s budget.

- [ ] **Step 5: Commit** — `git commit -m "core: backtest fires() reproducing the live as-of view"`

---

### Task 4: Schema migration and compose mounts

**Files:**
- Create: `db/migrations/001_history.sql`
- Modify: `db/init.sql` (append the same statements), `docker-compose.yml` (postgres: mount `./db/migrations:/migrations:ro`; api: mount `./scans:/app/scans:ro`)

**Interfaces:**
- Produces: table `candles_1d(day DATE, symbol TEXT, open, high, low, close DOUBLE PRECISION, volume BIGINT, oi BIGINT, PRIMARY KEY(symbol, day))`; column `alerts.source TEXT NOT NULL DEFAULT 'live'`; index `alerts_scan_ts ON alerts(scan, ts)`.

- [ ] **Step 1: Write the migration** (idempotent: `IF NOT EXISTS` everywhere; no expression index on a non-immutable cast).

- [ ] **Step 2: Append the same statements to `db/init.sql` and add the two mounts.**

- [ ] **Step 3: Apply on the VPS and verify**

```bash
ssh scanradar-vps 'cd /opt/scanradar && git pull -q && docker compose up -d postgres && sleep 5 && docker compose exec -T postgres psql -U scanner -d scanner -f /migrations/001_history.sql && docker compose exec -T postgres psql -U scanner -d scanner -c "\d candles_1d" -c "\d alerts"'
```

Expected: `candles_1d` listed with the six columns; `alerts` shows `source | text | not null | 'live'`. Running the migration a second time prints only `NOTICE ... already exists`.

- [ ] **Step 4: Commit** — `git commit -m "db: candles_1d, alerts.source, migration file"`

---

### Task 5: Backfill

**Files:**
- Create: `ingest/hist.py`, `ingest/backfill.py`, `tests/test_backfill.py`
- Modify: `ingest/main.py` (replace `_hist` with `from hist import hist`; call `hist(kite, ...)`), `README.md` (new section "History and replay", commands only; prose completed in Task 11)

**Interfaces:**
- Produces: `ingest.hist.hist(kite, token: int, frm: date, to: date, interval: str, oi: bool = False, tries: int = 4) -> list[dict]` — `_hist` moved, with `kite` as a parameter.
- Produces: `ingest.backfill.merge_oi(eq: list[dict], fut: list[dict]) -> list[tuple]` — rows `(ts, open, high, low, close, volume, oi, delta=0)`; OI forward-filled within a calendar day, 0 before the first futures bar of that day.
- Produces: `ingest.backfill.fetch_window(now: datetime, days: int) -> tuple[date, date]` — `(frm, to)`; `to` is yesterday when `now` is a weekday before 15:35 IST, else today.
- Produces: `ingest.backfill.REBUILD_DAILY_REF_SQL: str` — one `INSERT ... SELECT ... ON CONFLICT (day,symbol) DO UPDATE` using `lag()` over `(PARTITION BY symbol ORDER BY day)` for prev close/high/low/oi and `avg(volume) OVER (... ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) / 375` for `avg_vol_1m`, skipping rows where `prev_close IS NULL`.
- CLI: `python backfill.py [--days 60] [--daily-days 400] [--symbols A,B]`.

- [ ] **Step 1: Write the failing tests** (pure functions only)

```python
def test_merge_oi_forward_fills_within_day_only():
    eq = [{"date": T("09:15"), "open":1,"high":1,"low":1,"close":1,"volume":5},
          {"date": T("09:16"), ...}, {"date": T("09:17"), ...}, {"date": T2("09:15"), ...}]
    fut = [{"date": T("09:16"), "oi": 100}]
    rows = merge_oi(eq, fut)
    assert [r[6] for r in rows] == [0, 100, 100, 0] and all(r[7] == 0 for r in rows)

def test_backfill_window_excludes_open_session():
    assert fetch_window(IST_DT("2026-10-19 11:00"), 60)[1] == date(2026, 10, 18)
    assert fetch_window(IST_DT("2026-10-19 16:00"), 60)[1] == date(2026, 10, 19)
    assert fetch_window(IST_DT("2026-10-18 11:00"), 60)[1] == date(2026, 10, 18)  # Sunday

def test_rebuild_daily_ref_sql_mentions_write_once_window():
    assert "ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING" in REBUILD_DAILY_REF_SQL
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement `hist.py`, `backfill.py`**

`backfill.main()`: `kite = kite_auth.session(R)` wrapped so that a missing/stale token exits 2 with the message `no valid Kite token cached; run the preflight (or wait for the 08:55 login) and retry` — it must not fall through to `login()`; achieve this by checking `R.get("kite:access_token")` first and calling `KiteConnect.profile()` yourself. Universe from the `instruments` table (fallback `universe.build`). Per symbol: equity minute (chunks of ≤60 days), futures minute `oi=True`, `merge_oi`, `executemany` into `candles_1m ... ON CONFLICT DO NOTHING` using `cur.rowcount` for inserted count; equity/futures daily → `candles_1d` with `ON CONFLICT (symbol,day) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, oi=COALESCE(NULLIF(candles_1d.oi,0), EXCLUDED.oi)`. Then execute `REBUILD_DAILY_REF_SQL`. Log one line per symbol `SYM: +N 1m rows (M existed), D daily`, and a final summary. Also update `ingest/main.py` to import `hist`.

- [ ] **Step 4: Run tests** — PASS; `python -m py_compile ingest/*.py`.

- [ ] **Step 5: Run on the VPS and verify**

```bash
ssh scanradar-vps 'cd /opt/scanradar && git pull -q && docker compose build -q ingest && docker compose run --rm -T ingest python backfill.py 2>&1 | tail -15'
```

Expected: ends with a summary like `done: 213 symbols, 41 trading days, N 1m rows inserted, 0 failures` within ~8 minutes. Then:

```bash
ssh scanradar-vps 'cd /opt/scanradar && docker compose exec -T postgres psql -U scanner -d scanner -c "SELECT count(*), min(ts), max(ts) FROM candles_1m" -c "SELECT count(DISTINCT day) FROM candles_1d WHERE symbol='"'"'RELIANCE'"'"'" -c "SELECT count(*) FROM daily_ref" -c "SELECT count(*) FROM candles_1m WHERE symbol='"'"'RELIANCE'"'"' AND oi>0"'
```

Expected: ~3M rows spanning ~60 calendar days; ≥ 250 daily days for RELIANCE; `daily_ref` ≈ 213 × trading days; RELIANCE rows with OI > 0 ≈ 375 × trading days. Re-run the backfill: summary reports `0 1m rows inserted`.

- [ ] **Step 6: Commit** — `git commit -m "ingest: historical backfill of 1m/1d candles and daily refs"`

---

### Task 6: API — `/api/days` and `/api/replay`

**Files:**
- Create: `api/replay.py`, `api/.dockerignore` (unused if context is root; instead create root `.dockerignore` with `.env`, `.git`, `graft/`, `docs/`, `web/`, `.venv/`), `tests/test_api_replay.py`
- Modify: `api/Dockerfile` (`COPY api/ ./` and `COPY core/ ./core/`; add `pandas==2.2.3 numpy==2.1.3`), `docker-compose.yml` (api `build: {context: ., dockerfile: api/Dockerfile}`), `api/main.py`

**Interfaces:**
- Produces: `api.replay.parse_replay_args(day: str, at: str) -> tuple[date, datetime]` — raises `HTTPException(400, msg)` for malformed values, `at` outside `09:15..15:30`, or a Saturday/Sunday.
- Produces: `api.replay.load_day(con, day: date, upto: datetime | None) -> dict[str, pd.DataFrame]` — one SQL over `candles_1m` for the IST day, `ts <= upto` when given, grouped by symbol.
- Produces: `api.replay.load_refs(con, day: date) -> dict[str, dict]` — `daily_ref` joined with `instruments.sector`.
- Produces: `api.replay.snapshot_at(con, day: date, at: datetime) -> dict | None` — `view_at` per symbol then `core.ranking.rank(..., mode="replay")`.
- Produces: `GET /api/days -> {"days": [...], "latest": str | None, "live": bool}` — distinct IST dates of RELIANCE rows in `candles_1m`, newest first; `live` = `ingest:ws == "connected"`.
- Produces: `GET /api/replay?day=YYYY-MM-DD&at=HH:MM -> snapshot` — Redis cache `replay:{day}:{HH:MM}` TTL 7 days; 404 `no candles for that day` when `load_day` is empty; computation runs in `run_in_executor`.

- [ ] **Step 1: Write the failing tests** (pure helpers)

```python
def test_parse_replay_args():
    d, at = parse_replay_args("2026-10-16", "10:00")
    assert d == date(2026,10,16) and at.hour == 10 and at.tzinfo is not None
    for bad in [("2026-10-16","09:00"), ("2026-10-16","15:31"), ("2026-10-18","10:00"), ("nope","10:00"), ("2026-10-16","1000")]:
        with pytest.raises(HTTPException) as e: parse_replay_args(*bad)
        assert e.value.status_code == 400

def test_snapshot_at_uses_stub(monkeypatch):
    # monkeypatch load_day/load_refs to return one synthetic symbol; assert the top row's price == open of the `at` bar
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement** `api/replay.py` and the two routes; Dockerfile/compose/.dockerignore changes.

- [ ] **Step 4: Run tests** — PASS.

- [ ] **Step 5: Deploy and verify on the VPS**

```bash
ssh scanradar-vps 'cd /opt/scanradar && git pull -q && docker compose up -d --build api 2>&1 | tail -2 && sleep 3 && curl -s 127.0.0.1:8000/api/days | head -c 200; echo; time curl -s -o /dev/null -w "%{http_code}\n" "127.0.0.1:8000/api/replay?day=2026-10-16&at=10:00"; time curl -s "127.0.0.1:8000/api/replay?day=2026-10-16&at=10:00" | python3 -c "import sys,json; s=json.load(sys.stdin); print(s[\"mode\"], s[\"delta_available\"], s[\"n_bull\"], s[\"n_bear\"], s[\"bulls\"][0][\"symbol\"], s[\"bulls\"][0][\"oi_label\"])"; curl -s -o /dev/null -w "%{http_code}\n" "127.0.0.1:8000/api/replay?day=2026-10-18&at=10:00"'
```

Expected: days list with ≥ 40 entries; first replay call 200 in < 3 s cold, second < 0.2 s; prints `replay False <n> <m> <SYM> <label>`; the Sunday request returns 400. Confirm the api image has no `.env`: `docker compose exec -T api ls -a /app | grep -c '^.env$'` → `0`.

- [ ] **Step 6: Commit** — `git commit -m "api: /api/days and /api/replay with as-of ranking"`

---

### Task 7: API — per-day alerts with replay cache, daily candles

**Files:**
- Create: `api/alerts.py`, `tests/test_api_alerts.py`
- Modify: `api/main.py`

**Interfaces:**
- Produces: `api.alerts.ist_day_bounds(day: date) -> tuple[datetime, datetime]` — `[00:00 IST, next 00:00 IST)` tz-aware.
- Produces: `api.alerts.scan_files() -> list[tuple[dict, str]]` — `(scan, sha256 hex of file bytes)` for `/app/scans/*.json`.
- Produces: `api.alerts.ensure_replay_alerts(con, day: date, frames, refs, now: datetime) -> None` — for each scan: if no `source='replay'` rows for `(day, scan)` or any stored `payload->>'scan_hash'` differs, delete that day's replay rows for the scan and insert `fires()` results with `ts = minute`, `price = stub open`, `payload = {"tf": tf, "scan_hash": h, "source": "replay"}`. No-op when `day == today` and `now < 15:35 IST`.
- Produces: `api.alerts.merge_live_replay(rows: list[dict]) -> list[dict]` — drops a replay row when a live row with the same `scan` and `symbol` exists within ±1 minute.
- Produces: `GET /api/alerts?day=YYYY-MM-DD` → list of `{ts, scan, symbol, price, tf, source}` newest first; `GET /api/alerts?limit=` unchanged.
- Produces: `GET /api/candles/{symbol}?tf=1d&days=250` → `{"symbol", "bars": [{"day","o","h","l","c","v","oi"}]}` ascending; without `tf` the existing 1-minute response, now also including `"delta_available": bool`.

- [ ] **Step 1: Write the failing tests**

```python
def test_ist_day_bounds():
    a, b = ist_day_bounds(date(2026,10,16))
    assert a.isoformat() == "2026-10-16T00:00:00+05:30" and (b - a) == timedelta(days=1)

def test_merge_live_replay_prefers_live_within_one_minute():
    rows = [{"scan":"s","symbol":"X","ts":T("10:05"),"source":"live"},
            {"scan":"s","symbol":"X","ts":T("10:06"),"source":"replay"},
            {"scan":"s","symbol":"X","ts":T("10:40"),"source":"replay"}]
    assert [r["ts"] for r in merge_live_replay(rows)] == [T("10:05"), T("10:40")]

def test_ensure_replay_alerts_skips_open_day(monkeypatch):
    # today before 15:35 → no SQL executed (use a recording fake cursor)
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement** `api/alerts.py` and the routes (computation off the event loop; reuse `api.replay.load_day/load_refs`).

- [ ] **Step 4: Run tests** — PASS.

- [ ] **Step 5: Deploy and verify**

```bash
ssh scanradar-vps 'cd /opt/scanradar && git pull -q && docker compose up -d --build api 2>&1 | tail -1 && sleep 3 && time curl -s "127.0.0.1:8000/api/alerts?day=2026-10-16" | python3 -c "import sys,json; a=json.load(sys.stdin); print(len(a), a[:2])"; time curl -s "127.0.0.1:8000/api/alerts?day=2026-10-16" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"; curl -s "127.0.0.1:8000/api/candles/RELIANCE?tf=1d&days=5" | head -c 300'
```

Expected: first call completes (cold, computing both scans for 213 symbols) in under 60 s and returns a non-empty list with `source: "replay"`; second call < 0.3 s with the same count; daily bars for 5 days. Then `docker compose exec -T postgres psql -U scanner -d scanner -c "SELECT scan, count(*) FROM alerts WHERE source='replay' GROUP BY 1"`.

- [ ] **Step 6: Commit** — `git commit -m "api: per-day alerts with replay cache; daily candles"`

---

### Task 8: Dashboard — artifact look, themes, live parity

**Files:**
- Modify: `web/index.html` (rewrite; keep it one file; sections in order: `<style>`, markup, `<script>` with named functions `state`, `api`, `render*`, `chart*`, `controls*`)

**Interfaces:**
- Consumes: `/api/scan`, `/ws/scan`, `/api/candles/{sym}` (unchanged shapes).
- Produces: page-level state object `S = {mode:'live'|'replay', day, at, snap, selected, tf, theme, days, live}` and functions `renderAll()`, `renderCards()`, `renderDetail()`, `drawChart(cands, a, opts)`, `setStatus(text, cls)` used by Tasks 9–11.

- [ ] **Step 1: Port the artifact's CSS verbatim** (tokens, grid, card, chip, kv, conv dots, sector bars, light + dark blocks under `prefers-color-scheme` and `[data-theme]`), add `--muted` "—" styling for unavailable values, and a theme toggle button that sets `data-theme` and persists to `localStorage('theme')`.

- [ ] **Step 2: Re-implement rendering** for the live snapshot with the artifact's card and detail markup (symbol, PICK chip, % change, OI chip, OI %, VWAP side, RVOL, rank/percentile; detail header with price, change, big %, side chip, conviction number + 5 dots, OI chip; kv table with OI Δ, Vol Δ, VWAP, RVOL, OR H/L, OB, S1/S2, R1/R2, Stop, Target, Risk, R:R; the PICKED/NOT PICKED rule line). Add to the detail eyebrow: `SECTOR +x.xx% · vs sector +y.yy%` from `snap.sectors`. Status chip shows `LIVE` with a pulsing dot when `mode==='live'`.

- [ ] **Step 3: Verify in the browser against production**

Push, then `ssh scanradar-vps 'cd /opt/scanradar && git pull -q'` (web is a read-only bind mount; no restart). Open https://scanradar.tech in the built-in browser (sign in as `saurabh`), screenshot in both themes. Expected: layout matches the artifact; with no live snapshot the header shows `WAITING` and panels are empty but styled; no console errors (`read_console_messages onlyErrors`).

- [ ] **Step 4: Commit** — `git commit -m "web: artifact look, light/dark themes, live rendering"`

---

### Task 9: Dashboard — date selector, time slider, replay mode

**Files:**
- Modify: `web/index.html`

**Interfaces:**
- Consumes: `/api/days`, `/api/replay?day&at`.
- Produces: `setDay(day)`, `setAt('HH:MM')`, `goLive()`, `fetchReplay()` (one in flight; latest wins), `S.mode` transitions.

- [ ] **Step 1: Header controls**: `<select id="day">` filled from `/api/days` (default today when `live`, else `latest`); clock shows `S.at`; status chip `LIVE` / `REPLAY`; `Live` button visible only when not following live; theme toggle. Below header, full-width `<input type=range min=0 max=375 step=1>` mapped to 09:15..15:30 with hour tick labels; keyboard ←/→ = 1 min, Shift = 5 min.

- [ ] **Step 2: Behaviour**: live mode pins the slider to the snapshot minute; dragging enters replay of the current day (`/api/replay?day=today&at=`); selecting a past day sets `at=15:30` and fetches; `Live` button restores following. Clock updates immediately on drag; data swaps when the response arrives; stale responses (older `at`) are discarded.

- [ ] **Step 3: Empty states**: `/api/days` empty → one sentence in the main area with the verbatim command `docker compose run --rm ingest python backfill.py`; symbol without candles → `no data for SYMBOL on DATE`.

- [ ] **Step 4: Verify in the browser**: pick 2026-10-16, slider to 15:30 → cards render with `REPLAY` chip; drag to 10:00 → cards change, clock reads 10:00, request count ≤ 1 per slider stop (network panel); Live button appears; press Live → back to today's state. Keyboard arrows move one minute. Phone width (375 px) still usable.

- [ ] **Step 5: Commit** — `git commit -m "web: date selector, time slider, replay mode"`

---

### Task 10: Dashboard — chart timeframes, overlays, crosshair, markers, 1D

**Files:**
- Modify: `web/index.html`

**Interfaces:**
- Consumes: `/api/candles/{sym}?day=` (1-minute + `delta_available`), `/api/candles/{sym}?tf=1d&days=250`, alerts for the day (Task 11 supplies `S.alerts`; until then an empty array).
- Produces: `resampleBrowser(cands, tfMinutes) -> bars` (anchor 09:15, label left, same OHLCV rules as `core.rules.resample`), `drawChart(bars, a, {tf, upto, markers, daily})`.

- [ ] **Step 1: Timeframe row** `1m 3m 5m 15m 30m 60m 1D` in the detail panel, persisted in `localStorage('tf')`.

- [ ] **Step 2: Intraday chart**: bars from `resampleBrowser` of the 1-minute day cut at `S.at` (the forming bar is drawn from the minutes `< at` plus the stub, matching the API semantic); overlays computed from 1-minute data only: VWAP line, R1/R2/S1/S2, previous close, **previous-day high/low** (`ref.prev_high/prev_low`, dashed muted), opening-range band, OB zone, last-price tag, current-minute marker; x-axis fixed 09:15–15:30 for every intraday tf.

- [ ] **Step 3: Crosshair readout**: on pointer move, a vertical hairline and a one-line monospace readout above the canvas: `HH:MM  O H L C  V  VWAP  OI`; `V` shows `—` when the bar's volume is unknown. Touch: tap shows the readout for that bar.

- [ ] **Step 4: Alert markers**: small triangles at the minute of each alert for the selected symbol (bull scans up-triangle below the bar, bear scans down-triangle above; scan name in the readout when hovered).

- [ ] **Step 5: 1D chart**: 250 daily bars, selected day marked with a vertical line, no intraday overlays; kv row `Vol Δ` shows `—` when `delta_available` is false (any mode).

- [ ] **Step 6: Verify in the browser** on 2026-10-16: switch through all seven timeframes; the cards do not change; at 15m with `at=10:00` the last bar covers 09:45–10:00 and is flat-ish (stub) while earlier bars are complete; crosshair readout matches a value checked against `/api/candles/RELIANCE?day=2026-10-16`; 1D shows ~250 bars with the marker.

- [ ] **Step 7: Commit** — `git commit -m "web: chart timeframes, overlays, crosshair, alert markers, daily view"`

---

### Task 11: Dashboard — alert list, README, status

**Files:**
- Modify: `web/index.html`, `README.md` (complete "History and replay" section: three commands, limitations), `CLAUDE.md` (Status + architecture lines for `core/`, backfill, replay endpoints, dashboard)

**Interfaces:**
- Consumes: `/api/alerts?day=`, `/api/alerts?limit=20` (live).
- Produces: `S.alerts` for the selected day; `renderAlerts()`.

- [ ] **Step 1: Left-rail alert list** for the selected day, newest first: `HH:MM · scan · SYMBOL · ₹price`, a small `replay` tag when `source==='replay'`; in live mode new alerts prepend when the socket delivers a snapshot (re-fetch `?limit=20` as today). Clicking a row selects the symbol and sets `at` to that minute (entering replay if live).

- [ ] **Step 2: Footnote** under the sector rail when the selected day is older than the most recent monthly expiry: `OI before the current contract became front month is thin` (one sentence; compute "most recent expiry" as the latest `expiry` in `/api/days`' companion field — add `"expiry": "YYYY-MM-DD"` to `/api/days` from `instruments.expiry`).

- [ ] **Step 3: README section** with exactly these commands and the two limitations (no delta on backfilled days; OI caveat):

```bash
docker compose exec -T postgres psql -U scanner -d scanner -f /migrations/001_history.sql
```
```bash
docker compose run --rm ingest python backfill.py
```
```bash
docker compose run --rm ingest python backfill.py --days 3
```

- [ ] **Step 4: Verify in the browser**: on 2026-10-16 the list shows replay alerts; clicking one moves the slider and chart and highlights that symbol's card; on a day older than the last expiry the footnote appears.

- [ ] **Step 5: Run the full acceptance list from the spec** and record results in the commit message.

- [ ] **Step 6: Commit** — `git commit -m "web: per-day alert list; docs for history and replay"`

---

### Task 12 (Phase 2, GATED): engine onto `core/`

**Do not start until Saurabh confirms one clean live day after Monday 19 Oct 2026.**

**Files:**
- Modify: `engine/main.py` (import `from core.analytics import analyse, score`, `from core.rules import resample, evaluate`, `from core.ranking import rank`; replace lines 51–77 with `snapshot = rank(frames, refs, now)`; alert insert sets `source='live'` explicitly), `engine/Dockerfile` (`COPY engine/ ./` + `COPY core/ ./core/`), `docker-compose.yml` (engine build context `.`)
- Delete: `engine/analytics.py`, `engine/rules.py`

- [ ] **Step 1: Make the change; `diff` must show no arithmetic changes.**
- [ ] **Step 2: Deploy after 15:35 IST**: `ssh scanradar-vps 'cd /opt/scanradar && git pull -q && docker compose up -d --build engine && sleep 5 && docker compose logs --tail 3 engine'` → `engine waiting for candle signals`.
- [ ] **Step 3: Next trading day**: compare `/api/scan` at 10:00 with `/api/replay?day=today&at=10:00` fetched at 15:40: same top-5 bulls and bears by symbol.
- [ ] **Step 4: Commit** — `git commit -m "engine: use core/ analytics and ranking (phase 2)"`
