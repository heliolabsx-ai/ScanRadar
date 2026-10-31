# Trade Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For every pattern trigger and rule alert that carries a stop and a target, compute the Time Machine odds, size the trade in shares, apply Saurabh's day rules, store the row, and show it all on a new Trades tab and in the existing places (Patterns table, drawers, desk context, Telegram).

**Architecture:** Three pure modules in `core/` (replay, sizing, gate) with pytest; one storage/queue module `api/trades.py`; one live loop `api/live_trades.py` that runs every minute in the API process and then hands the minute to the existing desk tick; new routes in `api/main.py`; `web/trades.js` pure helpers plus a Trades view in `web/index.html`. Everything existing stays; the only visible changes to old screens are lots → shares and an odds pair.

**Tech Stack:** Python 3.12, numpy 2.1, pandas 2.2, FastAPI, psycopg 3, Redis; vanilla JS with node tests. Tests: `.venv/bin/python -m pytest tests -q` and `node --test tests/web/`.

**Spec:** `docs/superpowers/specs/2026-10-30-trade-loop-design.md`

## Global Constraints

- All times IST (`Asia/Kolkata`); candle `ts` is the minute **start**; 5-minute bars are anchored at 09:15 and keyed by bar start `HH:MM`.
- Replay constants: `N_PATHS = 4000`, `SEED = 19`, `SESSIONS = 60`, `FLAT_BY = "15:20"`; both levels touched in one bar = stop; paths borrow bars only from sessions strictly before the trigger day.
- Verdict words are exactly `flat`, `coin`, `edge`, `bad`, checked in that order; sentences use the spec's templates and never the word "will" except in the `flat` template given by the spec ("Will not resolve before the close…").
- Settings defaults: `capital 500000`, `max_loss 2000`, `day_loss 6000`, `max_trades 5`, `position_cap 0.6`, `product "intraday"`, `flat_by "15:20"`. Validation: all numbers positive, `day_loss ≥ max_loss`, `product ∈ {intraday, delivery}`, `flat_by` is `HH:MM` between 09:20 and 15:30.
- Refs: `pattern:SYM|kind|HH:MM(formed_at)` (as `live_desk.pattern_ref`) and `alert:ID`; one `trade_rows` row per `(day, ref)`.
- Rules apply from `RULES_FROM = 2026-11-03` (env `TRADES_RULES_FROM`); rows for earlier days get odds and size, `gate NULL`.
- The word "lot" must not appear in any user-facing string after Task 12 (grep `web/` and `api/desk.py`).
- No order placement, no broker code, no change to `ingest/`, the detector, the rule DSL or the ranking.
- Deploy: migration first, then `git pull`; `docker compose up -d --build --no-deps api engine` after 15:30 only. Web helper scripts need a new `?v=` token in `web/index.html` whenever they change.
- Commit after every task; never commit `.env`.

## Review Focus

1. **Stop equal to entry** (a scan whose `trade.stop` resolves to the bar close): sizing returns 0 shares with `limited_by = "zero_distance"`, the replay does not divide by zero (`rr = 0`), the gate says BLOCK "stop equals entry". Pinned in Task 1 and Task 3.
2. **A symbol with no or few past sessions** (new F&O entrant, 0–3 stored days): the row is still written, `odds` is `null` when there are no sessions, the Trades tab reads "no history"; with 3 sessions the odds carry `sessions: 3`. Pinned in Task 2 (`replay` returns `None` on zero sessions) and Task 10 (`oddsPair(null)` → `—`).
3. **A trigger at or after `flat_by`** (a 15:17 alert): `bars_left = 0`, `none_pct = 100`, verdict `flat`; the ledger settles the row immediately at the trigger close. Pinned in Task 2 and Task 3.
4. **Bad settings** (negative capital, `day_loss < max_loss`, `product: "cash"`, `flat_by: "3pm"`): `PUT /api/trades/settings` returns 400 and the stored settings are unchanged. Pinned in Task 1 (`validate`) and Task 7 (route test).
5. **Logging a paper trade twice, or on a BLOCK row:** the second click keeps the first `logged_at`; a BLOCK row can be logged (he took it anyway) and then counts in the ledger. Pinned in Task 4 (`log_paper` idempotent) and Task 3 (ledger counts logged rows regardless of gate).

---

### Task 1: Sizing and settings (`core/sizing.py`)

**Files:**
- Create: `core/sizing.py`
- Test: `tests/test_sizing.py`

**Interfaces:**
- Produces: `DEFAULTS: dict` (the seven settings above); `validate(settings: dict) -> dict` (returns a full settings dict with DEFAULTS filled in, raises `ValueError` with a message naming the field); `shares(entry: float, stop: float, settings: dict, side: str = "long") -> dict` with keys `shares: int`, `risk_inr: float` (shares × distance), `position_inr: float` (shares × entry), `limited_by: str` in `{"max_loss", "position_cap", "zero_distance", "delivery_no_short"}`.

- [ ] **Step 1: Write the failing tests**

```python
from core.sizing import shares, validate, DEFAULTS

def test_shares_by_max_loss():
    s = shares(854.6, 845.9, DEFAULTS)
    assert s == {"shares": 229, "risk_inr": round(229 * 8.7, 2), "position_inr": round(229 * 854.6, 2), "limited_by": "max_loss"}

def test_shares_capped_by_position():
    s = shares(6104.0, 6100.0, DEFAULTS, side="short")          # 500 by max loss, 49 by 60 % of ₹5,00,000
    assert s["shares"] == 49 and s["limited_by"] == "position_cap"

def test_zero_distance_and_delivery_short():
    assert shares(100.0, 100.0, DEFAULTS)["limited_by"] == "zero_distance"
    assert shares(100.0, 100.0, DEFAULTS)["shares"] == 0
    d = shares(100.0, 101.0, {**DEFAULTS, "product": "delivery"}, side="short")
    assert d == {"shares": 0, "risk_inr": 0.0, "position_inr": 0.0, "limited_by": "delivery_no_short"}

def test_validate_fills_defaults_and_rejects_bad_values():
    assert validate({"max_loss": 1500}) == {**DEFAULTS, "max_loss": 1500}
    for bad in ({"capital": -1}, {"day_loss": 1000, "max_loss": 2000}, {"product": "cash"}, {"flat_by": "3pm"}, {"flat_by": "16:00"}, {"max_trades": 0}):
        try:
            validate(bad); assert False, bad
        except ValueError as e:
            assert next(iter(bad)) in str(e)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sizing.py -q`
Expected: FAIL with `ModuleNotFoundError: core.sizing`

- [ ] **Step 3: Implement `DEFAULTS`, `validate`, `shares` in `core/sizing.py`**

