# PD fade alarms: fading a break of yesterday's high or low

Date: 1 Nov 2026 (brainstormed 31 Oct–1 Nov). Owner: Saurabh Verma. Status: draft for approval.

## Intent

Saurabh's observation: when a stock crosses yesterday's high (PDH) it usually comes back to PDH, and when
it crosses yesterday's low (PDL) it usually falls a little further and then climbs back to PDL. He wanted
an alarm for it. Checked on 20 stored sessions (1–30 Oct 2026, 213 stocks): a close above PDH came back
to touch PDH 87 % of the time and closed back below it 80 %; a close below PDL fell at least 0.5 %
further 76 % of the time and came back to touch PDL 86 %. His other two ideas (open on the line, hold,
travel to the other extreme) reached the far side only one time in six and are not built.

As a trade with a stop, the fade is a modest edge, not a free one. Backtested with the real rule engine
over the same 20 sessions, one fade per stock per day, price above ₹1,000, stop at the run's extreme,
target at the previous close:

| Alarm | Run before the fade | Window | Fires/day | Hit rate | Median R:R | Avg per trade |
|---|---|---|---|---|---|---|
| PDH fade (short) | ≥ 0.3 % | all day | 20 | 59 % | 1 : 0.9 | +0.10 R |
| PDH fade (short) | ≥ 0.3 % | before 11:00 | 12 | 58 % | 1 : 0.9 | +0.13 R |
| PDL fade (long) | ≥ 1.0 % | before 11:00 | 4 | 83 % | 1 : 0.3 | +0.17 R |
| PDL fade (long) | ≥ 0.3 % | all day | 22 | 56 % | 1 : 0.9 | −0.02 R |

The "first fade of the day" rule is what turned the numbers positive; firing on every re-cross was
break-even. Round-trip costs on a ₹2 lakh intraday position are ₹80–100, a third to a half of the PDH
edge, so the alarm is a candidate list for the Trades tab, not twenty trades a day. Twenty sessions is a
short record: the shape is clear, the decimals will move.

## Decisions Saurabh made

- Build it (31 Oct 2026): "numbers are good and have opportunities".
- **Two stages** (1 Nov 2026, chose A on the mockup `pd-fade-alarm.html`): a quiet "break" note when
  the line goes, then the loud "fade" alarm at the first 5-minute close back through the line.
- The alarm reaches him in the four places the mockup showed: Telegram, Mac notification, a Trades tab
  row with a Watch card chip, and the chart with the break and entry bars marked and the stop/target
  lines drawn.

## My calls (Saurabh to check while reading)

- **Four scan files**, all 5-minute, in `scans/`:
  - `PDH_break.json` — stage 1, short side: bar 1 closed above PDH, bar 2 closed at or below it. One per
    stock per day. No `trade` block (so the trade loop ignores it), `"desk": false`, Telegram **silent**.
  - `PDH_fade.json` — stage 2: bar 1 closed below PDH, bar 2 closed at or above it, the day's high is at
    least 0.3 % above PDH, close > ₹1,000. One per stock per day. Stop = day high, target = previous
    close. Telegram on, desk on.
  - `PDL_break.json` — mirror of the break note, with the **same 09:20–11:00 window as the PDL fade**,
    so no "watching for the fade" goes out after a fade can no longer fire (reviewer's question 2).
  - `PDL_fade.json` — mirror with a **1 % run** and a **09:20–11:00 window**: bar 1 closed above PDL,
    bar 2 at or below, the day's low at least 1 % below PDL, close > ₹1,000. Stop = day low, target =
    previous close. Telegram on, desk on.
- The PDH fade runs all day (the afternoon is weaker, not negative); the PDL fade only before 11:00
  (all-day is break-even). Both can be changed by editing the JSON.
- "One per stock per day" uses the existing cooldown (`cooldown_min` 400) rather than a new flag.
- **Gap opens count as breaks.** The two break scans are `"continuous": true` (as B0101 is), so
  yesterday's last bar sits in front of 09:15 and a stock that opens above PDH fires its break note on
  the 09:15 bar's close. The fade scans stay non-continuous, exactly as backtested (reviewer's question 3).
