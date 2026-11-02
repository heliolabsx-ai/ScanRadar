# PD fade backtest · 2 Nov 2026 (production backtester, 20 sessions 5 Oct–2 Nov, run after the deploy)

Entry = the price at the fire minute (the forming bar's first tick after the 5-minute bar closed back through the line), as the engine trades it. Stop at the day's extreme, target the previous close, one fade per stock per day, price > ₹1,000. Sizes in shares at the Trades settings' max loss. Before costs (about ₹80–100 per round trip at this size).

```
PDH fade — 20 sessions 2026-10-05 .. 2026-11-02
fires: 526 (26.3 per day), graded 526
target first: 210 (54%)   stop first: 180   neither by close: 136
r15: mean -0.05%  median -0.02%  (favourable = positive)
r30: mean -0.04%  median -0.02%  (favourable = positive)
r60: mean -0.05%  median -0.04%  (favourable = positive)
median R:R 1:1.20
avg R per trade +0.00  (501 trades: target = +R:R, stop = -1, open = 60-min move)
at size (shares by max loss ₹2,000), before costs: total ₹12,231  mean ₹24  worst ₹-2,000  best ₹10,184
by hour:  09h 191 fires 45% target -0.12% @30m  10h 120 fires 50% target +0.03% @30m  11h 72 fires 40% target +0.02% @30m  12h 51 fires 31% target -0.01% @30m  13h 38 fires 29% target +0.06% @30m  14h 38 fires 24% target -0.06% @30m  15h 16 fires 0% target +0.00% @30m
most frequent: HCLTECH 11, LTM 9, DRREDDY 8, SHREECEM 8, CIPLA 8, ULTRACEMCO 7, INFY 7, TECHM 7
===PDL===
PDL fade — 20 sessions 2026-10-05 .. 2026-11-02
fires: 97 (4.8 per day), graded 97
target first: 60 (83%)   stop first: 12   neither by close: 25
r15: mean +0.16%  median +0.11%  (favourable = positive)
r30: mean +0.17%  median +0.08%  (favourable = positive)
r60: mean +0.20%  median +0.18%  (favourable = positive)
median R:R 1:0.42
avg R per trade +0.16  (97 trades: target = +R:R, stop = -1, open = 60-min move)
at size (shares by max loss ₹2,000), before costs: total ₹31,387  mean ₹324  worst ₹-1,999  best ₹4,319
by hour:  09h 45 fires 69% target +0.34% @30m  10h 52 fires 56% target +0.03% @30m
most frequent: ABB 4, LODHA 3, BSE 3, GVT&D 3, MUTHOOTFIN 3, POLYCAB 3, LTM 3, CHOLAFIN 3
```

Reading: the PDL fade (morning, 1 % flush) is the edge: 83 % hit at 1:0.42, +0.16 R, ₹324 a trade before costs. The PDH fade as deployed is break-even before costs (54 % at 1:1.2, +0.00 R, ₹24 a trade): weaker than the design spike's 59 % / +0.10 R because the engine enters at the next tick rather than the bar's close and the window moved by a week. Treat the PDH fade as information until re-tested; the PDL fade as the alarm.