`shares = floor(max_loss / dist)` then `min(shares, floor(capital × position_cap / entry))`; `limited_by` names whichever bound was smaller (`max_loss` when equal). Round money to 2 places.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sizing.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/sizing.py tests/test_sizing.py
git commit -m "core: share sizing by max loss and the trade settings schema"
```

---

### Task 2: The Time Machine replay (`core/timemachine.py`)

**Files:**
- Create: `core/timemachine.py`
- Test: `tests/test_timemachine.py`

**Interfaces:**
- Consumes: `tests/conftest.py` `synthetic_day`, `shaped_day`; `core.backtest.outcome`.
- Produces:
  - `N_PATHS = 4000`, `SEED = 19`, `SESSIONS = 60`, `FLAT_BY = "15:20"`.
  - `trigger_bar(ts: pd.Timestamp) -> str` — the 5-minute bar (start `HH:MM`) whose close is the trigger: floor to 5 minutes of `ts − 1 minute`, 09:15 anchor (`10:20` → `"10:15"`, `10:20:05` → `"10:15"`).
  - `bars5(df1m: pd.DataFrame) -> dict[str, tuple[float, float, float, float]]` — `{bar start: (o, h, l, c)}` in time order.
  - `history(frames: dict[str, pd.DataFrame]) -> dict[str, dict]` — `{"YYYY-MM-DD": bars5(df)}` sorted by day; `frames` keys are day strings.
  - `monte_carlo(hist, at: str, side: str, entry: float, stop: float, target: float, *, flat_by=FLAT_BY, n=N_PATHS, seed=SEED) -> dict` with keys `target_pct, stop_pct, none_pct` (one decimal), `avg_r` (2 dp), `close_r: {p10, p50, p90}`, `median_tt_min: int | None`, `paths: int`, `sessions: int`, `bars_left: int`.
  - `past_sessions(hist, at, side, entry, stop, target, *, flat_by=FLAT_BY) -> dict` with keys `target, stop, none: int` and `last: list[[day, result, minutes]]` (the last 12).
  - `verdict(mc: dict, rr: float) -> tuple[str, str]` — `(word, sentence)`.
  - `replay(plan: dict, hist: dict) -> dict | None` where `plan = {symbol, day, at, side, entry, stop, target}` (`day` = `"YYYY-MM-DD"`, `at` = bar start); drops every `hist` day `>= plan["day"]`; returns `None` when no session is left; else `{"mc": …, "past": …, "verdict": word, "sentence": str, "rr": float, "flat_by": str}`.

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd
from conftest import synthetic_day, shaped_day
from core import timemachine as tm
from core.backtest import outcome

def hist_of(n=20, **kw):
    return tm.history({f"2026-09-{d:02d}": synthetic_day(seed=d, day=f"2026-09-{d:02d}", **kw) for d in range(1, n + 1)})

def test_trigger_bar_and_bars5():
    assert tm.trigger_bar(pd.Timestamp("2026-10-28 10:20", tz="Asia/Kolkata")) == "10:15"
    assert tm.trigger_bar(pd.Timestamp("2026-10-28 10:20:05", tz="Asia/Kolkata")) == "10:15"
    assert tm.trigger_bar(pd.Timestamp("2026-10-28 09:20", tz="Asia/Kolkata")) == "09:15"
    b = tm.bars5(synthetic_day())
    assert list(b)[:2] == ["09:15", "09:20"] and len(b) == 75 and len(b["09:15"]) == 4

def test_seeded_and_deterministic():
    h = hist_of()
    a = tm.monte_carlo(h, "10:15", "long", 100.0, 99.0, 102.0)
    b = tm.monte_carlo(h, "10:15", "long", 100.0, 99.0, 102.0)
    assert a == b and a["paths"] == 4000 and a["sessions"] == 20 and a["bars_left"] == 60
    assert abs(a["target_pct"] + a["stop_pct"] + a["none_pct"] - 100) < 0.2

def test_no_future_leak():
    h = hist_of()
    wild = {"2026-10-28": synthetic_day(seed=99, day="2026-10-28", base_price=100.0)}
    wild["2026-10-28"].loc[:, ["high"]] = 1000.0                  # a leak would push target_pct to ~100
    plan = dict(symbol="X", day="2026-10-28", at="10:15", side="long", entry=100.0, stop=99.0, target=102.0)
    assert tm.replay(plan, h) == tm.replay(plan, {**h, **tm.history(wild)})

def test_both_touched_is_stop_and_target_inside_first_bar():
    h = hist_of()
    tight = tm.monte_carlo(h, "10:15", "long", 100.0, 99.999, 100.001)    # both inside almost every bar → stop
    assert tight["stop_pct"] > 97.0
    easy = tm.monte_carlo(h, "10:15", "long", 100.0, 90.0, 100.001)      # target inside the first bar's range, stop far away
    assert easy["target_pct"] > 98.0

def test_after_flat_by_is_flat_and_empty_history_is_none():
    h = hist_of()
    late = tm.monte_carlo(h, "15:15", "long", 100.0, 99.0, 102.0)
    assert late == {**late, "bars_left": 0, "none_pct": 100.0, "target_pct": 0.0, "stop_pct": 0.0}
    assert tm.verdict(late, 2.0)[0] == "flat"
    assert tm.replay(dict(symbol="X", day="2026-08-01", at="10:15", side="long", entry=100, stop=99, target=102), h) is None

def test_verdict_words_and_sentences():
    assert tm.verdict({"none_pct": 75.0, "target_pct": 10.0, "stop_pct": 15.0, "avg_r": 0.1}, 1.0)[0] == "flat"
    w, s = tm.verdict({"none_pct": 19.1, "target_pct": 39.4, "stop_pct": 41.5, "avg_r": -0.05}, 0.84)
    assert w == "coin" and s == "Close to a coin flip: T1 first 39.4 %, stop first 41.5 %, average -0.05 R."
    w, s = tm.verdict({"none_pct": 66.1, "target_pct": 29.8, "stop_pct": 4.1, "avg_r": 0.52}, 0.7)
    assert w == "edge" and s == "Odds favour T1: 29.8 % against 4.1 %, average 0.52 R."
    assert tm.verdict({"none_pct": 10.0, "target_pct": 20.0, "stop_pct": 70.0, "avg_r": -0.6}, 1.0) == ("bad", "Odds favour the stop: 70.0 % against 20.0 %.")
    assert "will" not in s.lower()

def test_past_sessions_agree_with_backtest_outcome():
    df, _ = shaped_day([0, 0.2, 0.4, 0.3, 1.0, 1.8, 2.5, 2.2], day="2026-09-05")   # one clean session: long from the 09:30 bar reaches +2 %
    h = tm.history({"2026-09-05": df})
    at = "09:30"; e = h["2026-09-05"][at][3]
    ps = tm.past_sessions(h, at, "long", e, e * 0.995, e * 1.02)
    assert ps["target"] == 1 and ps["stop"] == 0 and ps["last"][0][0] == "2026-09-05" and ps["last"][0][1] == "target"
    o = outcome(df, pd.Timestamp("2026-09-05 09:34", tz="Asia/Kolkata"), "long", e, e * 0.995, e * 1.02)
    assert o["hit"] == "target"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_timemachine.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `core/timemachine.py`**

Vectorise the Monte Carlo across paths: for each future slot `k` (bar starts `> at` and `< flat_by`), build `ratios[k] = ndarray[(n_k, 3)]` of `h/o, l/o, c/o` from the sessions that have slot `k` (skip a slot nobody has), draw `rng.integers(0, n_k, n)` with `numpy.random.default_rng(seed)`, update the running price vector and resolve unresolved paths by first touch (stop checked before target). `avg_r` = mean of `+rr` for target, `−1` for stop, signed `(exit − entry) / risk` for none; `close_r` percentiles over the same vector; `median_tt_min` = median over target paths of `bars × 5`. `past_sessions` applies the plan's percentage distances to each session's close at `at`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_timemachine.py -q`
Expected: 7 passed, under 10 s total

- [ ] **Step 5: Commit**

```bash
git add core/timemachine.py tests/test_timemachine.py
git commit -m "core: Time Machine replay (Monte Carlo from the stock's own sessions, same plan on past sessions, verdict word)"
```

---

### Task 3: The day gate and the ledger (`core/gate.py`)

**Files:**
- Create: `core/gate.py`
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `core.sizing.DEFAULTS`; `core.backtest.outcome`.
- Produces:
  - `ledger(rows: list[dict], settings: dict) -> dict` — rows are `trade_rows` dicts already cut at the as-of minute (`outcome` present only when settled by then). Keys: `committed` (sum of `size.risk_inr` of logged rows without `outcome`), `realised` (sum of `outcome.pnl_inr` of logged settled rows), `realised_losses` (negative part only, as a positive number), `taken` (count of logged rows), `open: dict[symbol, "HH:MM"]` (logged rows without outcome, value = row `ts` hh:mm), `blocked_total`, `blocked_won` (gate BLOCK rows whose `outcome.hit == "target"`), `gate_state` (`"OPEN"` | `"CLOSED"`, closed when `taken ≥ max_trades` or `committed + realised_losses + settings.max_loss > day_loss`), `room_inr` (`day_loss − committed − realised_losses`, floored at 0).
  - `gate(row: dict, state: dict, settings: dict) -> tuple[str, str]` — `row` has `symbol`, `size`; `state` is a `ledger()` result. Rules in order: taken ≥ max_trades → `("BLOCK", f"{taken} trades today")`; `committed + realised_losses + size.risk_inr > day_loss` → `("BLOCK", f"₹{committed+realised_losses:,.0f} of ₹{day_loss:,.0f} day risk committed")`; `size.shares == 0` → `("BLOCK", {"zero_distance": "stop equals entry", "delivery_no_short": "delivery: no shorts", "position_cap": "position cap leaves no shares", "max_loss": "max loss leaves no shares"}[limited_by])`; symbol in open → `("BLOCK", f"already in {symbol} since {open[symbol]}")`; else `("PASS", "")`.
  - `settle(row: dict, df1m: pd.DataFrame, flat_by: str, final: bool) -> dict | None` — `core.backtest.outcome` on `df1m[df1m.ts < flat_by minute]` from the row's `ts`; returns `None` while the trade is open (no touch and not `final` and the data has not reached `flat_by − 1 min`); else the outcome dict plus `exit_px` (target/stop level, or the last close in the slice), `pnl_inr` = `shares × (exit_px − entry)` signed for the side (2 dp), `settled_at` (ISO; the touch minute, else the last bar's minute + 1). `shares` from `row["size"]["shares"]`, 0 shares → `pnl_inr 0`. A row whose `ts` is at or after `flat_by` settles at once: `hit "none"`, `exit_px = entry`, `pnl_inr 0`, `settled_at = ts`.

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd
from conftest import shaped_day
from core.gate import ledger, gate, settle
from core.sizing import DEFAULTS