- **Times are the minute the bar closed**, which is when the alert fires and how every existing row,
  marker and chip is stamped: a fade on the 15:10 bar reads 15:15 everywhere (Telegram footer, story line,
  chip, Trades row, triangle). The mockup's "15:00 / 15:10" were bar-start times and are illustrative
  (reviewer's question 4).
- The target is the previous close only. The far extreme (PDL for a PDH fade) hit one fade in six and
  loses as a trade; it is not offered as T2. The halfway target was dropped (needs 90 % hits to pay).
- **Per-scan minimum R:R.** The Trades queue and the desk take rule alerts only at R:R ≥ 1.5
  (`DESK_ALERT_MIN_RR`). The fades sit near or below 1:1 by design (the PDL fade's median is 1:0.3), so
  the fade scans carry `"min_rr": 0` (no floor), which replaces the global floor for that scan in
  `trades.candidates` and `live_desk.process_minute`. Every fade gets its row; the backtest table above
  is the unfiltered set. (Reviewer's question 1: a floor of 0.5 would have dropped half the PDL fades.)
- **Silent Telegram** = `"alert": {"telegram": "silent"}` → `disable_notification: true` on the Telegram
  send (`core/notify.py` and `engine/notify.py` gain an optional flag). The message still arrives; the
  phone does not buzz.
- Stage 1 shows on the Watch card as a muted chip `ABOVE PDH 15:00` / `BELOW PDL 09:40` until the fade
  fires or the day ends; stage 2 as the red/green chip `PDH FADE · SELL 15:10 · 61/29` (the odds pair the
  chips already carry). Today chips come from pattern rows and desk verdicts only; rule alerts of these
  four scans join them.
- The chart marks the break bar with a grey triangle and the entry bar with a red (short) or green
  (long) triangle; the Trades drawer already draws the stop and target lines from the row.
- Odds, shares and the gate come from the existing trade loop unchanged (the fade is a rule alert with
  stop and target). The Time Machine replay and the calibration note will grade the fades like any alert.

## Goals

- The stage-2 alarm within the minute the 5-minute bar closes back through the line, with entry, stop,
  target, R:R, the odds line and the share count, in Telegram and on the Trades tab.
- The stage-1 note ten minutes to hours earlier, silent, so the fade never arrives unannounced.
- Backtested with `scripts/backtest_scan.py` before go-live so the rail's per-scan stats carry the
  20-session record from day one.

## Non-goals

- No new detector, no change to the ranking, the Time Machine, the gate or the Trades tab layout.
- No "open on the line and hold" alarms (the data did not support them as trades).
- No order placement.

## Anti-goals

- Firing on every wobble around the line: one fade per stock per day, and a real run first.
- A fade that shows up with no odds or size because a global filter dropped it.
- A loud message for stage 1.

## Constraints

- The rule language (`core/rules.py`) already has `prev_day_high/low/close`, `day_high/low`, `off`,
  `mul`, `trade.stop/target` as a series. Two things it lacks and this design adds:
  1. **A time window on a scan**: `"hours": {"from": "09:20", "to": "11:00"}` at scan level, honoured by
     the engine, `core.backtest.fires` and the alert replay, keyed on the bar's close minute (IST).
  2. **`min_rr`** at scan level (see above).
- Deploy as before: scans ship on `git pull` (the engine reads `scans/` on each run); the engine and API
  rebuild together with `--no-deps api engine` outside market hours; ingest untouched.
- All times IST; a 5-minute bar "closes" at its start + 5 minutes; the entry price is the close of the
  bar that crossed back (the engine's `price = bars.close.iloc[-1]` at the fire minute).

## Scan definitions (the builder translates these to the JSON DSL exactly)

PDH fade (short):
```
[=1] close < prev_day_high
[=2] close >= prev_day_high
day_high > prev_day_high * 1.003
[=0] close > 1000
stop = day_high · target = prev_day_close · cooldown 400 min · min_rr 0 · telegram on
```
PDL fade (long):
```
[=1] close > prev_day_low
[=2] close <= prev_day_low
day_low < prev_day_low * 0.99
[=0] close > 1000
hours 09:20–11:00 · stop = day_low · target = prev_day_close · cooldown 400 · min_rr 0 · telegram on
```
PDH break (stage 1): `[=1] close > prev_day_high`, `[=2] close <= prev_day_high`, cooldown 400, no trade,
`"desk": false`, telegram silent. PDL break: mirror.

`[=n]` is the DSL's `off` (0 = forming bar, 1 = last closed bar). The 0.3 % and 1 % runs, the ₹1,000
floor and the 11:00 cut are the numbers from the backtest table above.

## Dashboard

- Watch chips for the four scans (muted for breaks, coloured for fades), via the existing
  `ScanWatch.events` with alerts passed in.
- Chart markers on the detail/drawer chart: grey triangle at a break alert, red/green at a fade alert
  (the chart already draws alert markers; the shape and colour per scan family is the change).
- Nothing else changes; the Trades tab shows the fade rows like any alert.

## Telegram text

Stage 2 (the scan's `note` supplies the one-line story template):
```
PDH fade · SELL
MAXHEALTH broke above PDH 866.45 at 15:00, ran to 873.90 (+0.9 %), first 5m close back below at 15:10.
entry ₹864.20 · stop ₹873.90 (run high) · target ₹851.15 (prev close) · R:R 1:1.3
T1 61 % · stop 29 % · odds favour T1 · 206 sh · PASS
15:10 · scanradar.tech
```
The story line needs the break time and the run high: the engine records them from the day's bars
when the fade fires (`payload.story = {break_at, run_px, run_pct}`) and the Telegram template reads
them. Stage 1: `PDH break · MAXHEALTH above 866.45 at 15:00 · watching for the fade` (silent).

## Tests

- `tests/test_rules_hours.py`: the window filter in `fires` and the engine's evaluate path (a fire at
  10:55 passes, one at 11:00 does not).
- `tests/test_scans_pd_fade.py`: on a `shaped_day` with a hand-built break and fade, the four scans fire
  exactly once each at the right minute with the right stop/target; the break scans carry no trade.
- `tests/test_trades.py`: `min_rr` on a scan admits a 1:0.9 alert to the queue; a scan without it still
  needs 1.5.
- `tests/web/test_watch.mjs`: alert chips for the four scans.
- Before go-live: `scripts/backtest_scan.py --scan "PDH fade" --days 20` and the same for PDL, numbers
  within a few points of the table above.

## Rollout

1. DSL additions (`hours`, `min_rr`, silent Telegram) with tests.
2. The four scan files, backtested; the chart markers and Watch chips.
3. Deploy outside market hours (`--no-deps api engine`); first live day watched like the trade loop's.

## Settled by the reviewer's reading (the builder's own calls, recorded)

- A scan with no `trade` block is never graded at pivot levels on replay (the break scans carry a
  `side` only for the chip colour).
- The Mac notification does not ring for break alerts, only for fades.
- The story template lives in its own scan field (`"story"`), not in `note`.
- `scripts/backtest_scan.py` prints median R:R and average R so the go-live check can be read against
  the table above.

## Left to the builder

- Exact JSON field names for `hours`, `min_rr` and the silent flag.
- How the story fields are computed in the engine (first bar closing through the line that day, and the
  extreme since) and where the Telegram template lives (`engine/main.py` today).
- Chip wording when both a break and a fade exist on one stock (the fade wins).
