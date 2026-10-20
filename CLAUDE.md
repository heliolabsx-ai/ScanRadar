# ScanRadar — project brief for Claude Code

Owner: Saurabh Verma. Personal intraday NSE F&O scanner. Everything below was decided in design sessions; treat it as the spec unless Saurabh says otherwise.

> **Repo note (17 Oct 2026):** this checkout was cloned from an empty GitHub repo. The code described under *Status* was restored from a tarball of the design session on 17 Oct 2026 and pushed to `origin/main` the same day. The dashboard look-and-behaviour reference is the Claude artifact "Intraday Flow Scanner" (https://claude.ai/artifact/374M9Chnq9UXNQ3ChZDT6C). This project is kept strictly separate from the TrackSo desk UI work: never add that folder to a ScanRadar session or vice versa.

## What it is

Two scanners sharing one data pipeline, with alerts and a web dashboard at **https://scanradar.tech**:

1. **Ranking scanner** — every minute, scores all ~200 NSE F&O stocks and ranks them bull/bear. Modelled on a commercial scanner Saurabh showed (card per stock: % change, OI build-up label, VWAP side, volume delta, order block, S1/S2/R1/R2, R:R, conviction score, rank/percentile, "top 3 picked" rule).
2. **Rule scanner** — Chartink-style conditions on 5-min candles (e.g. `[=3] High <= [=2] Close`), defined as JSON in `scans/`, fire Telegram alerts with cooldown.

Goals beyond the reference products: rank by **relative strength vs sector** rather than raw % change; log every signal and its outcome so the scanner grades itself (hit rate per setup); replay/backtest scans over stored candles. None of the "better than" features are built yet — see To-do.

## Infrastructure (fixed decisions)

| Item | Value |
|---|---|
| Hosting | Hostinger VPS **KVM 2** (2 vCPU / 8 GB), Docker image, Ubuntu. Hostname `srv1906667.hstgr.cloud`, IP **200.234.37.207**, paid to 2027-08-16. |
| Domain | **scanradar.tech**. DNS A records for `@` and `www` → 200.234.37.207 (not yet set as of 17 Oct 2026). |
| TLS / proxy | Caddy (auto Let's Encrypt), basic-auth on the whole site. Config in `Caddyfile`. |
| Broker/data | **Zerodha Kite Connect**, "Connect" plan (₹500/month, WebSocket + historical). App created with redirect URL `https://scanradar.tech/api/auth/callback`. Static IP 200.234.37.207 whitelisted in Kite developer profile (needed only for order APIs from 1 Apr 2026; data APIs are IP-free). |
| Login | **Fully automated** daily login with Kite user ID + password + TOTP secret from `.env` (`ingest/kite_auth.py` replays the browser flow — unofficial, fix here if Zerodha changes the login page). |
| Universe | All NSE stock futures, nearest expiry, indices excluded. Built daily from the instrument dump. |
| Alerts | Telegram bot (token + chat ID in `.env`). |
| Repo | https://github.com/heliolabsx-ai/ScanRadar (private). |
| Timezone | Asia/Kolkata everywhere. Market 09:15–15:30. |

Alternatives considered and rejected: Upstox (free, slightly less reliable feed) — code should keep a thin broker adapter so switching is cheap; Tailscale-only private access instead of a domain — rejected in favour of a public domain + basic auth.

## Architecture

```
Kite WebSocket ──► ingest ──► Postgres/TimescaleDB (candles_1m, daily_ref, instruments, alerts, scan_snapshots)
                      │
                      └──► Redis (live:{sym} forming candle, ref:{sym}, scan:latest, pub/sub)
                                  │
                   engine ◄───────┘  (subscribes candles:closed; runs analytics + rule scans; Telegram)
                      │
                   api (FastAPI) ──► web/index.html (static dashboard)  ◄── Caddy (HTTPS, basic auth)
```

Compose services: `postgres`, `redis`, `ingest`, `engine`, `api`, `caddy` (host network). All config via `.env` (see `.env.example`; never commit `.env`).

### ingest/ (Python 3.12, kiteconnect 5.x)
- `main.py` — scheduler: 08:55 login + universe + daily refs; 09:14 open WS; every minute flush closed candles to Postgres and forming candle to Redis; 15:31 finalize last candle, close WS and **exit the process** (Docker restarts it; KiteTicker's twisted reactor cannot be restarted in-process). If started mid-session on a weekday it runs setup immediately. Ticks outside 09:15–15:30 update OI only, no candles. Historical calls go through `_hist()` (0.4 s pacing, retry on 429). `ingest:status` in Redis shows setup progress or `error: ...`.
- `kite_auth.py` — `session(R)` reuses the Redis-cached token if Kite still accepts it (a fresh login invalidates the previous token for the api_key), else TOTP auto-login → request_token → access_token (cached in `kite:access_token`, 20 h TTL). The Connect redirect chain is walked hop-by-hop so an unreachable/401 callback URL doesn't lose the token; TOTP codes are never resubmitted within one 30 s window. `KITE_PASSWORD` empty = manual-token mode.
- `preflight.py` — first-day check: `docker compose run --rm ingest python preflight.py` (session, universe, historical with OI, 20 s WS sample in market hours).
- `universe.py` — NFO FUT nearest expiry ⋈ NSE EQ → `(symbol, eq_token, fut_token)`; static `SECTOR` map (extend as needed; unmapped → OTHERS).
- `candles.py` — tick → 1-min OHLCV; volume delta by tick rule (uptick = buy); futures FULL-mode ticks supply OI.
- Equity subscribed in QUOTE mode, futures in FULL mode (OI only comes in FULL).

### engine/
- `analytics.py` — per symbol: % change, VWAP, OI % vs prev-day OI, OI label via price×OI matrix on a **30-min intraday trend** (long/short build-up, unwinding, covering), classic pivots from prev H/L/C, opening range (first 15 min), RVOL vs 20-day avg 1-min volume, order block = origin candle of largest 5-bar impulse, stop/target/R:R. `score()` = relative move×18 + OI confirmation + VWAP side + RVOL + R:R.
- `rules.py` — JSON rule DSL. Sides: `{"number":x}`, `{"series":"prev_day_close|prev_day_high|prev_day_low"}`, `{"off":N,"field":"open|high|low|close|volume|vwap|oi"}` (`off` 0 = forming bar; add `"sma":n` / `"ema":n` for a moving average ending at that bar, added 19 Oct 2026). Timeframes 1m/3m/5m/15m/30m/60m resampled from 1-min with 09:15 anchor. `match` all|any. **Scan-owned levels (19 Oct 2026):** optional `"trade": {"stop": SIDE|{"pct"}, "target": SIDE|{"pct"}|{"rr"}}` resolved at the fire minute by `rules.trade_levels()`; engine stores `stop/target/levels` in the alert payload, replay (`api/alerts._trade_payload`) and the desk use the same; fallback to pivot levels when absent or on the wrong side.
- `main.py` — on each `candles:closed` message (debounced 20 s): load today's candles + forming bar, analyse, rank (TOP_N=3 per side, R:R ≥ 1.5 to be "picked"), write `scan:latest` to Redis + `scan_snapshots` to Postgres, evaluate every `scans/*.json`, insert `alerts`, send Telegram (cooldown key `cooldown:{scan}:{symbol}` in Redis).
- `notify.py` — Telegram sendMessage.

### core/ (shared analytics, added 17 Oct 2026)
`analytics.py` and `rules.py` are byte-identical copies of the engine's (the engine switches to importing them in Phase 2, see the design doc). `asof.py` defines the one as-of semantic (`view_at`: complete bars `ts < at` + a flat stub of the `at` bar). `ranking.py` `rank()` is the engine's score/rank/picked logic as a function. `backtest.py` `fires()` replays a scan over a day with the live view at every minute (proven equal to the naive definition by tests) and `outcome()` grades a trade. Tests in `tests/` run with `.venv/bin/python -m pytest tests -q` (`requirements-dev.txt`).

### core/patterns.py (added 18 Oct 2026)
Double/triple bottom/top detector on 5m bars: `detect_day(df1m, prev_close, params)` emits each pattern's lifecycle tagged with the minute it became knowable (bar close), `as_of(patterns, at, ctx)` filters to a minute and scores (extended ≥1 %, OI lower at the second point, volume ≥1.5× avg, sector agrees, VWAP side; sixth check = confirming close). Pairing: a new swing pairs with the most recent earlier qualifying swing; a later second point supersedes the earlier double (or extends it to a triple). `DEFAULTS` tuned 18 Oct (see `docs/superpowers/notes/patterns-backtest-2026-10-18.md`); `detector_hash()` keys stored rows. **v4 (19 Oct 2026, Saurabh's spec):** nine checks (`CHECKS`), score of 8: + exhaustion (second point's swing bars traded less than the first's), breakout volume (confirming close ≥ 1.5× the median of the pattern's bars; also a **gate** via `breakout_vol`, a quiet close stays forming and a target reached without a confirming bar abandons), breakout delta (tick-rule delta with the direction). Target = nearest prev-day pivot beyond the neckline at least one height away, else `target_mult`×height (`facts.target_basis`). A shape re-forming on a failed pattern's stop+neckline within `retest_bars` is state **retest** (`facts.retest_of/retest_until`, clock-based). `summarise()` splits by score ≥5 and by the breakout-volume tick. **v5 (19 Oct 2026):** new finders on the same lifecycle — `_head_shoulders` (shoulders within `shoulder_tol`, head beyond by `hs_head`, neckline = second trough), `_flag` (pole ≥ `pole_min` in `pole_bars`, then `flag_min..flag_max` bars of drift within half the pole; expires), `_ranges` (last three swings of each kind: flat/flat = rectangle emitting both sides, flat top + rising lows = ascending triangle, mirror = descending; `abandon_level` = far side of the box), opening-range break (first `orb_bars` bars, break by `orb_until`, created already triggered). `LABELS`, `CONTINUATION` (extension and OI checks flip), `confidence(stats, kind, score, state)` = recorded hit rate in the score bucket, served on every row as `confidence` and shown in the Conf column. `summarise(rows)` gives per-kind stats. `core/calendar.py` = copy of `ingest/tradingday.py`; `core/notify.py` = copy of `engine/notify.py` (engine keeps its own until Phase 2).

### api/ (FastAPI; imports core/)
`GET /api/health`, `GET /api/scan`, `GET /api/days` (stored sessions, `live`, `expiry`), `GET /api/replay?day&at` (as-of ranking, Redis-cached, with `top3_since`/`rank_30m_ago` from `scan_snapshots`, `last_bar`), `GET /api/instruments`, `GET /api/candles/{symbol}?day=` (1-minute + `daily_ref` + `delta_available`) and `?tf=1d&days=`, `GET /api/alerts?day=` → `{pending, alerts}` (live + replay rows with outcomes; first request per day kicks a background `_prepare_day`: replay alerts, outcomes, per-minute ranking history, warm frames), `GET /api/alerts?limit=`, `GET /api/scan-stats?days=`, `GET /api/patterns?day&at` → `{pending, rows, counts}` (settled days from the `patterns` table via `api/patterns.py`, today computed on demand and cached 60 s), `GET /api/pattern-stats?days=`, `WS /ws/scan` (also forwards `{"type":"patterns"}` when the live loop finds a new trigger). `api/live_patterns.py`: a 60 s loop in the api process (own process pool, `PATTERNS_LIVE=1`, 09:20–15:20 on trading days) writes today's rows as `source='live'` and sends one Telegram per trigger per (symbol, kind) per day. `scripts/backtest_patterns.py` (mounted at `/app/scripts`) persists replay rows for stored days and prints the stats; notes mount at `/app/notes`. Scans mounted read-only at `/app/scans`; each scan JSON carries `"side"`.

### api/desk.py + api/live_desk.py (Claude "Desk", added 18 Oct 2026)
Claude Opus 5.5 (`DESK_MODEL`) as the intraday desk via the Anthropic Python SDK, structured outputs (`output_config.format` json_schema), cached system prompts, server-side `fallbacks: "default"`. Jobs: **verdict** per pattern trigger / rule alert (take|wait|skip, confidence, 3 reasons, exit rule, book_half_at; ticket is the system's), **brief** every 15 min, **ask**, **review** at 15:40. Hard daily budget `DESK_BUDGET_INR` (Redis `desk:spend:{day}`, briefs stop at 70 %); every call stored in `desk_calls` (migration 004) with context/output/usage/cost. Loop runs in the API process ~20 s after each minute (after the pattern tick); Telegram per verdict; `desk:updated` on the WebSocket. Routes `GET /api/desk?day&at`, `POST /api/desk/ask`, `POST /api/desk/review?day`. Dashboard Desk tab (`web/desk.js` helpers, node tests). No key → desk off, nothing else changes. The model never places orders and is told what it is blind to (news, index level, order book).

### ingest/backfill.py
`docker compose run --rm ingest python backfill.py [--days 60] [--daily-days 400] [--symbols A,B] [--universe-only]`. Gap-fill only (`ON CONFLICT DO NOTHING`, daily OI write-once), rebuilds `daily_ref` with window functions, writes the `instruments` table when empty. Uses the cached token, never logs in. Kite fact: equity minute bars end 15:14 (360/day), futures 15:39.

### web/
Single static `index.html` (reference-artifact look, light/dark, IBM Plex Sans + JetBrains Mono). Tabs: **Scanner**, **Patterns**, **Desk** (`#desk`, `web/desk.js`), **Watch** (`#watch`, `web/watch.js`: multi-ticker heat board on `GET /api/watch?symbols&day&at` — `api/watch.py` builds analyse() rows + rank + 60-min spark per symbol; cards, odometer prices, position bar, event chips from the patterns/desk feeds, groups, sort with 5-minute hysteresis, detail drawer reusing `wireChart` via `panelEl()`; list in `localStorage`; chosen by Saurabh from four animatic demos on 19 Oct 2026). Two original tabs: **Scanner** and **Patterns** (`#patterns`; table of setups with quick pills, detail panel with the pattern drawn on the chart and the "Why this row" checklist; helpers in `web/patterns.js`, tests `node --test tests/web/test_patterns.mjs`). `web/notify.js` raises Mac notifications for new live alerts, picks and pattern triggers (bell button; seeded on first live data). Header: date selector, as-of clock, LIVE/REPLAY chip, Live button, risk-per-trade input, theme toggle; full-width time slider (←/→ keys). Cards carry rank stability; detail panel has sector context, position size from lot size, timeframe row 1m–1D, canvas chart with VWAP/pivots/PDH-PDL/OR/OB, volume and OI panes, crosshair readout, alert markers. Left rail: breadth, sectors, per-scan stats line, per-day alert list cut at the as-of minute with outcomes (click jumps to that minute). A **view filter** strip (sector, price band, RVOL ≥, R:R ≥, OI build-up only, VWAP with direction, picks only) lives in `web/filter.js` as pure functions, persisted in `localStorage`, applied to cards and alerts only (never to the ranking, engine or Telegram); tests: `node --test tests/web/test_filter.mjs`. Dev loop without the dashboard password: SSH tunnel `-L 18000:127.0.0.1:8000` + a static server that proxies `/api` (see the plan's Task 8 notes).

### screens/ (added 20 Oct 2026)
Daily-timeframe screens, Chartink translations: `core/screens.py` (`evaluate(screen, daily)`, sides d/w/m + sma + mul; `basis` eod = completed candles at the previous close, the default, or intraday = forming day per minute), `api/screens.py` (`passing(con, screen, day, at)` from `candles_1d` + today's aggregate of `candles_1m`), routes `GET /api/screens`, `GET /api/screen/{name}?day&at` (Redis-cached). `screens/S0101.json` is Saurabh's NR7-downtrend screen. The Patterns tab filters by a selected screen (`localStorage` `screen`); the Watch tab has a Screen group. Mounted at `/app/screens` (compose).

### scans/
`bounce_failure_short.json` — 1:1 translation of Saurabh's Chartink scan (price > 1000, below prev close and VWAP, bar-2 bounce failing). `vwap_reclaim_long.json` — example long. `decline_below_sma13_short.json` — Saurabh's second Chartink scan (19 Oct 2026): price > 2000, last closed 5m bar at or below its 13-SMA, three falling closes.

## Deploy (VPS)

```bash
cd /opt && git clone https://github.com/heliolabsx-ai/ScanRadar.git scanradar && cd scanradar
cp .env.example .env && nano .env         # Kite key/secret, user/password/TOTP, Telegram, Postgres pw, DASH_HASH
docker run --rm caddy:2.8-alpine caddy hash-password --plaintext 'pw'   # → DASH_HASH
docker compose up -d --build
docker compose logs -f ingest
```
Verify: `https://scanradar.tech/api/health` → `{"ingest":"ready","ws":"connected"}` in market hours. Full walkthrough in `README.md`.

## Status (17 Oct 2026)

- Code committed and pushed to GitHub (17 Oct 2026). Engine smoke-tested on synthetic candles. **Not yet run against live Kite data.**
- Ingest first-day review done against kiteconnect 5.0.1 source: tick keys `last_price`/`volume_traded` (QUOTE) and `oi` (FULL) confirmed; fixed historical pacing (two calls per symbol were sharing one sleep), lost-token-on-unreachable-redirect, TOTP reuse on retry, reactor restart across days, pre-open/post-close ticks, last-candle persistence, `$` in `DASH_HASH` breaking compose interpolation (must be single-quoted in `.env`), Caddy 2.8 `basic_auth` rename. Still unverified live: the exact JSON of Kite's `/api/login` + `/api/twofa`, and whether the Connect redirect chain is 2 or 3 hops — `preflight.py` is the first thing to run on the VPS.
- DNS done 17 Oct 2026: `@` A → 200.234.37.207, `www` CNAME → scanradar.tech (Hostinger nameservers, zone edited in hPanel).
- VPS 17 Oct 2026: it was not empty — a Traefik proxy on 80/443, a Node app named `ingest` (`/docker/ingest`) and an Ollama container from Hostinger's Docker Manager were running. Saurabh chose to retire them (`docker compose -p <name> down`, volumes kept). Repo cloned to `/opt/scanradar`; `.env` is written by `configure-env.sh` (interactive, single-quoted values; `--set VAR` re-enters one secret). Saurabh works through Hostinger's browser terminal and tends to paste command blocks literally, so never put placeholder values inside runnable blocks. Claude Code on Saurabh's Mac has root SSH to the VPS via `ssh scanradar-vps` (key `~/.ssh/scanradar_vps`, authorised 17 Oct 2026).
- **Deployed 17 Oct 2026 (Saturday).** `preflight.py` passed: TOTP login OK, universe 213 stock futures (Oct-27 expiry, 29 unmapped sectors), historical daily candles with OI OK. Full stack up with Let's Encrypt certs for both hostnames. Telegram not configured yet (alerts log only). WebSocket/OI-on-ticks still unverified: first chance is Monday 19 Oct 2026 — run `docker compose run --rm ingest python preflight.py` during market hours (safe: reuses the cached token) and watch `docker compose logs -f ingest`.
- Kite login facts learned live: the account needed **External 2FA TOTP enabled and verified** (the login response's `twofa_types` must contain `totp`; the Kite-app `app_code` mode cannot be automated); Zerodha locks after 5 wrong codes, so `kite_auth` never retries a 4xx; the Connect flow shows `/connect/authorize` once per app until the user clicks Authorize in a browser (done for this api_key); the redirect chain is `/connect/login` → `/connect/finish` → callback URL with `request_token`.
- A browser-only demo of the dashboard on simulated data exists as a Claude artifact (reference for look & behaviour).

- **18 Oct 2026:** Patterns tab and the Claude Desk built on `feat/patterns`, deployed on the VPS from that branch (merge to `main` pending Saurabh's call). `ANTHROPIC_API_KEY` set on the VPS by Saurabh (rotate the key after the first live day). First real verdict on 16 Oct data: Opus 5.5, 9 s, ₹2.16 (cache write; reads are cheaper). Facts learned: structured-output schemas accept only a subset (no `minimum`/`maximum`/`maxLength`/`minItems`; keep lengths in the prompt); `fallbacks: "default"` goes through `extra_body` with the `server-side-fallback-2026-07-01` header; at ₹2,000 risk most F&O lots size to 0, so the cards show the one-lot risk.

- **19 Oct 2026, first live day:** ingest clean all day (375 minutes × 213 symbols, OI on every candle, preflight confirmed OI on ticks). Engine rule scans crashed 09:16–09:33 (forming bar made `ts` object dtype; fixed). Desk: 39 verdicts, briefs failed 09:45–13:15 on `max_tokens` (thinking shares the limit; limits raised), spend ≈ ₹107 incl. the refunded waste. Patterns v4 then v5 shipped the same day; stored days re-backtested under v5. Web helper scripts are cache-busted with `?v=<hash>` in `web/index.html` — **bump the token whenever a helper .js changes.** `scripts/day_report.py <day>` prints the facts for the end-of-day review; `scripts/backtest_scan.py --scan NAME` backtests a rule scan.

- **20 Oct 2026 (Dussehra, NSE closed):** the socket connected and no tick arrived all day; the page served Monday's last snapshot as "Tue 20 Oct LIVE" for hours before anyone noticed. Fixed 21 Oct: ingest `watchdog()` reports a silent feed once (log, `ingest:status`, Telegram), `scan:latest` expires after 30 min, `/api/scan` carries `stale`, the status chip reads STALE, `/api/days.live` needs a fresh `live:ts`. `NSE_HOLIDAYS` in `.env` is the proper fix per holiday; `core/calendar.py`/`ingest/tradingday.py` read it.

## To-do / roadmap (in priority order)

1. First live run; fix whatever breaks in ingest. Confirm OI arrives on futures ticks and prev-day OI loads via `historical_data(..., oi=True)`.
2. Dashboard: rule-alert tab, scan builder UI (Chartink-style), light theme.
3. **Self-grading**: for every alert and every "picked" stock, record price at +15/+30/+60 min and whether R1/S1 target or stop hit first; show hit-rate per scan on the card.
4. **Replay / backtest**: run any scan over stored `candles_1m` for N days; report fire count and forward returns.
5. Sector rotation detection (rank changes over rolling 30 min), relative strength vs NIFTY, multi-timeframe alignment, option-chain OI (PCR, max-pain) — ideas discussed, not designed.
6. Paper-trade log from the dashboard. No live order placement unless explicitly asked (SEBI retail-algo rules apply; static IP already whitelisted if ever needed).

## Conventions

- Python, FastAPI, pandas; keep broker-specific code inside `ingest/` behind the same interface so Upstox can be swapped in.
- All times IST; candle `ts` is the minute **start**.
- Secrets only in `.env`; never log tokens.
- Prefer editing existing modules over adding services; the VPS is small.