def row(sym, ts, risk, shares=100, logged=True, gate_="PASS", outcome=None):
    return {"symbol": sym, "ts": f"2026-10-28T{ts}:00+05:30", "side": "long", "entry": 100.0, "stop": 99.0, "target": 102.0,
            "size": {"shares": shares, "risk_inr": risk, "position_inr": shares * 100.0, "limited_by": "max_loss"},
            "gate": gate_, "gate_reason": "", "paper": {"logged_at": "x"} if logged else None, "outcome": outcome}

def test_day_risk_fills_up_and_blocks():            # the 28 Oct sequence from the mockup: three logged rows leave ₹34 of room
    rows = [row("AMBER", "10:55", 1992.0), row("KFINTECH", "11:05", 1980.0), row("DABUR", "10:30", 1994.0)]
    st = ledger(rows, DEFAULTS)
    assert st["committed"] == 5966.0 and st["taken"] == 3 and st["room_inr"] == 34.0 and st["gate_state"] == "CLOSED"
    assert gate(row("LT", "13:25", 1990.0, logged=False), st, DEFAULTS) == ("BLOCK", "₹5,966 of ₹6,000 day risk committed")

def test_count_cap_same_symbol_and_zero_shares():
    rows = [row(s, t, 500.0) for s, t in (("A", "09:30"), ("B", "09:35"), ("C", "09:40"), ("D", "09:45"), ("E", "09:50"))]
    assert gate(row("F", "10:00", 500.0, logged=False), ledger(rows, DEFAULTS), DEFAULTS) == ("BLOCK", "5 trades today")
    st = ledger([row("LT", "10:55", 500.0)], DEFAULTS)
    assert gate(row("LT", "11:30", 500.0, logged=False), st, DEFAULTS) == ("BLOCK", "already in LT since 10:55")
    z = row("X", "11:00", 0.0, shares=0, logged=False); z["size"]["limited_by"] = "zero_distance"
    assert gate(z, st, DEFAULTS) == ("BLOCK", "stop equals entry")
    assert gate(row("Y", "11:00", 500.0, logged=False), st, DEFAULTS) == ("PASS", "")

def test_settled_rows_move_to_realised_and_losses_count_against_room():
    won = row("A", "09:30", 1000.0, outcome={"hit": "target", "pnl_inr": 2000.0})
    lost = row("B", "09:40", 1000.0, outcome={"hit": "stop", "pnl_inr": -1000.0})
    blocked_won = row("C", "09:50", 1000.0, logged=False, gate_="BLOCK", outcome={"hit": "target", "pnl_inr": 1500.0})
    st = ledger([won, lost, blocked_won, row("D", "10:00", 1000.0)], DEFAULTS)
    assert st["committed"] == 1000.0 and st["realised"] == 1000.0 and st["realised_losses"] == 1000.0
    assert st["blocked_total"] == 1 and st["blocked_won"] == 1 and st["taken"] == 3 and st["open"] == {"D": "10:00"}

def test_logged_block_row_counts():
    st = ledger([row("A", "09:30", 1000.0, gate_="BLOCK")], DEFAULTS)
    assert st["taken"] == 1 and st["committed"] == 1000.0

def test_settle_target_stop_open_and_flat_by():
    df, _ = shaped_day([0, 0.5, 1.0, 1.5, 2.5, 2.0] + [2.0] * 69, day="2026-10-28")          # 75 bars to 15:29
    r = row("X", "09:20", 100.0, shares=10); r["entry"] = 100.0; r["stop"] = 99.0; r["target"] = 102.0
    s = settle(r, df, "15:20", final=False)
    assert s["hit"] == "target" and s["exit_px"] == 102.0 and s["pnl_inr"] == 20.0 and s["settled_at"].endswith("+05:30")
    r["target"] = 110.0
    assert settle(r, df[df.ts < pd.Timestamp("2026-10-28 12:00", tz="Asia/Kolkata")], "15:20", final=False) is None
    s = settle(r, df, "15:20", final=False)                      # the data reaches 15:19: flat at the last close before 15:20
    assert s["hit"] == "none" and s["settled_at"][11:16] == "15:20" and s["pnl_inr"] == round(10 * (s["exit_px"] - 100.0), 2)
    assert settle(r, df[df.ts < pd.Timestamp("2026-10-28 15:14", tz="Asia/Kolkata")], "15:20", final=True)["hit"] == "none"
    late = dict(r, ts="2026-10-28T15:25:00+05:30")
    assert settle(late, df, "15:20", final=False)["hit"] == "none"   # a trigger after flat_by settles at once
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gate.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `core/gate.py`**

`ledger` is one pass over the rows. `settle` builds the slice, calls `outcome`, finds the touch minute with the same loop as `core.backtest.time_to_target_min` (first bar touching the level that decided).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gate.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add core/gate.py tests/test_gate.py
git commit -m "core: day ledger, gate rules and trade settlement at flat_by"
```

---

### Task 4: Storage, queue and the row pipeline (`db/migrations/005_trades.sql`, `api/trades.py`)

**Files:**
- Create: `db/migrations/005_trades.sql`; `api/trades.py`
- Modify: `db/init.sql` (append the mirror after the `003_patterns_facts` block)
- Test: `tests/test_trades.py`

**Interfaces:**
- Consumes: `core.sizing.{validate, shares, DEFAULTS}`, `core.timemachine.{replay, history, trigger_bar}`, `core.gate.{ledger, gate}`, `api/patterns.py` `stored_rows`, `rows_at`, `confidence`, `stats`; `api/alerts.py` `alerts_for_day`, `scan_files`, `merge_live_replay`; `api/live_desk.py` `pattern_ref`, `gate` (score/rr), `MIN_CONF`; `api/replay.py` `session_bounds`, `load_refs`; `desk.ALERT_MIN_RR`, `desk.MAX_AGE_MIN`.
- Produces (`api/trades.py`; the module does `from core import timemachine as tm` and `from core.gate import ledger, gate, settle`, so tests reach them as `trades.tm` and `trades.ledger`):
  - `RULES_FROM: dt.date` (env `TRADES_RULES_FROM`, default `2026-11-03`); `REPEAT_MIN = 60`.
  - `get_settings(con) -> dict`; `put_settings(con, body: dict) -> dict` (validate → upsert `trade_settings('current')`; `ValueError` propagates).
  - `candidates(con, day, at: dt.datetime, *, include_replay: bool, max_age: int | None, pat_stats: dict, scans: dict) -> list[dict]` — pattern rows (`rows_at(stored, at)`, state `triggered`/`retest`-with-trigger ≠ `failed`, `live_desk.gate(score, rr)`, confidence hit rate ≥ `MIN_CONF` or no record, `triggered_at ≤ at`, not older than `max_age` when given) and alerts (`alerts_for_day` → `merge_live_replay`, `ts ≤ at`, scan without `"desk": false`, `stop` and `target` present, R:R ≥ `desk.ALERT_MIN_RR`, repeat rule: skip when another alert of the same `(scan, symbol)` earlier that day is within `REPEAT_MIN` minutes). Each candidate: `{ref, source: "pattern"|"alert", symbol, side, ts (minute, IST), bar: trigger_bar(ts), entry, stop, target (T1 for patterns, falling back to target when facts.target1 is missing), target2, rr, score, label, kind_or_scan}`. Sorted by `(ts, -rr, -score, symbol)`.
  - `load_history(con, symbol: str, day: dt.date, sessions: int = 60) -> dict` — `history()` of the symbol's stored 1-minute candles for the `sessions` stored days before `day` (one query, `ts >= day − 120 calendar days AND ts < day start`); memoised per `(symbol, day)` in a module dict capped at 300 entries.
  - `build_row(cand: dict, settings: dict, state: dict, hist: dict, *, apply_rules: bool) -> dict` — odds via `replay`, size via `shares(side=cand.side)`, then `gate` when `apply_rules` else `gate=None, gate_reason=None`; returns the full `trade_rows` record (`odds` may be `None`).
  - `insert_row(cur, row: dict) -> int | None` — `INSERT … ON CONFLICT (day, ref) DO NOTHING RETURNING id`.
  - `rows_for_day(con, day, upto: dt.datetime | None = None) -> list[dict]` — JSON-safe rows (`ts`, `settled_at` ISO IST), ordered by `ts, id`; when `upto` is given, rows with `ts > upto` are dropped and an `outcome` whose `settled_at > upto` is replaced by `None`.
  - `odds_by_ref(con, day) -> dict[str, dict]` — `ref → {id, odds, size, gate, gate_reason}`.
  - `log_paper(con, row_id: int, note: str) -> dict | None` — sets `paper = {logged_at: now ISO, note}` only when `paper IS NULL`; returns the row.
  - `update_outcome(cur, row_id: int, outcome: dict) -> None`.
  - `settle_open(con, day, at: dt.datetime, flat_by: str, final: bool) -> int` — for rows without `outcome`, one query for their symbols' candles (`symbol = ANY(%s)`, `ts < at`), `core.gate.settle`, write; returns the count settled.
  - `backfill_day(con, day, settings) -> int` — `candidates(include_replay=True, max_age=None)` → `build_row(apply_rules = day >= RULES_FROM)` → insert; `settle_open(final=True)`; returns rows inserted.

- [ ] **Step 1: Write the migration and mirror it**

`db/migrations/005_trades.sql` with the `trade_rows` and `trade_settings` DDL from the spec, idempotent (`IF NOT EXISTS`), header comment with the apply command. Append the same block to `db/init.sql` under `-- 005_trades`.

- [ ] **Step 2: Write the failing tests**

```python
import datetime as dt
import trades
from core.sizing import DEFAULTS
from conftest import synthetic_day
from test_api_alerts import FakeCon          # recording cursor

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T = lambda hhmm: dt.datetime(2026, 10, 28, int(hhmm[:2]), int(hhmm[3:]), tzinfo=IST)

