# Pattern backtest · 2026-10-18 13:45 IST

Detector `718186796f0d`, params `{"tol": 0.004, "min_sep": 6, "max_sep": 36, "min_retrace": 0.008, "k": 2, "fail_bars": 3, "min_extension": 1.0}`, 42 trading days (2026-08-18 .. 2026-10-16), 4606 patterns, rows written.

Hit rate = target touched before stop, among graded triggers. Failed = closed back through the neckline within 3 bars before the target. Returns are signed in the trade's favour.

| kind | triggers | forming only | hit rate | failed ≤3 bars | avg r30 | avg r60 | median min to target | score≥3 triggers / hit | score<3 triggers / hit |
|---|---|---|---|---|---|---|---|---|---|
| double_bottom | 633 | 1659 | 64% | 46% | 0.04% | 0.05% | 37 | 350 / 74% | 283 / 52% |
| double_top | 783 | 1384 | 58% | 42% | 0.01% | 0.02% | 65 | 458 / 62% | 325 / 51% |
| triple_bottom | 11 | 69 | 100% | 73% | -0.17% | 0.24% | 44 | 5 / — | 6 / 100% |
| triple_top | 21 | 46 | 50% | 52% | -0.05% | -0.11% | 71 | 16 / 50% | 5 / — |

## Per day

| day | patterns | triggered | inserted |
|---|---|---|---|
| 2026-08-18 | 56 | 16 | 56 |
| 2026-08-19 | 116 | 33 | 116 |
| 2026-08-20 | 75 | 26 | 75 |
| 2026-08-21 | 58 | 23 | 58 |
| 2026-08-24 | 30 | 7 | 30 |
| 2026-08-25 | 63 | 19 | 63 |
| 2026-08-26 | 75 | 14 | 75 |
| 2026-08-27 | 70 | 20 | 70 |
| 2026-08-28 | 59 | 13 | 59 |
| 2026-08-31 | 107 | 41 | 107 |
| 2026-09-01 | 128 | 39 | 128 |
| 2026-09-02 | 132 | 63 | 132 |
| 2026-09-03 | 96 | 32 | 96 |
| 2026-09-04 | 68 | 19 | 68 |
| 2026-09-07 | 84 | 17 | 84 |
| 2026-09-08 | 71 | 35 | 71 |
| 2026-09-09 | 118 | 46 | 118 |
| 2026-09-10 | 69 | 20 | 69 |
| 2026-09-11 | 140 | 72 | 140 |
| 2026-09-15 | 105 | 26 | 105 |
| 2026-09-16 | 108 | 33 | 108 |
| 2026-09-17 | 118 | 40 | 118 |
| 2026-09-18 | 98 | 26 | 98 |
| 2026-09-21 | 86 | 17 | 86 |
| 2026-09-22 | 78 | 21 | 78 |
| 2026-09-23 | 74 | 10 | 74 |
| 2026-09-24 | 78 | 14 | 78 |
| 2026-09-25 | 85 | 20 | 85 |
| 2026-09-28 | 181 | 23 | 181 |
| 2026-09-29 | 131 | 46 | 131 |
| 2026-09-30 | 171 | 71 | 171 |
| 2026-10-01 | 169 | 86 | 169 |
| 2026-10-05 | 185 | 46 | 185 |
| 2026-10-06 | 79 | 15 | 79 |
| 2026-10-07 | 137 | 43 | 137 |
| 2026-10-08 | 245 | 43 | 245 |
| 2026-10-09 | 172 | 62 | 172 |
| 2026-10-12 | 144 | 64 | 144 |
| 2026-10-13 | 143 | 48 | 143 |
| 2026-10-14 | 122 | 20 | 122 |
| 2026-10-15 | 162 | 64 | 162 |
| 2026-10-16 | 120 | 55 | 120 |

## Reading (18 Oct 2026)

- Chosen defaults after a 10-day sweep of five settings: bounce between the points ≥ 0.8 %, points ≥ 6 bars apart, day already moved ≥ 1 % at the second point. The loose defaults produced ~800 patterns a day with flat returns; these produce 15–30 a day and the score separates them.
- The score is the edge: double bottoms with score ≥ 3 hit the target before the stop 74 % of the time (350 triggers) versus 52 % below 3; double tops 62 % versus 51 %. Triples are rare (11 + 21 triggers in 42 days) and not yet meaningful.
- Mean returns 30 and 60 minutes after the trigger are flat for every setting even where the hit rate is high: the target prints and price comes back. Take the target when it prints; do not hold a pattern trade for the clock.
- 42–46 % of triggers close back through the neckline within 3 bars. The page shows these as *failed*; treating the first close back through the neckline as the exit keeps the loss near zero.
- Backfilled days carry no tick delta; the volume check uses total 5m volume. OI before 29 Sep (the previous expiry) is thin, so the OI check is weaker on older days.
