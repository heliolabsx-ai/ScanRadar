# PD Fade Alarms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Four rule scans (PDH break, PDH fade, PDL break, PDL fade) that fade a break of yesterday's high or low, with a silent heads-up at the break and a loud, sized, gated alarm at the first 5-minute close back through the line.

**Architecture:** Three small additions to the scan language (`hours` window, `min_rr` floor, `telegram: "silent"`) plus a `story` block the engine fills for the Telegram line; the four scans are JSON in `scans/` and ride the existing engine → deferred Telegram → trade loop → Trades tab path unchanged. The dashboard learns to show break/fade alerts as Watch chips and chart triangles.

**Tech Stack:** Python 3.12 (pandas, psycopg, Redis), FastAPI API process, vanilla JS dashboard with node tests.

**Spec:** `docs/superpowers/specs/2026-11-01-pd-fade-alarms-design.md`

## Global Constraints

- Scan names exactly `PDH break`, `PDH fade`, `PDL break`, `PDL fade` (the dashboard keys chips and markers on them).
- Fade rules (DSL `off`: 0 = forming bar, 1 = last closed bar): PDH fade = `[=1] close < prev_day_high`, `[=2] close >= prev_day_high`, `day_high > prev_day_high × 1.003`, `[=0] close > 1000`; PDL fade = `[=1] close > prev_day_low`, `[=2] close <= prev_day_low`, `day_low < prev_day_low × 0.99`, `[=0] close > 1000`. Stop = `day_high` / `day_low`, target = `prev_day_close`. `cooldown_min` 400 (one per stock per day). `min_rr` 0. Telegram on, desk on. Fade scans are NOT continuous.
- Break scans: PDH break = `[=1] close > prev_day_high`, `[=2] close <= prev_day_high`; PDL break mirrored. `"continuous": true`, `cooldown_min` 400, no `trade` block, `"desk": false`, `"alert": {"telegram": "silent"}`.
- PDL break and PDL fade carry `"hours": {"from": "09:20", "to": "11:00"}`; PDH scans have no window. `from` inclusive, `to` exclusive, compared on the fire minute (bar close) in IST.
- A scan without a `trade` block is never graded at pivot levels on replay (`ensure_replay_alerts`, `ensure_outcomes`).
- Silent Telegram = `disable_notification: true` on the Telegram send; the message still arrives.
- All alert times are the minute the bar closed (the fire minute), everywhere.
- Deploy outside market hours: scans ship on `git pull`; `docker compose up -d --build --no-deps api engine`; ingest untouched. Bump `?v=` tokens for every helper `.js` changed.
- No order placement. No user-facing "lot". Odds text stays as it is (replays, never forecasts).
- Commit after every task; commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. **Missing or zero reference levels** (a new listing with `prev_high` 0 or no `daily_ref` row): no fade or break may fire. Pinned in Task 5 (`test_no_fire_without_reference_levels`).
2. **The first bars of the day**: a non-continuous fade scan has no bar 2 at 09:20 and must not fire; a continuous break scan sees yesterday's last bar and fires on a gap open at the 09:15 bar's close. Pinned in Task 5.
3. **A second cross the same day**: the cooldown of 400 minutes means one fade and one break per stock per day, even when the stock crosses the line three times. Pinned in Task 5 (`test_one_fade_per_day`).
4. **The silent flag must survive the deferred queue**: a break alert pushed to `tg:defer` is sent with `silent=True` by `flush_deferred` and by the engine's direct fallback; a fade is sent loud. Pinned in Task 3.
5. **A malformed `hours` block** (`to` before `from`, or `"11am"`): the scan must fail loudly at load, never fire all day. Pinned in Task 1 (`test_hours_rejects_malformed`).

---

### Task 1: Time window and R:R floor in the scan language

**Files:**
- Modify: `core/rules.py` (docstring lines 1-20; new functions after `continuous` at line 43)
- Modify: `core/backtest.py:53-108` (`fires`)
- Modify: `engine/main.py:130-139` (`run_once` scan loop)
- Test: `tests/test_rules_hours.py`

