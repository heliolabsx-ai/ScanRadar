# Pattern backtest · 2026-10-18 14:21 IST

Detector `0ffda4dc8301`, params `{"tol": 0.004, "min_sep": 6, "max_sep": 36, "min_retrace": 0.008, "k": 2, "fail_bars": 3, "min_extension": 1.0}`, 42 trading days (2026-08-18 .. 2026-10-16), 5746 patterns, rows written.

Hit rate = target touched before stop, among graded triggers. Failed = closed back through the neckline within 3 bars before the target. Returns are signed in the trade's favour.

| kind | triggers | forming only | hit rate | failed ≤3 bars | avg r30 | avg r60 | median min to target | score≥3 triggers / hit | score<3 triggers / hit |
|---|---|---|---|---|---|---|---|---|---|
| double_bottom | 340 | 1714 | 63% | 47% | 0.02% | 0.01% | 40 | 194 / 67% | 146 / 58% |
| double_top | 462 | 1592 | 57% | 42% | -0.01% | -0.01% | 79 | 263 / 64% | 199 / 50% |
| triple_bottom | 120 | 722 | 61% | 48% | 0.02% | 0.10% | 44 | 70 / 71% | 50 / 47% |
| triple_top | 153 | 643 | 66% | 43% | 0.06% | 0.05% | 34 | 99 / 66% | 54 / 67% |

## Per day

| day | patterns | triggered | inserted |
|---|---|---|---|
| 2026-08-18 | 77 | 10 | 77 |
| 2026-08-19 | 133 | 24 | 133 |
| 2026-08-20 | 94 | 21 | 94 |
| 2026-08-21 | 61 | 18 | 61 |
| 2026-08-24 | 38 | 8 | 38 |
| 2026-08-25 | 76 | 13 | 76 |
| 2026-08-26 | 94 | 11 | 94 |
| 2026-08-27 | 85 | 12 | 85 |
| 2026-08-28 | 79 | 10 | 79 |
| 2026-08-31 | 121 | 31 | 121 |
| 2026-09-01 | 162 | 30 | 162 |
| 2026-09-02 | 164 | 35 | 164 |
| 2026-09-03 | 112 | 23 | 112 |
| 2026-09-04 | 90 | 17 | 90 |
| 2026-09-07 | 104 | 9 | 104 |
| 2026-09-08 | 98 | 21 | 98 |
| 2026-09-09 | 140 | 34 | 140 |
| 2026-09-10 | 82 | 12 | 82 |
| 2026-09-11 | 167 | 51 | 167 |
| 2026-09-15 | 131 | 20 | 131 |
| 2026-09-16 | 128 | 27 | 128 |
| 2026-09-17 | 160 | 29 | 160 |
| 2026-09-18 | 128 | 26 | 128 |
| 2026-09-21 | 104 | 9 | 104 |
| 2026-09-22 | 93 | 19 | 93 |
| 2026-09-23 | 95 | 9 | 95 |
| 2026-09-24 | 101 | 12 | 101 |
| 2026-09-25 | 111 | 16 | 111 |
| 2026-09-28 | 214 | 18 | 214 |
| 2026-09-29 | 193 | 31 | 193 |
| 2026-09-30 | 222 | 56 | 222 |
| 2026-10-01 | 212 | 66 | 212 |
| 2026-10-05 | 229 | 35 | 229 |
| 2026-10-06 | 103 | 12 | 103 |
| 2026-10-07 | 192 | 38 | 192 |
| 2026-10-08 | 286 | 34 | 286 |
| 2026-10-09 | 227 | 45 | 227 |
| 2026-10-12 | 178 | 48 | 178 |
| 2026-10-13 | 171 | 33 | 171 |
| 2026-10-14 | 152 | 16 | 152 |
| 2026-10-15 | 190 | 47 | 190 |
| 2026-10-16 | 149 | 39 | 149 |
## Reading (18 Oct 2026, detector v3)

- Chosen defaults after a 10-day sweep of five settings: bounce between the points ≥ 0.8 %, points ≥ 6 bars apart, day already moved ≥ 1 % at the second point. The loose defaults produced ~800 patterns a day with flat returns; these produce 15–30 live rows a day and the score separates them.
- Detector v2 makes a later second point supersede the earlier double on the same first low (or extend it into a triple), which removed the near-duplicate rows v1 produced and is why triples are now common.
- The score is the edge: double bottoms with score ≥ 3 hit the target before the stop 67 % of the time (194 triggers) versus 58 % below 3; double tops 64 % versus 50 %; triple bottoms 71 % versus 47 %.
- Mean returns 30 and 60 minutes after the trigger are flat for every setting even where the hit rate is high: the target prints and price comes back. Take the target when it prints; do not hold a pattern trade for the clock.
- 42–48 % of triggers close back through the neckline within 3 bars. The page shows these as *failed*; treating the first close back through the neckline as the exit keeps the loss near zero.
- Backfilled days carry no tick delta; the volume check uses total 5m volume. OI before 29 Sep (the previous expiry) is thin, so the OI check is weaker on older days.
