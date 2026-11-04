# Stock panel on every chart, market structure, cleaner chart, history — design

Date: 4 Nov 2026. Owner: Saurabh. Approved from the mockup
`.superpowers/brainstorm/99915-1793812957/content/stock-panel-v4.html` (JSWENERGY, 2–4 Nov 2026 real bars) with "deploy".

## Intent

Saurabh showed the Alarms stock panel (header, tiles, chart, level ladder, ticket, story) and asked for
that level of detail behind **every chart on every page**, **BOS / CHoCH** (break of structure, change of
character) drawn on every chart, a chart that is **not cluttered** (candles readable), and **history**
(previous sessions). One rule from the mockup round: **a click on any chart enlarges it, and the enlarged
view carries all the details**, not just the canvas.

## Goals

1. One **stock panel** (`openStockPanel(symbol)`) opened by a click on any chart: Scanner detail, Patterns
   detail, Watch drawer, Trades drawer, Alarms drawer. Full-screen, Esc closes. Contents, top to bottom:
   header (symbol, BUY/SELL pill and setup of the latest alarm today, `fired HH:MM · now HH:MM · position
   words · sector`, price and % vs previous close), five tiles (VWAP, OI vs yesterday with the build-up
   chip, RVOL, day range with the position dot, **Structure**), the chart with its toolbar, the level
   ladder with NOW and the two **swing levels**, a "structure now" block (trend, the price whose 5m close
   would be the next BOS or CHoCH either way), the ticket (the Alarms ticket for an alarm row, the break
   note for a PD break, nothing for a plain stock), the **story** (today's events for the symbol in time
   order) and **this stock before today** (its stored alerts over the last 20 sessions with outcomes).
2. **Market structure** on 5-minute bars, drawn on every intraday timeframe: fractal swings of strength
   `k` (default 3, choices 2/3/5, persisted), a swing confirmed `k` bars after it; a 5m **close** through
   the last unbroken swing high is a BOS when the trend is up or unknown and a CHoCH when it is down; the
   mirror for a swing low; the trend flips on the break. BOS = thin solid line in the direction's colour
   from the swing to the break bar with a pill; CHoCH = amber dashed, same pill. Swing labels HH/HL/LH/LL
   (vs the previous swing of the same kind) are an optional overlay. Structure is computed over the
   **previous stored session plus today** so the morning has context. BOS/CHoCH rows appear in the story in grey.
3. **Cleaner chart**, same renderer for every panel: price gridlines with axis labels; candles with a body
   at least 2 px and hollow bodies for up bars when wide enough; VWAP thin solid; PDH/PDL thin solid with a
   filled tag (no band); pivots R1/R2/S1/S2/PC as **edge tags only** (lines on a toggle); opening range,
   order block, swing labels, session lines and alarm markers on toggles; **drag to pan, wheel to zoom**;
   1-minute opens on the **last two hours** (a "fit day" button shows the whole session); a "days" control
   1/2/3 puts previous sessions on the chart with dashed separators and a date label per session.
   Toggle state lives in `localStorage` `chart.ov`, the swing strength in `chart.swing`.
4. **History**: previous sessions on the chart (above) and the symbol's alerts over the last 20 sessions
   under the panel, from a new `GET /api/alerts?symbol=SYM&days=20`.

## Non-goals

- Tick-by-tick history: only 1-minute candles are stored. Said so to Saurabh in the mockup; unchanged.
- Rewriting each tab's inline detail panel: they keep their markup and get the new chart renderer; the
  unified panel is the enlarged view they all open.
- A BOS/CHoCH scan or Telegram alarm. Structure is drawn and reported, not fired. A rule-DSL series can come later.
- Changing the engine, the ranking, the replay rows or the Alarms board API.

## Constraints

- `web/` is a read-only Caddy mount: web changes ship with `git pull`; bump every changed helper's `?v=`.
- The API change (one query, one route parameter) needs `--no-deps api` outside market hours.
- All times IST, bar `ts` = minute start; a 5m bar's event minute is shown as its close (start + 5).
- The as-of rule: nothing after `S.at` leaks in (structure, story, tiles, history list cut at the day).
- Replays never move unread badges, never log paper trades; unchanged.

## Decided how

- Pure helpers in a new `web/structure.js` (`ScanStructure`): `resample(mins, tf)` session-aware
  (`day`, `slot`, `m`, `i`, `stub`), `structure(bars5, k)` → `{sw, ev, trend, upLevel, dnLevel}`,
  `mapIndex(bars, b5, j, end)`, `niceStep(x)`, `fitView(n, i0, perDay, days, tf, full)`,
  `positionWords(px, vwap, pdh, pdl, trend)`, `axisEvery(tf, slot)`. Node tests `tests/web/test_structure.mjs`.
- `drawChart(a)` in `web/index.html` is rewritten around a `view {a, b}` in bar units, overlay toggles
  and the structure overlay; the existing pattern overlay, trade levels, timeline markers and alert
  markers are kept and placed through the new x mapping. Daily (1D) stays as before.
- Candle cache gains previous sessions: `chartCache.prev = {day: candles}` fetched lazily from
  `/api/candles/{sym}?day=` for the one or two stored sessions before `S.day` (from `S.days`).
- `openStockPanel(symbol, opts)` replaces `openChartBig`: it reuses `drawAlarmPanel`'s pieces (tiles,
  ladder, ticket, story) with the symbol's board row when one exists at the minute, else a synthetic row;
  the story merges the symbol's board timeline, today's alerts, pattern triggers and desk verdicts, plus
  BOS/CHoCH rows; history from the new route, cached per symbol for a minute.
- `api/alerts.symbol_history(con, symbol, days)`: alerts for the symbol with `ts >= today - days*7/5`
  calendar-approximated as the last `days` stored sessions, live/replay merged, newest first, same row shape as
  `alerts_for_day`. Route: `/api/alerts?symbol=&days=` (symbol set → history; `day` ignored).

## Left to the builder

Exact pixel sizes, the pill wording for a plain stock ("no alarm yet"), the colour of swing labels.