**Interfaces:**
- Produces: `rules.in_hours(scan: dict, ts) -> bool` — `ts` is a tz-aware IST datetime or `pd.Timestamp`; True when the scan has no `hours` block or `from <= ts.time() < to`; raises `ValueError` naming the scan when the block is malformed (missing key, not `HH:MM`, `to <= from`). `rules.min_rr(scan: dict, default: float) -> float` — the scan's `min_rr` when present (0 allowed), else `default`. `fires()` skips minutes outside the window. The engine skips a scan for the whole `run_once` when `now` is outside its window.

- [ ] **Step 1: Write the failing tests**

```python
import datetime as dt, pytest, pandas as pd
from core.rules import in_hours, min_rr
from core.backtest import fires
from conftest import shaped_day

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
W = {"name": "w", "hours": {"from": "09:20", "to": "11:00"}}

def test_in_hours_inclusive_from_exclusive_to():
    assert in_hours(W, dt.datetime(2026, 10, 30, 9, 20, tzinfo=IST))
    assert in_hours(W, dt.datetime(2026, 10, 30, 10, 59, tzinfo=IST))
    assert not in_hours(W, dt.datetime(2026, 10, 30, 11, 0, tzinfo=IST))
    assert in_hours({"name": "n"}, dt.datetime(2026, 10, 30, 15, 0, tzinfo=IST))
    assert in_hours(W, pd.Timestamp("2026-10-30 10:30", tz="Asia/Kolkata"))

def test_hours_rejects_malformed():
    for bad in ({"from": "11:00", "to": "09:20"}, {"from": "9am", "to": "11:00"}, {"to": "11:00"}):
        with pytest.raises(ValueError, match="w"):
            in_hours({"name": "w", "hours": bad}, dt.datetime(2026, 10, 30, 10, 0, tzinfo=IST))

def test_min_rr_default_and_zero():
    assert min_rr({"name": "a"}, 1.5) == 1.5
    assert min_rr({"name": "a", "min_rr": 0}, 1.5) == 0.0

def test_fires_respects_the_window():
    df, _ = shaped_day([0] * 75)                                   # flat day: a rule that is always true
    scan = {"name": "always", "timeframe": "5m", "rules": [{"lhs": {"number": 1}, "op": ">", "rhs": {"number": 0}}], "hours": {"from": "10:00", "to": "10:10"}}
    out = fires(scan, df, {}, cooldown_min=1)
    assert [t.strftime("%H:%M") for t in out][:3] == ["10:00", "10:01", "10:02"] and all("10:00" <= t.strftime("%H:%M") < "10:10" for t in out)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_rules_hours.py -q`
Expected: FAIL with `ImportError: cannot import name 'in_hours'`

- [ ] **Step 3: Implement `in_hours` and `min_rr` in `core/rules.py`, the skip in `fires()` (`if not in_hours(scan, ts.iloc[i]): continue`, placed before the cooldown/evaluate line, after the forming-bar fold is still applied — so fold first, then skip), and `if not in_hours(sc, now): continue` at the top of the engine's `for sc in scans` loop; document `hours` and `min_rr` in the module docstring**

