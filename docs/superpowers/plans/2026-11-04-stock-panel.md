# Stock panel, market structure, cleaner chart, history — implementation plan

> **For agentic workers:** executed natively in the session that wrote it (Saurabh said "deploy"); one
> fresh reviewer on the whole branch at the end. Steps use checkbox syntax.

**Goal:** every chart on the dashboard gets the new renderer (BOS/CHoCH, declutter, pan/zoom, previous
sessions) and a click on it opens one full-screen stock panel with all the details.

**Architecture:** pure helpers in `web/structure.js` (node tests), the renderer and the panel in
`web/index.html`, one new query in `api/alerts.py` behind `GET /api/alerts?symbol=&days=`.

**Spec:** `docs/superpowers/specs/2026-11-04-stock-panel-design.md`

## Global constraints

- `engine/` untouched. `api/alerts.py` gains one function; `ALERT_SCHEMA` unchanged (replay rows are not touched).
- Bump `?v=` for `structure.js` (new) and any helper changed; `index.html` itself is not cache-busted.
- As-of: everything cut at `S.at`; previous sessions always complete.
- Tests: `node --test tests/web/*.mjs` and `.venv/bin/python -m pytest tests -q` green before the merge.

## Review focus

1. A symbol with no candles today (or `S.at` before 09:16): the panel and chart must not throw; tiles show "—".
2. A 5m bar straddling the as-of minute: the structure must not use the forming bar as a swing or a break.
3. Pan past the data: the view clamps, no NaN in the axis.
4. Days 2/3 when only one previous session is stored: the chart shows what exists and says so in the readout.
5. The history list on a settled day shows only sessions before that day (never today's or later rows).

---

### Task 1: `web/structure.js` + tests

**Files:** create `web/structure.js`, `tests/web/test_structure.mjs`.

**Produces:** `ScanStructure.resample(mins, tf)`, `structure(bars5, k)`, `mapIndex(bars, b5, j, end)`,
`niceStep(x)`, `fitView(n, i0, perDay, days, tf, full)`, `axisEvery(tf, slot)`, `positionWords(px, vwap, pdh, pdl, trend)`.

- [ ] Tests: resample keeps sessions apart and marks the forming bar `stub`; structure on a hand-built
      zigzag yields BOS up, BOS up, CHoCH down, BOS down with the right prices and bar indices; a swing is
      confirmed k bars later (no event before confirmation); `upLevel`/`dnLevel` are the last unbroken swings;
      mapIndex maps a 5m event onto 1m (end minute) and 15m bars; fitView gives the last 120 bars on 1m
      unless `full`; niceStep 0.37 → 0.25, 2.1 → 2.5; axisEvery picks the first spacing ≥ 64 px.
- [ ] Implement; `node --test tests/web/test_structure.mjs` green.
- [ ] Commit `feat(web): market structure helpers (swings, BOS/CHoCH), chart view helpers`.

### Task 2: API history

**Files:** modify `api/alerts.py`, `api/main.py`; test in `tests/test_api_alerts.py`.

- [ ] `symbol_history(con, symbol, days)`: `SELECT id,ts,scan,symbol,price,payload,source FROM alerts WHERE symbol=%s AND ts >= %s ORDER BY ts DESC`
      with `ts >= now - days*2 calendar days` (covers 20 sessions with holidays), merged live/replay, the
      `alerts_for_day` row shape, then cut to the first `days` distinct days. Test with the `_Con` fake: the
      SQL params, the merge, the day cut.
- [ ] Route: `alerts(limit, day, symbol: str | None = None, days: int = 20)` → when `symbol` is set return
      `{symbol, days, alerts}`; `symbol` upper-cased.
- [ ] pytest green; commit `feat(api): per-symbol alert history (/api/alerts?symbol=&days=)`.

### Task 3: chart renderer

**Files:** modify `web/index.html` (`drawChart`, `wireChart`, `loadAndDraw`, chart cache, CSS for the toolbar chips).

- [ ] `<script src="structure.js?v=…">`; `S.ov` from `localStorage` `chart.ov` (defaults
      `{vwap:1,pd:1,piv:0,or:0,ob:0,bos:1,sw:0,al:1,ses:1}`), `S.swing` from `chart.swing` (3), `S.cdays` (1).
- [ ] Toolbar: existing tf buttons + days 1/2/3 + show chips + swing 2/3/5 + fit; rendered by `chartToolbar()`
      into every `.tfrow`.
- [ ] `loadAndDraw`: also fetch the previous sessions needed (`S.cdays` or 2 for structure) into `chartCache.prev`.
- [ ] `drawChart`: view in bar units, gridlines, session separators, overlays per chip, structure overlay,
      candles/volume/OI, markers (alerts, timeline, pattern, trade levels) through the new x mapping, axis,
      crosshair with the structure event and alarm in the readout. 1D unchanged.
- [ ] Pointer: drag pans, wheel zooms, plain click opens the stock panel (not inside the panel itself).
- [ ] Manual check through the dev proxy on 4 Nov: Scanner, Patterns, Watch, Trades, Alarms charts.
- [ ] Commit `feat(web): one chart renderer with market structure, pan/zoom, previous sessions, overlay chips`.

### Task 4: the stock panel

**Files:** modify `web/index.html` (lightbox markup/CSS, `openStockPanel`, `closeStockPanel`, story/history builders).

- [ ] Lightbox becomes the panel container: header, tiles (+ Structure), chart, ladder (+ SWING H/L, structure
      now), ticket, Story, History. Esc / backdrop / Esc ✕ close; the previous panel re-renders on close.
- [ ] Data: `/api/watch?symbols=` at the minute (tiles), `/api/alarms?day&at` rows for the symbol (ticket,
      timeline; cached per minute), `S.alerts` / `S.prows` / `S.desk` for the story fallback, the new history route.
- [ ] Every chart's click and the ⤢ button open it.
- [ ] Commit `feat(web): the stock panel behind every chart (click to enlarge with all details)`.

### Task 5: docs, review, deploy

- [ ] `CLAUDE.md`: web section (structure.js, the stock panel, chart toggles), API route, status line.
- [ ] Reviewer on the whole branch; fix wave; tests green.
- [ ] Merge to `main`, push; VPS: `git pull` then `docker compose up -d --build --no-deps api` (after 15:30 or
      before 09:00); verify `/api/alerts?symbol=JSWENERGY&days=20` and the panel on the live site.
