# Pin bars at the day's extreme — backtest, 4 Nov 2026

Saurabh's Chartink scan (screenshot, 4 Nov 2026): on the 15-minute chart, any one of four groups. Each group: the
bar's range is more than three times its body, the bar is green or red (no doji), one end of the body sits on the
bar's extreme, and that extreme is the day's high (groups 1 and 3) or the day's low (groups 2 and 4). Folded into
two ScanRadar scans by wick: "no lower wick at the day high" (short) and "no upper wick at the day low" (long),
which is exactly the union of the Chartink groups. Needed the derived candle fields `range`, `body`, `upper_wick`,
`lower_wick` in `core/rules.py` (tests `tests/test_rules_derived.py`).

Run on the VPS from a side checkout (`docker compose run --rm --no-deps -v .../core:/app/core:ro -v .../scans:/app/scans:ro
api python scripts/backtest_scan.py --scan NAME --days 20`), 20 sessions 6 Oct–3 Nov 2026, 213 stocks, stop at the
wick end (the day's extreme), target 1:2, sizing by a ₹1,000 max loss, before costs.

| Variant | Fires/day | Target first | Stop first | Avg R | At size |
|---|---|---|---|---|---|
| Day high, as Chartink has it (forming bar, all day, any price) | 80.2 | 28 % | 65 % | −0.12 | −₹33,222 |
| Day low, as Chartink has it | 111.2 | 29 % | 65 % | −0.10 | −₹14,460 |
| Day high, last closed bar, 09:45–15:00, price > ₹1,000, one per stock per day | 6.8 | 24 % | 67 % | −0.23 | −₹5,743 |
| Day low, same | 5.5 | 33 % | 62 % | −0.00 | −₹2,108 |

Why the literal version is noise: Chartink's `[0]` is the forming bar. A minute into a 15-minute bar its range is a
few paise, so "range > 3 × body" passes on almost any bar, and the first bar of the day is the day's high and low by
definition (43 % of the day-high fires were in the 09:15 hour). The stop at the end of a tiny wick is why two in
three trades stop out.

The closed-bar version is a real pin bar, but still no edge over these 20 sessions: the short side lost in every
hour; the long side was mildly positive only in the 10:00 (38 %) and 12:00 (36 %) hours and flat overall.

Decision: both scans parked in `scans/parked/` (the engine and API read only `scans/*.json`), the DSL fields merged.
Things that might change the picture and were not tested: a wider stop (beyond the wick by some %), a 1:1 target,
confirmation by the next bar closing through the pin's body, or restricting to stocks already in a trend.