- [ ] **Step 4: Run the tests to verify they pass, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_rules_hours.py -q` → 4 passed; `.venv/bin/python -m pytest tests -q` → all passed.

- [ ] **Step 5: Commit**

```bash
git add core/rules.py core/backtest.py engine/main.py tests/test_rules_hours.py
git commit -m "rules: scan-level hours window and min_rr floor"
```

---

### Task 2: The per-scan R:R floor reaches the trade queue and the desk

**Files:**
- Modify: `api/trades.py:122-124` (`_alert_candidates`), `api/live_desk.py:206-207` (`process_minute`)
- Test: `tests/test_trades.py`, `tests/test_desk.py`

**Interfaces:**
- Consumes: `core.rules.min_rr(scan, default)` from Task 1; `desk.ALERT_MIN_RR`.
- Produces: an alert from a scan with `"min_rr": 0` enters the queue and gets a verdict at any R:R > 0; scans without `min_rr` keep the 1.5 floor; the desk's skip message reads `skipped: R:R 1:0.9 below 1.5` with the floor that applied.

- [ ] **Step 1: Write the failing tests**

In `tests/test_trades.py`, extend `test_candidates_apply_the_desk_filters_and_the_repeat_rule` (or add `test_scan_min_rr_admits_low_rr_alerts`): with `scans={"PDL fade": {"side": "long", "min_rr": 0}}` an alert `price 100, stop 99, target 100.3` (R:R 0.3) yields a candidate with `rr == 0.3`; with `scans={}` the same alert yields none.

In `tests/test_desk.py` add `test_process_minute_uses_the_scan_min_rr` using the existing monkeypatched `process_minute` fixtures: an alert of a `min_rr: 0` scan at R:R 0.3 reaches `_verdict`; without `min_rr` it is skipped with the message containing `below 1.5`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_trades.py tests/test_desk.py -q`
Expected: the two new tests FAIL (candidate missing / verdict not called).

- [ ] **Step 3: Implement**: both sites compute `floor = rules.min_rr(sc, desk.ALERT_MIN_RR)` and compare `rr < floor`; the desk's skip text uses `floor`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_trades.py tests/test_desk.py -q` → all passed.

- [ ] **Step 5: Commit**

```bash
git add api/trades.py api/live_desk.py tests/test_trades.py tests/test_desk.py
git commit -m "trades, desk: honour a scan's own min_rr floor"
```

---

### Task 3: Silent Telegram end to end

**Files:**
- Modify: `core/notify.py`, `engine/notify.py` (`telegram`), `engine/main.py:21-30` (`defer`) and `:151-161` (the Telegram block), `api/live_trades.py` (`flush_deferred`, the two `send(...)` calls), `api/live_patterns.py:60-64` (`defer`, unchanged behaviour but the item gains `"silent": False`)
- Test: `tests/test_live_trades.py`

**Interfaces:**
- Produces: `telegram(text: str, silent: bool = False)` in both notify modules (`disable_notification: silent` in the JSON body). Engine `defer(ref, text, now, silent=False)` writes `"silent"` into the `tg:defer` item and passes `silent` to the direct fallback. `flush_deferred` calls `send(text, silent=True)` when `item.get("silent")` else `send(text)` (so existing one-argument fakes keep working). Engine: `tg = sc.get("alert", {}).get("telegram", True)`; a falsy value sends nothing; `"silent"` sets `silent=True`.

- [ ] **Step 1: Write the failing test**

```python
def test_flush_deferred_passes_silent_through():
    now = dt.datetime(2026, 10, 30, 10, 20, 25, tzinfo=IST)
    items = [json.dumps({"ref": "alert:1", "text": "<b>PDH break</b>", "ts": (now - dt.timedelta(seconds=100)).isoformat(), "silent": True}),
             json.dumps({"ref": "alert:2", "text": "<b>PDH fade</b>", "ts": (now - dt.timedelta(seconds=100)).isoformat()})]
    R = FakeR(items, {})
    calls = []
    def send(text, **kw): calls.append((text, kw.get("silent", False)))
    assert lt.flush_deferred(R, dt.date(2026, 10, 30), send, now) == 2
    assert calls == [("<b>PDH break</b>", True), ("<b>PDH fade</b>", False)]
```