def test_candidates_apply_the_desk_filters_and_the_repeat_rule(monkeypatch):
    pat = [{"symbol": "LT", "kind": "triple_top", "side": "short", "state": "triggered", "score": 6, "rr": 0.9, "entry": 3793.7, "stop": 3833.0, "target": 3700.0,
            "facts": {"target1": 3759.2}, "formed_at": T("13:00"), "triggered_at": T("13:25")},
           {"symbol": "X", "kind": "double_top", "side": "short", "state": "failed", "score": 6, "rr": 2.0, "entry": 100, "stop": 101, "target": 98, "facts": {}, "formed_at": T("10:00"), "triggered_at": T("10:20")}]
    als = [{"ts": T("10:20").isoformat(), "scan": "B0101", "symbol": "KFINTECH", "price": 854.6, "stop": 845.9, "target": 872.0, "side": "long", "source": "live", "id": 7},
           {"ts": T("10:40").isoformat(), "scan": "B0101", "symbol": "KFINTECH", "price": 856.0, "stop": 846.0, "target": 876.0, "side": "long", "source": "live", "id": 8},   # repeat within 60 min
           {"ts": T("10:30").isoformat(), "scan": "vwap_reclaim_long", "symbol": "Y", "price": 100, "stop": 99, "target": 101, "side": "long", "source": "live", "id": 9}]    # R:R 1.0 < 1.5
    monkeypatch.setattr(trades, "_pattern_rows_at", lambda con, day, at, include_replay: pat)
    monkeypatch.setattr(trades, "_alert_rows", lambda con, day: als)
    out = trades.candidates(None, dt.date(2026, 10, 28), T("14:00"), include_replay=False, max_age=None, pat_stats={}, scans={})
    assert [(c["ref"], c["bar"], c["target"]) for c in out] == [("alert:7", "10:15", 872.0), ("pattern:LT|triple_top|13:00", "13:20", 3759.2)]
    assert out[0]["source"] == "alert" and out[1]["target2"] == 3700.0
    assert trades.candidates(None, dt.date(2026, 10, 28), T("14:00"), include_replay=False, max_age=10, pat_stats={}, scans={}) == []

def test_build_row_sizes_gates_and_keeps_odds_optional():
    hist = trades.tm.history({f"2026-09-{d:02d}": synthetic_day(seed=d, day=f"2026-09-{d:02d}") for d in range(1, 6)})
    cand = {"ref": "alert:7", "source": "alert", "symbol": "KFINTECH", "side": "long", "ts": T("10:20"), "bar": "10:15", "entry": 100.0, "stop": 99.0, "target": 102.0, "target2": None, "rr": 2.0, "score": None, "label": "B0101"}
    state = trades.ledger([], DEFAULTS)
    r = trades.build_row(cand, DEFAULTS, state, hist, apply_rules=True)
    assert r["size"]["shares"] == 2000 // 1 and r["gate"] == "PASS" and r["odds"]["mc"]["sessions"] == 5 and r["odds"]["verdict"] in ("flat", "coin", "edge", "bad")
    assert r["day"] == dt.date(2026, 10, 28) and r["ts"] == T("10:20") and r["rr"] == 2.0
    assert trades.build_row(cand, DEFAULTS, state, {}, apply_rules=False)["odds"] is None
    assert trades.build_row(cand, DEFAULTS, state, hist, apply_rules=False)["gate"] is None

def test_insert_and_paper_sql_shapes():
    con = FakeCon()
    trades.insert_row(con.cur, {"day": dt.date(2026, 10, 28), "ts": T("10:20"), "ref": "alert:7", "symbol": "K", "side": "long", "source": "alert",
                                "entry": 1, "stop": 0.5, "target": 2, "rr": 2.0, "odds": None, "size": {"shares": 1}, "gate": "PASS", "gate_reason": ""})
    assert "ON CONFLICT (day, ref) DO NOTHING RETURNING id" in con.cur.executed[-1]
    trades.log_paper(con, 5, "took it")
    assert "paper IS NULL" in con.cur.executed[-1]

def test_settings_roundtrip_validates():
    con = FakeCon()
    assert trades.get_settings(con) == DEFAULTS                       # empty table → defaults
    try:
        trades.put_settings(con, {"product": "cash"}); assert False
    except ValueError:
        pass
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_trades.py -q`
Expected: FAIL with `ModuleNotFoundError: trades`

- [ ] **Step 4: Implement `api/trades.py`**

Keep two thin seams the test monkeypatches: `_pattern_rows_at(con, day, at, include_replay)` (`pt.rows_at` over `pt.stored_rows` filtered by source, with `confidence` attached from `pat_stats`) and `_alert_rows(con, day)` (`al.alerts_for_day` plus the alert `id`; extend `alerts_for_day` to return `id` in its dicts, which the dashboard ignores). `get_settings` on an empty `fetchone()` returns `DEFAULTS`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_trades.py tests/test_api_alerts.py -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add db/migrations/005_trades.sql db/init.sql api/trades.py api/alerts.py tests/test_trades.py
git commit -m "trades: migration 005, settings, trigger queue and the odds/size/gate row pipeline"
```

---

### Task 5: Calibration script (`scripts/backtest_odds.py`)

**Files:**
- Create: `scripts/backtest_odds.py`
- Test: `tests/test_backtest_odds_cli.py`

**Interfaces:**
- Consumes: `trades.candidates`, `trades.load_history`, `trades.build_row`, `trades.backfill_day`, `core.backtest.outcome`, `api/replay.py` `load_day`.
- Produces: `calibration(rows: list[dict]) -> dict` — `rows` have `odds.mc.target_pct`, `odds.verdict`, `outcome.hit`; returns `{"deciles": [{"bucket": "30-40", "n": int, "predicted": float, "realised": float}], "by_verdict": {word: {"n", "target_pct", "stop_pct", "none_pct"}}}` where realised = share of rows with `hit == "target"` among decided rows. `fmt_note(cal, days, n_rows) -> str` (markdown). CLI: `--days 42 --note PATH --write` (persist rows via `backfill_day`), `--no-write` default.