Also a test in `tests/test_live_trades.py` (or a new `tests/test_notify.py` using `monkeypatch` on `requests.post`) that `core.notify.telegram("x", silent=True)` posts `disable_notification: True` and `telegram("x")` posts `False`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_live_trades.py tests/test_notify.py -q`
Expected: FAIL (`silent` ignored / unexpected keyword).

- [ ] **Step 3: Implement** in the files listed; the engine's text block becomes `tg = sc.get("alert", {}).get("telegram", True); if tg: ... defer(f"alert:{aid}", text, now, silent=(tg == "silent"))` and the `TRADES_LIVE=0` path `telegram(text, silent=(tg == "silent"))`.

- [ ] **Step 4: Run the tests to verify they pass, then the whole suite**

Run: `.venv/bin/python -m pytest tests -q` → all passed.

- [ ] **Step 5: Commit**

```bash
git add core/notify.py engine/notify.py engine/main.py api/live_trades.py api/live_patterns.py tests/test_live_trades.py tests/test_notify.py
git commit -m "telegram: silent sends (disable_notification) carried through the deferred queue"
```

---

### Task 4: The story block (break time, run high, run %) for the Telegram line

**Files:**
- Modify: `core/rules.py` (new `story(scan, bars, ctx) -> dict | None` after `trade_levels`), `engine/main.py:142-157` (payload + text), `api/alerts.py:69-83` (`_trade_payload` adds `story` to the replay payload when the scan has one)
- Test: `tests/test_rules_story.py`

**Interfaces:**
- Consumes: `bars` as the engine builds them (`join_sessions(prev, resample(df, tf))`, forming bar last, `attrs["session_start"]`).
- Produces: scan field `"story": {"line": "prev_day_high" | "prev_day_low", "template": "..."}`. `rules.story(scan, bars, ctx) -> dict | None` returns `{"line": float, "break_at": "HH:MM", "run_px": float, "run_pct": float}` where `break_at` is the close minute (bar `ts` + timeframe) of the first bar TODAY whose close is through the line in the scan's direction (short → above `prev_day_high`, long → below `prev_day_low`), `run_px` the extreme (max high / min low) from that bar to the bar before the last row (the last row is the forming bar in the engine; a caller passing only closed bars gets the same rule), `run_pct` = signed distance of `run_px` from the line in percent (1 dp); `None` when the scan has no `story` or no bar today is through the line. The engine formats `template.format(symbol=..., at=now_hhmm, **story)` as the second Telegram line and stores `payload["story"]`; the replay payload stores the same dict so the dashboard can show it.
- Templates (exact copy, in the scan files of Task 5): PDH fade `"{symbol} broke above PDH {line:,.2f} at {break_at}, ran to {run_px:,.2f} ({run_pct:+.1f} %), first 5m close back below at {at}."`; PDL fade `"{symbol} broke below PDL {line:,.2f} at {break_at}, fell to {run_px:,.2f} ({run_pct:+.1f} %), first 5m close back above at {at}."`; PDH break `"{symbol} above PDH {line:,.2f} at {at} · watching for the fade"`; PDL break `"{symbol} below PDL {line:,.2f} at {at} · watching for the fade"`.

- [ ] **Step 1: Write the failing tests**

```python
from core.rules import story, resample
from conftest import shaped_day
import pandas as pd

def test_story_finds_the_break_and_the_run():
    df, _ = shaped_day([0, 0.1, 0.4, 0.9, 0.6, -0.2], base=100.0)     # PDH 100.2: bar 2 (09:25) closes above, run high in bar 3, bar 5 closes back below
    bars = resample(df, "5m"); bars.attrs["session_start"] = 0
    sc = {"name": "PDH fade", "side": "short", "story": {"line": "prev_day_high", "template": "x"}}
    s = story(sc, bars, {"prev_day_high": 100.2})
    assert s["line"] == 100.2 and s["break_at"] == "09:30" and s["run_px"] == pytest.approx(float(bars.high.iloc[2:5].max())) and s["run_pct"] > 0

def test_story_none_without_a_break_or_block():
    df, _ = shaped_day([0, -0.1, -0.2], base=100.0)
    bars = resample(df, "5m"); bars.attrs["session_start"] = 0
    assert story({"name": "n", "side": "short"}, bars, {"prev_day_high": 100.2}) is None
    assert story({"name": "n", "side": "short", "story": {"line": "prev_day_high", "template": "x"}}, bars, {"prev_day_high": 100.2}) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_rules_story.py -q` → `ImportError: story`.

- [ ] **Step 3: Implement `story` in `core/rules.py`; wire the engine (`payload["story"] = st` and the Telegram text when `sc.get("story")`: `f"<b>{name} · {'SELL' if side=='short' else 'BUY'}</b>\n{line}{lvl}\n{now:%H:%M}  ·  scanradar.tech"` where `line` is the formatted template; otherwise the existing text) and `_trade_payload` (`if scan and scan.get("story"): out["story"] = story(scan, bars, ctx)` for both the scan-levels and no-trade paths).**

- [ ] **Step 4: Run the tests, then the suite**

Run: `.venv/bin/python -m pytest tests/test_rules_story.py tests -q` → all passed.

- [ ] **Step 5: Commit**

```bash
git add core/rules.py engine/main.py api/alerts.py tests/test_rules_story.py
git commit -m "scans: story block (break minute, run extreme) for the Telegram line and the alert payload"
```

---

### Task 5: The four scans, no pivot grading for break scans, backtester R figures

**Files:**
- Create: `scans/PDH_break.json`, `scans/PDH_fade.json`, `scans/PDL_break.json`, `scans/PDL_fade.json`
- Modify: `api/alerts.py:106-142` (`ensure_replay_alerts`: no `_trade_payload` for a scan without `trade`), `:145-167` (`ensure_outcomes`: the pivot fallback only when `scan.get("trade")`), `scripts/backtest_scan.py:60-80` (print median R:R and average R)
- Test: `tests/test_scans_pd_fade.py`

**Interfaces:**
- Consumes: `fires`, `trade_levels`, `in_hours`, `story` from Tasks 1 and 4; `alerts.scan_files()` to load the JSON.
- Produces: the four scan files with exactly the Global Constraints' rules, `"note"` carrying the backtest record from the spec's table, `"story"` templates from Task 4; backtester lines `median R:R 1:x.xx` and `avg R per trade ±x.xx` (target = +R:R, stop = −1, open = 60-min move in R).

- [ ] **Step 1: Write the failing tests** (load the scans from `scans/` by name through `alerts.scan_files()` with `SCAN_DIR` pointed at the repo's `scans/`)

```python
def test_pdh_fade_fires_once_at_the_first_close_back_below():
    # PDH 5010 (+0.2 %): closes 0, 0.1 (below), 0.9 (above: run to ~+0.9 %), 0.1 (back below -> fire at 09:35), 0.6 (above again), 0.0 (below again: no second fire)
    df, _ = shaped_day([0, 0.1, 0.9, 0.1, 0.6, 0.0] + [0.0] * 20, base=5000.0)
    ctx = {"prev_day_high": 5010.0, "prev_day_low": 4900.0, "prev_day_close": 4950.0}
    out = fires(SCANS["PDH fade"], df, ctx, 400)
    assert [t.strftime("%H:%M") for t in out] == ["09:35"]
    bars = resample(df[df.ts < out[0]], "5m"); bars.attrs["session_start"] = 0
    lv = trade_levels(SCANS["PDH fade"], bars, ctx, entry=float(df[df.ts < out[0]].close.iloc[-1]))
    assert lv["stop"] == float(bars.high.max()) and lv["target"] == 4950.0 and lv["basis"] == "scan"

def test_pdl_fade_needs_a_one_percent_flush_and_the_morning():
    df, _ = shaped_day([0, -0.5, -1.3, 0.1] + [0.1] * 30, base=5000.0)         # flush to -1.3 % below PDL 5000, then a close back above
    ctx = {"prev_day_high": 5100.0, "prev_day_low": 5000.0, "prev_day_close": 5040.0}
    assert [t.strftime("%H:%M") for t in fires(SCANS["PDL fade"], df, ctx, 400)] == ["09:35"]
    late = df.copy(); late["ts"] = late.ts + pd.Timedelta(hours=2)                  # same shape from 11:15: outside the window
    assert fires(SCANS["PDL fade"], late, ctx, 400) == []
    shallow, _ = shaped_day([0, -0.5, -0.8, 0.1] + [0.1] * 30, base=5000.0)        # only 0.8 % below: no fire
    assert fires(SCANS["PDL fade"], shallow, ctx, 400) == []