- [ ] **Step 1: Write the failing test**

```python
from backtest_odds import calibration, fmt_note

def test_calibration_buckets_and_verdicts():
    rows = [{"odds": {"mc": {"target_pct": 35.0}, "verdict": "coin"}, "outcome": {"hit": "target"}},
            {"odds": {"mc": {"target_pct": 38.0}, "verdict": "coin"}, "outcome": {"hit": "stop"}},
            {"odds": {"mc": {"target_pct": 80.0}, "verdict": "edge"}, "outcome": {"hit": "none"}},
            {"odds": None, "outcome": {"hit": "target"}}]
    c = calibration(rows)
    d = {b["bucket"]: b for b in c["deciles"]}
    assert d["30-40"]["n"] == 2 and d["30-40"]["predicted"] == 36.5 and d["30-40"]["realised"] == 50.0
    assert c["by_verdict"]["coin"]["n"] == 2 and c["by_verdict"]["edge"]["none_pct"] == 100.0
    assert "| 30-40 | 2 | 36.5 | 50.0 |" in fmt_note(c, 42, 4)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_backtest_odds_cli.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the script** (same `sys.path` prologue as `scripts/backtest_patterns.py`)

`main`: list the last `--days` stored days older than today, for each day run `candidates(include_replay=True, max_age=None)` → `build_row(apply_rules=False)` with the day's `settle` outcomes (`core.gate.settle(final=True)` on `load_day` frames); with `--write` call `backfill_day` instead and read the rows back. Print `fmt_note` and write it to `--note`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_backtest_odds_cli.py -q`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/backtest_odds.py tests/test_backtest_odds_cli.py
git commit -m "scripts: backtest_odds calibration of the Time Machine against graded outcomes"
```

---

### Task 6: The trades loop (`api/live_trades.py`) and deferred Telegram

**Files:**
- Create: `api/live_trades.py`
- Modify: `api/live_patterns.py:46-121` (`message`, `tick`), `engine/main.py:122-141` (alert insert and Telegram), `api/main.py:38-42` (startup)
- Test: `tests/test_live_trades.py`, `tests/test_live_patterns.py` (extend)

**Interfaces:**
- Consumes: `trades.*` (Task 4), `core.gate.ledger`, `live_desk.tick`, `live_desk.should_run` pattern, `desk.enabled`.
- Produces:
  - `live_trades.should_run(now) -> bool` — trading day and 09:20 ≤ time ≤ 15:30.
  - `live_trades.odds_line(row: dict | None) -> str` — `"T1 39 % · stop 42 % · coin flip · 229 sh · PASS"`, `"… · BLOCK: 5 trades today"`, percentages rounded half-up with `int(x + 0.5)`; verdict words rendered `flat` → `cannot resolve by close`, `coin` → `coin flip`, `edge` → `odds favour T1`, `bad` → `odds favour stop`; `odds None` → `"no history · 229 sh · PASS"`; row None → `""`.
  - `live_trades.process_minute(con, R, day, at, settings) -> dict` — `candidates(include_replay=False, max_age=desk.MAX_AGE_MIN)`, skip refs already in `odds_by_ref`, `state = ledger(rows_for_day(con, day, at), settings)` recomputed after each insert, `build_row(apply_rules = day >= RULES_FROM)`, insert, `R.set(f"trades:{day}:{ref}", json.dumps(row-odds-size-gate), ex=86400)`; then `settle_open(final = at.time() >= flat_by)`; returns `{"rows": n_new, "settled": n}`; publishes `trades:updated` `{"at": "HH:MM", "new": n}` when either is non-zero.
  - `live_trades.flush_deferred(R, day, send, at) -> int` — `lpop`s the Redis list `tg:defer:{day}` until empty (`{"ref", "text", "ts"}` items); if `trades:{day}:{ref}` exists the message is `text + "\n" + odds_line(row)`; else if the item is younger than 90 s it is pushed back; else sent as is; items older than 30 min are dropped with a log line. Returns the number sent.
  - `live_trades.run(connect, send, prepare) -> coroutine` — the one minute loop of the API process from now on: every minute at ~:20 s, in `run_in_executor(None, …)`: `tick` (`process_minute` when `should_run`, then `flush_deferred` always), then `live_desk.tick(connect, now, send, prepare)` when `desk.enabled()` and `live_desk.should_run(now)`. `live_desk.run` is no longer started by `main.py` (keep the function; its sleep line moves here).
  - `live_patterns.tick`: for a fresh trigger worth telling, `R.rpush(f"tg:defer:{day}", json.dumps({"ref": live_desk.pattern_ref(r), "text": message(r), "ts": now.isoformat()}))` instead of `send(...)`; `R.expire(…, 86400)`. `message(r)` unchanged.
  - `engine/main.py`: `INSERT INTO alerts(...) VALUES(...) RETURNING id`; the Telegram text for a fired scan goes to `R.rpush(f"tg:defer:{day}", …)` with `ref = f"alert:{id}"` instead of `telegram(...)` (the engine keeps `telegram` for other messages). When `TRADES_LIVE=0` (env, default `1`) the engine sends directly as before.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_live_trades.py
import json, datetime as dt
import live_trades as lt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

def test_should_run_window():
    assert lt.should_run(dt.datetime(2026, 10, 28, 9, 20, tzinfo=IST)) and lt.should_run(dt.datetime(2026, 10, 28, 15, 30, tzinfo=IST))
    assert not lt.should_run(dt.datetime(2026, 10, 28, 15, 31, tzinfo=IST)) and not lt.should_run(dt.datetime(2026, 10, 31, 10, 0, tzinfo=IST))   # Saturday

def test_odds_line():
    row = {"odds": {"mc": {"target_pct": 39.4, "stop_pct": 41.5}, "verdict": "coin"}, "size": {"shares": 229}, "gate": "PASS", "gate_reason": ""}
    assert lt.odds_line(row) == "T1 39 % · stop 42 % · coin flip · 229 sh · PASS"
    assert lt.odds_line({**row, "gate": "BLOCK", "gate_reason": "5 trades today"}) == "T1 39 % · stop 42 % · coin flip · 229 sh · BLOCK: 5 trades today"
    assert lt.odds_line({**row, "odds": None}) == "no history · 229 sh · PASS"
    assert lt.odds_line({**row, "gate": None}) == "T1 39 % · stop 42 % · coin flip · 229 sh"
    assert lt.odds_line(None) == ""

class FakeR:
    def __init__(self, items, rows): self.items = list(items); self.rows = rows; self.pushed = []
    def lpop(self, k): return self.items.pop(0) if self.items else None
    def rpush(self, k, v): self.pushed.append(v)
    def expire(self, *a): pass
    def get(self, k): return self.rows.get(k)

def test_flush_deferred_appends_the_line_or_waits_or_gives_up():
    now = dt.datetime(2026, 10, 28, 10, 20, 25, tzinfo=IST)
    item = lambda ref, age_s: json.dumps({"ref": ref, "text": "<b>x</b>", "ts": (now - dt.timedelta(seconds=age_s)).isoformat()})
    R = FakeR([item("alert:7", 10), item("alert:8", 10), item("alert:9", 120), item("alert:10", 1900)],
              {"trades:2026-10-28:alert:7": json.dumps({"odds": None, "size": {"shares": 3}, "gate": "PASS", "gate_reason": ""})})
    sent = []
    assert lt.flush_deferred(R, dt.date(2026, 10, 28), sent.append, now) == 2
    assert sent == ["<b>x</b>\nno history · 3 sh · PASS", "<b>x</b>"]          # 7 with the line, 9 too old to wait, 10 dropped
    assert json.loads(R.pushed[0])["ref"] == "alert:8"                             # 8 waits for the next minute
```

Extend `tests/test_live_patterns.py` with a test that `tick`'s send path pushes to the deferred list: refactor the send inside `tick` into `defer(R, day, r, now)` and assert its `rpush` payload has `ref == "pattern:TATASTEEL|double_bottom|10:00"` and `text == message(r)`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_live_trades.py tests/test_live_patterns.py -q`
Expected: FAIL with `ModuleNotFoundError: live_trades` / `AttributeError: defer`

- [ ] **Step 3: Implement `api/live_trades.py`, `live_patterns.defer`, the engine change, and switch `main.py` startup to `lt.run(connect, telegram, _prepare_day)`**