def test_break_scans_fire_on_a_gap_open_with_yesterdays_bar_and_carry_no_trade():
    prev, _ = shaped_day([0] * 75, base=100.0, day="2026-10-29")                  # yesterday closed at 100 (PDH 100.2)
    df, _ = shaped_day([1.0, 1.1, 1.2], base=100.0)                               # opens 1 % above PDH
    ctx = {"prev_day_high": 100.2, "prev_day_low": 99.0, "prev_day_close": 100.0}
    out = fires(SCANS["PDH break"], df, ctx, 400, prev1m=prev)
    assert [t.strftime("%H:%M") for t in out] == ["09:20"]
    assert "trade" not in SCANS["PDH break"] and SCANS["PDH break"]["desk"] is False and SCANS["PDH break"]["alert"]["telegram"] == "silent"
    assert fires(SCANS["PDH fade"], df, ctx, 400) == []                           # non-continuous fade: nothing to fade yet

def test_no_fire_without_reference_levels():
    df, _ = shaped_day([0, 0.5, 0.1, 0.6, 0.0], base=5000.0)
    zero = {"prev_day_high": 0, "prev_day_low": 0, "prev_day_close": 0}
    for name in SCANS:
        assert fires(SCANS[name], df, zero, 400) == []

def test_one_fade_per_day():
    df, _ = shaped_day([0, 0.9, 0.1, 0.9, 0.0, 0.9, 0.1] + [0.0] * 60, base=5000.0)
    ctx = {"prev_day_high": 5010.0, "prev_day_low": 4900.0, "prev_day_close": 4950.0}
    assert len(fires(SCANS["PDH fade"], df, ctx, 400)) == 1
```

Plus a test for the no-pivot-grading rule: `ensure_outcomes` on a `FakeCon` whose cursor yields one PDH break alert (no stop/target) must issue an UPDATE with `"outcome": null` and never call `_trade_payload` (monkeypatch it to raise).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scans_pd_fade.py -q` → FAIL (`KeyError: 'PDH fade'`).

- [ ] **Step 3: Write the four scan files and the two `alerts.py` guards; add the R:R and avg-R lines to the backtester (`rr = |target − price| / |price − stop|` per graded row; avg R over graded rows with the trade-loop convention)**

- [ ] **Step 4: Run the tests, then the suite**

Run: `.venv/bin/python -m pytest tests/test_scans_pd_fade.py tests -q` → all passed.

- [ ] **Step 5: Commit**

```bash
git add scans/PDH_break.json scans/PDH_fade.json scans/PDL_break.json scans/PDL_fade.json api/alerts.py scripts/backtest_scan.py tests/test_scans_pd_fade.py
git commit -m "scans: PDH/PDL break notes and fade alarms; no pivot grading without a trade block; backtester R figures"
```

---

### Task 6: Watch chips, chart triangles, quiet notifications

**Files:**
- Modify: `web/watch.js:31-42` (`events(symbol, prows, verdicts, alerts)`), `web/notify.js:10-18` (`newAlerts` skips break scans), `web/index.html:581-590` (chart markers by scan family), `:1079` (pass `S.alerts` to `events`), `:783-787` (notifications), script `?v=` tokens
- Test: `tests/web/test_watch.mjs`, `tests/web/test_notify.mjs`

**Interfaces:**
- Produces: `ScanWatch.alertKind(scan: string) -> 'brk' | 'fade' | null` (`/^PD[HL] break$/` → `brk`, `/^PD[HL] fade$/` → `fade`); `events(symbol, prows, verdicts, alerts)` adds chips `{k: 'brk', t: 'ABOVE PDH 15:05'}` / `'BELOW PDL 09:40'` for break alerts (muted class `brk`) and `{k: 'fade', t: 'PDH FADE · SELL 15:15 · 61/29'}` for fades (odds pair when present; `side` from the alert); a fade chip replaces the same stock's break chip; chips sort newest first with the existing cap of 3. `ScanNotify.newAlerts` never returns break-scan alerts. Chart: an alert whose scan is `brk` draws a grey triangle, `fade` a red (short) or green (long) one, other scans as today.

- [ ] **Step 1: Write the failing tests**