Settings are read once per minute via `trades.get_settings(con)`. Log one line per minute with new rows.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add api/live_trades.py api/live_patterns.py api/main.py engine/main.py tests/test_live_trades.py tests/test_live_patterns.py
git commit -m "trades loop: odds, size and gate per trigger each minute; Telegram trigger lines carry the odds via a deferred queue"
```

---

### Task 7: Routes and the day backfill (`api/main.py`)

**Files:**
- Modify: `api/main.py` (`_prepare_day` 125-157, `/api/alerts` 251-274, `/api/patterns` 278-323, websocket 550-572; new routes after the desk block)
- Test: `tests/test_api_trades.py`

**Interfaces:**
- Consumes: `trades.*`, `core.gate.ledger`, `rp.parse_replay_args`, `rp.day_is_settled`, `_kick_prepare`.
- Produces:
  - `GET /api/trades?day&at` → `{day, at, pending, applied: bool, rules_from: "2026-11-03", settings, ledger, rows}`; rows = `rows_for_day(con, d, at_dt)`; `ledger = ledger(rows, settings)`; on a settled day with no rows → `pending: true` and `_kick_prepare(d)`.
  - `GET /api/trades/settings` → settings; `PUT /api/trades/settings` (JSON body) → saved settings, `400` with the `ValueError` message on bad input.
  - `POST /api/trades/{id}/paper` body `{note}` → the row; `404` when the id is unknown; publishes `trades:updated`.
  - `/api/patterns` rows gain `odds` (the stored odds dict or `null`) and `trade` (`{id, shares, risk_inr, gate, gate_reason}` or `null`) joined by `live_desk.pattern_ref(row)`; `/api/alerts?day=` rows likewise by `f"alert:{id}"`.
  - `_prepare_day`: after the patterns block, `trades.backfill_day(con, day, trades.get_settings(con))` when `rows_for_day` is empty.
  - Websocket: subscribe `trades:updated` → `{"type": "trades", "data": …}`.
  - Pure helper for the test: `trades_view(rows, settings, day, now) -> dict` in `api/trades.py` (the response body minus `pending`).

- [ ] **Step 1: Write the failing test**

```python
import datetime as dt
import trades
from core.sizing import DEFAULTS

def test_trades_view_marks_rules_not_applied_before_go_live():
    v = trades.trades_view([], DEFAULTS, dt.date(2026, 10, 28), "15:30")
    assert v["applied"] is False and v["rules_from"] == "2026-11-03" and v["ledger"]["taken"] == 0
    assert trades.trades_view([], DEFAULTS, dt.date(2026, 11, 3), "10:00")["applied"] is True

def test_attach_trade_fields():
    by_ref = {"pattern:LT|triple_top|13:00": {"id": 4, "odds": {"verdict": "flat"}, "size": {"shares": 0, "risk_inr": 0}, "gate": "BLOCK", "gate_reason": "x"}}
    rows = trades.attach(by_ref, [{"symbol": "LT", "kind": "triple_top", "formed_at": "2026-10-28T13:00:00+05:30"}, {"symbol": "A", "kind": "flag", "formed_at": "2026-10-28T09:30:00+05:30"}], "pattern")
    assert rows[0]["odds"] == {"verdict": "flat"} and rows[0]["trade"] == {"id": 4, "shares": 0, "risk_inr": 0, "gate": "BLOCK", "gate_reason": "x"}
    assert rows[1]["odds"] is None and rows[1]["trade"] is None
    assert trades.attach({"alert:7": by_ref["pattern:LT|triple_top|13:00"]}, [{"id": 7, "scan": "B0101"}], "alert")[0]["trade"]["id"] == 4
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api_trades.py -q`
Expected: FAIL with `AttributeError: trades_view`

- [ ] **Step 3: Implement `trades_view`, `attach(by_ref, rows, kind)` in `api/trades.py` and the routes, joins, backfill and websocket channel in `api/main.py`**

- [ ] **Step 4: Run the tests and boot the API against the local stack**

Run: `.venv/bin/python -m pytest tests -q` → all passed.
Then, with the SSH tunnel from the dashboard dev loop (`ssh -L 18000:127.0.0.1:8000 scanradar-vps`, API not yet redeployed), only check that `python -c "import main"` succeeds in `api/` with `DATABASE_URL`/`REDIS_URL` set to dummies: `cd api && DATABASE_URL=postgresql://x REDIS_URL=redis://localhost:1 ../.venv/bin/python -c "import main"`.
Expected: no traceback.

- [ ] **Step 5: Commit**

```bash
git add api/main.py api/trades.py tests/test_api_trades.py
git commit -m "api: /api/trades, settings and paper routes; odds joined onto patterns and alerts; settled-day backfill"
```

---

### Task 8: The desk sees odds, gate and shares (`api/desk.py`, `api/live_desk.py`)

**Files:**
- Modify: `api/desk.py:89-99` (HOUSE), `:101-118` (VERDICT_SYSTEM), `:182-204` (`size_for`, `verdict_context`), `:246-256` (`telegram_text`), `:418-477` (`pnl_for`, `review_context`); `api/live_desk.py:63-66` (`lots_map` stays), `:92-104` (`_verdict`), `:121-203` (`process_minute`), `:238-259` (`run_review`)
- Test: `tests/test_desk.py` (update the lot assertions)

**Interfaces:**
- Consumes: `core.sizing.shares`, `trades.odds_by_ref`, `trades.get_settings`.
- Produces:
  - `desk.verdict_context(trigger, *, snap, sector_pct, bars, ref, setup_stats, now_hhmm, settings, trade=None) -> dict` — `ticket.size` = `trade["size"]` when given else `shares(entry, stop, settings, side)`; new top-level `odds` (`trade["odds"]` or `None`) and `gate` (`{"state": trade["gate"], "reason": trade["gate_reason"]}` or `None`). `lot` parameter removed; `size_for` deleted.
  - `HOUSE`: "sizes in lots" → "sizes in shares"; "lot size or the rupee risk budget is never a reason" → "share count or the rupee risk budget is never a reason".
  - `VERDICT_SYSTEM` gains, after the ticket sentence: `The odds block, when present, is a replay of this stock's own past sessions from this minute (T1 first / stop first / neither by the close) and the same plan on each past session: argue with it, cite it, never obey it. A gate of BLOCK means the trader's day rules are spent: the call cannot be take; say wait or skip and name the gate reason.`
  - `telegram_text`: `Size 229 sh` from `size["shares"]`; a line `Odds: T1 39 % · stop 42 % · coin flip` when `odds` present and `Gate: BLOCK — reason` when the gate blocks.
  - `pnl_for(outcome, side, entry, stop, target, qty)` unchanged (qty = shares).
  - `review_context(day, *, alerts, patterns, verdicts, settings, candles, spend, budget)` — P&L at `shares(entry, stop, settings, side)["shares"]`; `pnl_basis` = `f"shares sized by max loss ₹{max_loss:,.0f} per trade, before costs; target = full move, stop = full risk, open = 60-min return"`; `lots` parameter removed.
  - `live_desk.process_minute`: `trows = trades.odds_by_ref(con, day)`, `settings = trades.get_settings(con)`; `_verdict(..., trade=trows.get(ref), settings=settings)`; `lots` no longer passed. `run_review` passes `settings`.

- [ ] **Step 1: Update the tests first**

In `tests/test_desk.py` replace every `size_for`/`lot` assertion: `verdict_context(..., settings=DEFAULTS, trade=None)["ticket"]["size"]["shares"] == 229` for entry 854.6 / stop 845.9; with `trade={"odds": {"verdict": "coin"}, "size": {"shares": 5}, "gate": "BLOCK", "gate_reason": "5 trades today"}` assert `ctx["gate"] == {"state": "BLOCK", "reason": "5 trades today"}` and `ctx["ticket"]["size"]["shares"] == 5`; `telegram_text` contains `Size 5 sh` and `Gate: BLOCK — 5 trades today`; `review_context(..., settings=DEFAULTS)["pnl_basis"].startswith("shares sized by max loss ₹2,000")`; `"lot" not in desk.HOUSE and "lot" not in desk.VERDICT_SYSTEM`; `"never obey it" in desk.VERDICT_SYSTEM`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_desk.py -q`
Expected: FAIL on the new assertions

- [ ] **Step 3: Implement the changes**

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all passed; `grep -n "lot" api/desk.py` shows no user-facing string (comments allowed).

- [ ] **Step 5: Commit**

```bash
git add api/desk.py api/live_desk.py tests/test_desk.py
git commit -m "desk: shares not lots; the verdict sees the odds and the gate and is told how to treat them"
```

---

### Task 9: Dashboard helpers (`web/trades.js`)

**Files:**
- Create: `web/trades.js`
- Test: `tests/web/test_trades.mjs`

**Interfaces:**
- Produces `root.ScanTrades = { size, tiles, orderRows, oddsPair, oddsBar, verdictWord, verdictClass, gateLabel, ticket, filterRows, inr }`:
  - `size(entry, stop, settings, side='long') -> {shares, risk_inr, position_inr, limited_by}` — same arithmetic as `core.sizing.shares` (test values identical to Task 1).
  - `inr(n) -> "₹1,95,703"` (en-IN grouping, no decimals; `null` → `—`).
  - `tiles(ledger, settings, applied, rulesFrom = '2026-11-03') -> [{key, label, value, sub, cls}]` six tiles in order `capital`, `max_loss`, `day_risk` (`value "₹5,966 / ₹6,000"`, `sub` the meter percent as `pct`), `trades` (`value "3 / 5"`, `sub "gate OPEN"`/`"gate CLOSED"`), `pnl` (`value` realised as `inr`, `cls` `up`/`dn`), `blocked_won` (`value "1 of 4"`, `sub "the gate's honest cost"`); when `applied` is false the `trades` tile `sub` is `"rules not applied (before 3 Nov 2026)"` formatted from `rulesFrom`.
  - `orderRows(rows, mode)` — `mode 'live'` newest first by `ts` then `id` desc; `'replay'` time order.
  - `oddsPair(odds) -> "39/42"` (rounded ints, `null` → `"—"`); `oddsBar(odds) -> {target, stop, none}` percentages summing to 100 (`null` → all 0).
  - `verdictWord(odds)` → `cannot resolve by close` / `coin flip` / `odds favour T1` / `odds favour stop` / `no history`; `verdictClass(odds)` → `flat|coin|edge|bad|none`.
  - `gateLabel(row) -> {text: "PASS"|"BLOCK"|"—", cls: "pass"|"block"|"off", reason}`.
  - `ticket(row, settings) -> string` — `"BUY 229 KFINTECH @ 854.60 · stop 845.90 · T1 861.90 · T2 872.00 · risk ₹1,992 · 1 : 0.8"` (`T2` only when `target2`), followed by ` · BLOCKED: reason` when blocked.
  - `filterRows(rows, filter, sectorsOf)` — applies `ScanFilter.pass` to `{symbol, price: entry, sector, rvol: null, rr}` the way `passAlert` does for alerts (sector and price band apply; RVOL/OI/VWAP/picks checks are skipped for trade rows).

- [ ] **Step 1: Write the failing tests** (same loader as `tests/web/test_desk.mjs`; load `filter.js` first)

```js
test('size matches the server arithmetic', () => {
  assert.deepEqual(T.size(854.6, 845.9, S), { shares: 229, risk_inr: 1992.3, position_inr: 195703.4, limited_by: 'max_loss' });
  assert.equal(T.size(6104, 6100, S, 'short').limited_by, 'position_cap');
  assert.equal(T.size(100, 100, S).limited_by, 'zero_distance');
  assert.equal(T.size(100, 101, { ...S, product: 'delivery' }, 'short').limited_by, 'delivery_no_short');
});
test('tiles', () => {
  const t = T.tiles({ committed: 5966, realised: -1200, realised_losses: 1200, taken: 3, gate_state: 'CLOSED', blocked_total: 4, blocked_won: 1, room_inr: 34 }, S, true);
  assert.deepEqual(t.map(x => x.key), ['capital', 'max_loss', 'day_risk', 'trades', 'pnl', 'blocked_won']);
  assert.equal(t[2].value, '₹5,966 / ₹6,000'); assert.equal(t[3].value, '3 / 5'); assert.equal(t[3].sub, 'gate CLOSED'); assert.equal(t[4].cls, 'dn'); assert.equal(t[5].value, '1 of 4');
  assert.equal(T.tiles({ taken: 0 }, S, false, '2026-11-03')[3].sub, 'rules not applied (before 3 Nov 2026)');
});
test('odds, verdict, gate, ticket, order', () => {
  const odds = { mc: { target_pct: 39.4, stop_pct: 41.5, none_pct: 19.1 }, verdict: 'coin', sentence: 's' };
  assert.equal(T.oddsPair(odds), '39/42'); assert.equal(T.oddsPair(null), '—');
  assert.deepEqual(T.oddsBar(odds), { target: 39.4, stop: 41.5, none: 19.1 });
  assert.equal(T.verdictWord(odds), 'coin flip'); assert.equal(T.verdictWord(null), 'no history'); assert.equal(T.verdictClass(null), 'none');
  const row = { id: 2, symbol: 'KFINTECH', side: 'long', entry: 854.6, stop: 845.9, target: 861.9, target2: 872, rr: 0.84, size: { shares: 229, risk_inr: 1992.3 }, gate: 'BLOCK', gate_reason: '5 trades today', ts: '2026-10-28T11:05:00+05:30' };
  assert.equal(T.ticket(row, S), 'BUY 229 KFINTECH @ 854.60 · stop 845.90 · T1 861.90 · T2 872.00 · risk ₹1,992 · 1 : 0.8 · BLOCKED: 5 trades today');
  assert.deepEqual(T.gateLabel(row), { text: 'BLOCK', cls: 'block', reason: '5 trades today' }); assert.equal(T.gateLabel({ gate: null }).text, '—');
  const a = { ...row, id: 1, ts: '2026-10-28T10:55:00+05:30' };
  assert.deepEqual(T.orderRows([a, row], 'live').map(r => r.id), [2, 1]); assert.deepEqual(T.orderRows([row, a], 'replay').map(r => r.id), [1, 2]);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/web/test_trades.mjs`
Expected: fail (file missing)

- [ ] **Step 3: Implement `web/trades.js`** in the IIFE style of `web/desk.js`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --test tests/web/`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add web/trades.js tests/web/test_trades.mjs
git commit -m "web: Trades tab helpers (sizing, tiles, odds, gate, ticket)"
```

---

### Task 10: The Trades tab (`web/index.html`)

**Files:**
- Modify: `web/index.html` — script tags (line 7-11), tabs (189), header risk input (194), state (271, 287-288), `setView`/hash/`panelEl` (720-730), `renderAll` (559-564), websocket handler (1083), boot (1097); new `#tradesView` markup after `#watchView`; new functions `loadTrades`, `renderTrades`, `renderTradeDrawer`, `renderTradeSettings`, `saveTradeSettings`, `logPaper`.

**Interfaces:**
- Consumes: `ScanTrades` (Task 9), `GET /api/trades`, `PUT /api/trades/settings`, `POST /api/trades/{id}/paper`, `wireChart(a)` / `panelEl()`, `ScanFilter`, `S.at`/`S.day`/`S.mode`, the Desk tab's ask input.
- Produces: `S.trows`, `S.tledger`, `S.tsettings` (loaded at boot from `/api/trades/settings`; `S.risk = S.tsettings.max_loss`), `S.tsel`; `panelEl()` returns `$('tdrawer')` when `S.view === 'trades'`; `setView('trades')` and `#trades` hash; `renderAll` and the websocket `trades` message call `loadTrades()`.

- [ ] **Step 1: Markup and wiring**

Add `<button class="tab" data-view="trades">Trades</button>` after Watch. Replace the Risk label with `<button class="chip" id="riskChip" title="Max loss per trade; change it on the Trades tab">₹2,000 / trade</button>` whose click is `setView('trades'); openTradeSettings()`. `#tradesView`: a `.ttiles` strip (six `.kpi` tiles, `capital` and `max_loss` editable in place on click, a gear button opening `#tsettings` form with the seven fields and Save/Cancel), a `<table class="pt" id="trows">` with columns Time · Stock · Setup · Entry (stop/T1 under) · Odds (mini-bar + word) · Shares (position under) · Risk ₹ · Gate · Outcome, `#tempty`, and a drawer `#tdrawer` (same `.wdrawer` styling as Watch) with: the chart (`.tfrow` + `<canvas class="chart">`, levels from the row), the full odds bar, the past-sessions counts and the last-12 squares, the verdict sentence, the ticket text in `<pre>`, a note input, buttons **Log as paper trade** and **Ask the desk**, no other buttons. Blocked rows get class `blocked` (grey).

- [ ] **Step 2: Behaviour**

`loadTrades()` fetches `/api/trades?day=${S.day||todayIST()}&at=${S.at||'15:30'}`, stores, `renderTrades()`; while `pending`, retry every 10 s like `loadPatterns`. `renderTrades()` builds the tiles via `ScanTrades.tiles`, rows via `orderRows(filterRows(...), S.mode)`; the strip reads the rules-not-applied text when `applied` is false. Row click → `S.tsel`, `renderTradeDrawer()` → `wireChart({symbol, price: entry, stop, target, ...})` with the row's levels drawn like the pattern detail does. **Log as paper trade** → `POST /api/trades/{id}/paper` then `loadTrades()`. **Ask the desk** → `setView('desk')`, put `ScanTrades.ticket(row) + ' — take, wait or skip?'` into the ask input. Settings save → `PUT`, on 400 show the message under the form; on success `S.tsettings` updates, `riskChip` text updates, detail panels re-render.

- [ ] **Step 3: Cache-bust and verify in the browser**

Add `<script src="trades.js?v=<short git hash>"></script>`. Serve `web/` with the dev loop (static server proxying `/api` to the tunnel) and open `#trades` on a stored day: tiles show, rows list, drawer opens with a chart, settings form validates (try `day_loss 1000` → error text). `node --test tests/web/` still green.

- [ ] **Step 4: Commit**

```bash
git add web/index.html web/trades.js
git commit -m "web: Trades tab (settings, ledger tiles, sized and gated queue, drawer with ticket and paper log)"
```

---

### Task 11: Odds and shares everywhere else

**Files:**
- Modify: `web/index.html:363-368` (scanner sizing), `:801`, `:819` (pattern detail qty), `:777-796` (patterns table + `<th>Odds</th>` in the thead at line 232), `:1042` (Watch add-box hint), `:1010-1036` (Watch drawer size block and pattern chips); `web/desk.js:32-38` (`ticket`), `web/watch.js:31-40` (`events` chip text), `web/notify.js:31-40` (`alertMessage`, add `patternMessage(r)` if absent)
- Test: `tests/web/test_desk.mjs`, `tests/web/test_watch.mjs`, `tests/web/test_notify.mjs` (update)

**Interfaces:**
- Consumes: `ScanTrades.size`, `ScanTrades.oddsPair`, `ScanTrades.verdictClass`, `S.tsettings`, row `odds` / `trade` fields from `/api/patterns` and `/api/alerts`.
- Produces: `sizing(a)` in `index.html` returns `<dt>Size</dt><dd>229 sh · ₹1,95,703</dd><dt>Loss at stop</dt><dd class="dn">₹1,992</dd><dt>Reward</dt><dd class="up">…</dd>` or `<dd class="mut">stop equals entry</dd>` from `limited_by`; pattern detail `Qty` line → `Shares @ ₹2,000` with the same text; Patterns table new `<td class="mono odds {cls}" title="{sentence}">39/42</td>` after Conf; pattern detail panel: an odds bar div under the chart when `r.odds`; `ScanDesk.ticket(v, maxLoss)` → `size: '229 sh · ₹1,95,703'` (from `size.shares`, `size.position_inr`), `riskLabel: 'Size @₹2,000'` from `maxLoss`; new `ScanDesk.oddsLine(v)` → `'odds 39/42 · coin flip · gate BLOCK: 5 trades today'` from `v.context.odds` / `v.context.gate` (empty string when the context has neither), rendered as one line on each verdict card in `index.html`; Watch chip text gains ` · 39/42` when the pattern row has odds; `ScanNotify.alertMessage(a)` and the pattern notification include ` · odds 39/42` when present; the Watch add-box hint drops `lot …`.

- [ ] **Step 1: Update the node tests** — in `test_desk.mjs` the ticket expects `'229 sh · ₹1,95,703'` for `size: {shares: 229, position_inr: 195703.4, risk_inr: 1992.3}` and `'0 sh · stop equals entry'` for `{shares: 0, limited_by: 'zero_distance'}`, and `D.oddsLine` returns `'odds 39/42 · coin flip · gate BLOCK: 5 trades today'` for a context with `odds: {mc: {target_pct: 39.4, stop_pct: 41.5}, verdict: 'coin'}` and `gate: {state: 'BLOCK', reason: '5 trades today'}` and `''` without them; in `test_watch.mjs` an event for a pattern row with `odds` ends with `' · 39/42'`; in `test_notify.mjs` `alertMessage({..., odds})` contains `'odds 39/42'`.

- [ ] **Step 2: Run them to verify they fail**

Run: `node --test tests/web/`

- [ ] **Step 3: Implement**, then bump `?v=` on `desk.js`, `watch.js`, `notify.js` and `trades.js` in `index.html`.

- [ ] **Step 4: Verify** — `node --test tests/web/` green; `grep -n -i "lot" web/index.html web/desk.js web/watch.js web/notify.js web/trades.js` returns nothing user-facing (the `S.lots` map and `lot_size` field may stay as data); in the browser the scanner detail panel, pattern detail and Watch drawer show shares; the Patterns table has the Odds column.

- [ ] **Step 5: Commit**

```bash
git add web/
git commit -m "web: shares everywhere, odds column and bars, odds in chips and notifications, header risk chip"
```

---

### Task 12: Calibration run, deploy, live check

**Files:**
- Create: `docs/superpowers/notes/odds-backtest-2026-11-01.md` (written by the script)
- Modify: `CLAUDE.md` (a `### core/timemachine.py, core/sizing.py, core/gate.py, api/trades.py` paragraph and the Trades tab line under `web/`, dated), `README.md` (migration 005 in the deploy steps)

- [ ] **Step 1: Migration and calibration on the VPS** (Saurabh's terminal or `ssh scanradar-vps`; outside market hours)

```bash
cd /opt/scanradar && git pull && docker compose exec -T postgres psql -U scanner -d scanner -f /migrations/005_trades.sql
```

```bash
cd /opt/scanradar && docker compose run --rm --no-deps -T api python scripts/backtest_odds.py --days 42 --write --note /app/notes/odds-backtest-2026-11-01.md
```

Expected: a calibration table printed; rows written for the stored days with `gate NULL`. Commit the note.

- [ ] **Step 2: Deploy the API and engine after 15:30**

```bash
cd /opt/scanradar && docker compose up -d --build --no-deps api engine && docker compose logs --tail 20 api
```

Expected: `trades loop up` and `desk loop` lines in the API log; `curl -s -u … https://scanradar.tech/api/trades/settings` returns the defaults; `/api/trades?day=2026-10-28&at=15:30` returns rows with odds and `applied: false`.

- [ ] **Step 3: First live day (Mon 3 Nov 2026)** — at 09:25 check `docker compose logs --since 10m api | grep trades`; one Telegram trigger message should end with the odds line; the Trades tab shows PASS/BLOCK on today's rows. Record anything broken in the end-of-day review.

- [ ] **Step 4: Docs and commit**

```bash
git add CLAUDE.md README.md docs/superpowers/notes/odds-backtest-2026-11-01.md
git commit -m "docs: trade loop (Time Machine odds, share sizing, day gate, Trades tab) and the first calibration note"
```

---

## Plan-level calls (not in the spec, settled here)

- **No Redis hash for the ledger.** The ledger is recomputed from `trade_rows` on every minute and request (≤ 80 rows); Redis holds only `trades:{day}:{ref}` for the Telegram line and the pub/sub channel. The spec allowed this ("the Redis hash is a cache of the table, never the source").
- **Deferred Telegram instead of a 20 s wait in the engine.** The pattern loop and the engine push their trigger messages to `tg:defer:{day}`; the trades loop sends them with the odds line appended once the row exists (or without it after 90 s). Both pattern and alert messages get the line; the engine never blocks.
- **One loop task.** `live_trades.run` owns the minute and calls `live_desk.tick` afterwards, so the desk always sees the row and the trades loop runs even when the desk is off.
- **`side` on `shares()`** so delivery product can refuse shorts; `settings` carries `product`.
- **Grading covers all rows**, not only logged ones, so BLOCK rows can be counted as "blocked that won" and the queue shows outcomes.