```js
test('alert chips for PD break and fade', () => {
  const als = [{ scan: 'PDH break', symbol: 'X', side: 'short', ts: '2026-10-30T15:05:00+05:30' },
               { scan: 'PDH fade', symbol: 'X', side: 'short', ts: '2026-10-30T15:15:00+05:30', odds: { mc: { target_pct: 61.2, stop_pct: 28.9 }, verdict: 'edge' } },
               { scan: 'B0101', symbol: 'X', side: 'long', ts: '2026-10-30T10:00:00+05:30' }];
  assert.equal(W.alertKind('PDL break'), 'brk'); assert.equal(W.alertKind('PDH fade'), 'fade'); assert.equal(W.alertKind('B0101'), null);
  const ev = W.events('X', [], [], als);
  assert.deepEqual(ev.map(e => e.k + '|' + e.t), ['fade|PDH FADE · SELL 15:15 · 61/29']);     // the fade replaces the break; B0101 is not a chip
  assert.deepEqual(W.events('X', [], [], als.slice(0, 1)).map(e => e.t), ['ABOVE PDH 15:05']);
});
test('break alerts never notify', () => {
  const seen = new Set();
  const out = N.newAlerts(seen, [{ scan: 'PDH break', symbol: 'X', ts: '2026-10-30T15:05:00+05:30' }, { scan: 'PDH fade', symbol: 'X', ts: '2026-10-30T15:15:00+05:30' }]);
  assert.deepEqual(out.map(a => a.scan), ['PDH fade']);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/web/test_watch.mjs tests/web/test_notify.mjs` → the two new tests fail.

- [ ] **Step 3: Implement**, then bump `?v=` for `watch.js` and `notify.js` in `index.html` and extend the chart marker loop (triangle colour/size by `ScanWatch.alertKind(al.scan)`). Run the inline-script syntax check.

- [ ] **Step 4: Run the node suites**

Run: `node --test tests/web/test_desk.mjs tests/web/test_filter.mjs tests/web/test_notify.mjs tests/web/test_patterns.mjs tests/web/test_watch.mjs tests/web/test_trades.mjs` → all pass.

- [ ] **Step 5: Commit**

```bash
git add web/watch.js web/notify.js web/index.html tests/web/test_watch.mjs tests/web/test_notify.mjs
git commit -m "web: PD break/fade chips on Watch cards, chart triangles, no notification for the quiet break note"
```

---

### Task 7: Backtest, deploy, docs

**Files:**
- Modify: `CLAUDE.md` (a dated line under `### scans/` for the four scans and the three DSL additions; the Status bullet), `README.md` (scan JSON fields `hours`, `min_rr`, `telegram: "silent"`, `story`)
- Create: `docs/superpowers/notes/pd-fade-backtest-2026-11-0X.md` (the backtester's output for both fades)

- [ ] **Step 1: On the VPS, outside market hours, pull and backtest both fades** (writes their replay rows so the rail's per-scan stats show the record from day one)

```bash
cd /opt/scanradar && git pull && docker compose run --rm --no-deps -T api python scripts/backtest_scan.py --scan "PDH fade" --days 20
```

```bash
cd /opt/scanradar && docker compose run --rm --no-deps -T api python scripts/backtest_scan.py --scan "PDL fade" --days 20
```

Expected: PDH fade about 20 fires/day, hit rate within a few points of 59 %, median R:R near 1:0.9, avg R near +0.10; PDL fade about 4/day, hit rate near 83 %. Paste both outputs into the note.

- [ ] **Step 2: Deploy** (`docker compose up -d --build --no-deps api engine`), then check `docker compose logs --tail 20 engine` shows the scan count including the four new names and no traceback; `curl localhost:8000/api/alerts?limit=5` works.

- [ ] **Step 3: Docs and commit**

```bash
git add CLAUDE.md README.md docs/superpowers/notes/
git commit -m "docs: PD fade alarms (hours, min_rr, silent Telegram, story) and their 20-session backtest"
```

- [ ] **Step 4: First live day**: confirm one silent break note and one fade alarm arrive in Telegram with the story line and the odds line; the fade row sits on the Trades tab with PASS/BLOCK; a Watch card shows the chips.
