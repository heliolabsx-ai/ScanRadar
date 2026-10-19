# ScanRadar — deploy guide

Intraday NSE F&O scanner: Kite Connect WebSocket → 1-min candles with OI → ranking scanner + Chartink-style rule scans → Telegram alerts → dashboard at https://scanradar.tech.

```
ingest/   Kite auto-login (TOTP), universe build, tick→candle, Postgres + Redis, preflight.py
engine/   analytics, ranking, rule engine, Telegram
api/      FastAPI: /api/scan, /api/candles/{sym}, /api/alerts, /ws/scan
web/      dashboard (static)
scans/    rule scans as JSON — drop a file in, no restart needed
db/       TimescaleDB schema
```

## 1. DNS (do this first — certificates need it)

As of 17 Oct 2026 `scanradar.tech` is still on Hostinger's parking nameservers
(`horizon.dns-parking.com` / `orbit.dns-parking.com`) and resolves to the parking
IP `2.57.91.91`. In **hPanel → Domains → scanradar.tech → DNS / Name Servers**
edit the zone (keep Hostinger's nameservers, just change the records):

| Type | Name | Value | Action |
|---|---|---|---|
| A | @ | 200.234.37.207 | replace the parking A record |
| A | www | 200.234.37.207 | replace / add |
| CNAME / A | www → parking | — | delete if present |
| TXT | @ | `v=spf1 -all` | optional, no mail from this domain |

Check from anywhere; proceed only when both return the VPS IP:

```bash
dig +short scanradar.tech A; dig +short www.scanradar.tech A
```

Caddy will retry certificate issuance on its own, so starting the stack before
DNS propagates is harmless, but the dashboard stays unreachable until then.

## 2. VPS prep (once)

```bash
ssh root@200.234.37.207
timedatectl set-timezone Asia/Kolkata
apt update && apt install -y ufw git
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
docker --version && docker compose version   # pre-installed on the Hostinger Docker image; otherwise: curl -fsSL https://get.docker.com | sh
```

Harden SSH: in `/etc/ssh/sshd_config` set `PasswordAuthentication no` after you've added your key, then `systemctl restart ssh`.

## 3. Deploy

```bash
mkdir -p /opt && cd /opt
git clone https://github.com/heliolabsx-ai/ScanRadar.git scanradar   # private repo: use a GitHub PAT or deploy key
cd /opt/scanradar
bash configure-env.sh      # interactive: asks for every value, hashes the dashboard password, writes .env
```

`configure-env.sh` single-quotes every value (the bcrypt hash contains `$`, which
docker compose would otherwise try to interpolate) and sets mode 600. To edit by
hand instead: `cp .env.example .env && nano .env`, and generate the hash with
`docker run --rm caddy:2.8-alpine caddy hash-password --plaintext '<password>'`.

Kite TOTP secret: Kite web → My profile → Password & security → External 2FA TOTP → *Can't scan? Copy the key*. If you already use an authenticator app, you must re-set up TOTP to see the key again (your phone app keeps working; add the same key there).

Telegram: message @BotFather → `/newbot` → copy the token. Start a chat with your bot, send "hi", then open `https://api.telegram.org/bot<TOKEN>/getUpdates` to read your `chat.id`.

Bring up the data stores, then run the preflight **before** starting the scheduler:

```bash
docker compose up -d --build postgres redis
docker compose build ingest
docker compose run --rm ingest python preflight.py
```

The preflight logs in, builds the universe, pulls daily candles with OI for three
symbols and (during market hours) samples the WebSocket for 20 s to confirm that
futures ticks carry OI and equity ticks carry volume. Fix anything it reports,
then start everything:

```bash
docker compose up -d --build
docker compose logs -f ingest      # watch login + universe + ref load
```

First run on a weekday between 08:55 and 15:30 triggers setup immediately; otherwise it waits for 08:55 next trading day.

## 4. Verify

- `https://scanradar.tech/api/health` → `{"ingest":"ready","ws":"connected",...}` during market hours. While setup runs `ingest` reads `logging in` → `building universe` → `loading daily refs` → `ready`; on failure it reads `error: ...`.
- `docker compose logs ingest` should show `Kite login OK`, `universe: ~200 F&O stocks`, `daily refs: N/N symbols loaded, 0 without prev-day OI`, `ws connected; subscribed ...`, then `flush: N candles persisted` every minute.
- `https://scanradar.tech` → dashboard (basic-auth prompt).
- Telegram: first alert arrives when any scan in `scans/` fires.

## 5. Daily lifecycle

| Time | Action |
|---|---|
| 08:55 | reuse cached token if still valid, else auto-login; instrument dump; previous-day refs (~3 min for ~200 stocks at 0.4 s/call) |
| 09:14 | WebSocket opens; equity ticks in QUOTE mode, futures in FULL mode (OI). Ticks before 09:15 and after 15:30 do not form candles |
| every minute | candles flushed; engine re-ranks and evaluates scans |
| 15:31 | last candle persisted, WebSocket closed, **ingest process exits on purpose** and Docker restarts it (the twisted reactor inside KiteTicker cannot be restarted in-process) |

Logs: `docker compose logs -f engine` shows `scan: N symbols, B bull / R bear, A alerts` each minute.

## 6. First-day runbook

| Symptom | Likely cause | Fix |
|---|---|---|
| `login failed: ...` | wrong KITE_USER_ID / KITE_PASSWORD | fix `.env`, `docker compose restart ingest` |
| `External TOTP is not enabled on this Kite account (accepts ['app_code', 'sms'])` | the account uses the Kite app's own code; a stored secret can never match | Kite web > My profile > Password & security > External 2FA TOTP > Enable, **complete the verification step**, copy the key, rerun `configure-env.sh`. Preflight step 0a shows the account's list; `totp` must be in it |
| `2FA failed: ... Invalid TOTP/App Code. N attempt(s) remain` | wrong key, or VPS clock skew | **stop**: Zerodha locks the account after 5 wrong codes. Preflight step 0b prints the code the stored key generates; it must equal the authenticator app's. `timedatectl` must show synchronised |
| `Connect login stopped redirecting: HTTP 200` | Zerodha changed the login flow (or the API key is inactive / the Kite Connect subscription lapsed) | check developers.kite.trade billing; then fix `ingest/kite_auth.py` |
| `historical ... throttled` repeatedly / refs `N without prev-day OI` | 429 from the historical API | raise `HIST_PACE` in `ingest/main.py`; refs for throttled symbols are retried 4× |
| `futures candle has no 'oi' key` (preflight) | historical add-on not active on the Kite Connect app | enable it in the developer console |
| `ws error 403` / `not started` | token invalid: another login with the same API key (e.g. the official Kite app, a second script) invalidated it | one API key = one live token; don't log in elsewhere with it, or restart ingest after 08:55 |
| `flush: 0 candles persisted` all day | no equity ticks reaching the state map | check `ws connected; subscribed` appeared and that tokens are ints |
| TLS never comes up | DNS still on parking IP | section 1 |

Manual token fallback: set `KITE_PASSWORD=` blank, then `docker compose exec redis redis-cli set kite:access_token <token>` and restart ingest; it validates the token with `profile()` and proceeds.

## 7. History and replay

The dashboard shows any stored session, not just the live one: pick a date in the
header, drag the time slider to see the ranking exactly as the scanner had it at
that minute, switch the chart between 1m/3m/5m/15m/30m/60m/1D, and read the rule
alerts for that day with what each trade did afterwards (return at 15/30/60 min,
target or stop first) plus a per-scan hit rate over the last 20 sessions.

One-time setup after deploying this version (also in the migration file header):

```bash
docker compose exec -T postgres psql -U scanner -d scanner -f /migrations/001_history.sql
```

Load the last 60 calendar days of 1-minute candles (with futures OI) and about a
year of daily bars for the whole universe. Takes about 18 minutes; safe to re-run,
it only fills gaps and never overwrites live-captured candles:

```bash
docker compose run --rm ingest python backfill.py
```

Evening top-up if the live ingest missed anything (run after 15:35):

```bash
docker compose run --rm ingest python backfill.py --days 3
```

The first time a past day is opened the API prepares it in the background (replay
alerts, outcomes, per-minute ranking history; two to four minutes) and the page
polls until it is ready. Limits: backfilled days have no tick-rule volume delta
(the panel shows "—"); futures OI before the current contract became front month
is thin, and the dashboard says so under the sector rail; NSE equities stop
printing at 15:14 in the 2026 calendar while futures run to 15:39, so the slider
parks on the last real bar.

Replay and live use the same analytics code (`core/`). The engine switches to
`core/` after the first clean live day (Phase 2 in the design doc).

## 8. Patterns tab (chart patterns with a recorded confidence)

The **Patterns** tab detects chart patterns on 5-minute bars for every stock,
obeying the same date and time slider as the Scanner. Reversal shapes: double and
triple bottoms and tops, head & shoulders and its inverse. Continuation shapes:
bull and bear flags, rectangles (range breakout / breakdown), ascending and
descending triangles, and the opening-range break (first 15 minutes, break by
10:30, created at the moment it triggers). Every shape shares one lifecycle and
one **Conf** column: the recorded hit rate of that pattern in that score bucket
over the last 42 sessions, with the sample size, so a name never carries more
weight than its own record. For continuation shapes the first two checks flip:
the day must have moved *with* the trade and OI must be building, not unwinding. A row is
**forming** once its second (or third) swing point is confirmed, **triggered**
on the first 5-minute close through the neckline that carries volume (at least
1.5× the median volume of the pattern's own bars; a quiet close through the
neckline leaves the setup forming), with entry = that close, stop = the
pattern's extreme and target = the nearest previous-day pivot beyond the
neckline that is at least one pattern height away, else 1.5× the height, so
R:R varies by setup. **Failed** = a close back through the neckline within
three bars. A shape that re-forms on a failed setup's stop and neckline within
12 bars is labelled **retest** rather than forming. The score counts eight
confirmations: the day already moved at least 1 % by the second point, OI
lower at the second point than the first, trigger volume at least 1.5× the
day's 5-minute average, the sector agreeing with the direction, the trigger
close on the right side of VWAP, exhaustion (the second point's bars traded
less than the first's), breakout volume (the tick behind the gate) and
breakout delta (tick-rule delta with the direction; zero on backfilled days).
The stats line splits the hit rate by whether the breakout-volume tick was
present. Clicking a row draws the points, neckline, trigger bar and
stop/target zones on the chart, and the panel lists the checks.

Rows for stored days come from the `patterns` table. Populate it (and print the
hit rates) with:

```bash
docker compose run --rm -T api python scripts/backtest_patterns.py --days 60 --note /app/notes/patterns-backtest.md
```

Detector parameters live in `core/patterns.py` (`DEFAULTS`); the tuning and the
numbers behind them are in `docs/superpowers/notes/patterns-backtest-2026-10-18.md`.
Changing the parameters changes the detector hash and the next run replaces the
stored replay rows.

During the session the API process runs a live loop (every minute from 09:20
to 15:20 on trading days, `PATTERNS_LIVE=1`) that writes today's rows, sends one
Telegram per trigger per symbol and kind (`<b>Double bottom · BUY</b>` /
`SELL`, entry, stop, target, R:R, score) and pushes the page to refresh. The
Mac notification fires for new triggers when the bell is on.

Limits: 5-minute bars only; backfilled days use total volume for the volume
check; OI before the previous expiry is thin; mean returns 30 minutes after a
trigger are flat in the backtest even where the hit rate is high, so the target
is a take-profit, not a hold.

## 9. Desk tab (Claude as the intraday desk)

The **Desk** tab puts Claude on top of the scanner. It never trades and never sets
levels: entry, stop, target and size are the system's, Claude only judges.

| Job | When | What you get |
|---|---|---|
| Verdict | every pattern trigger and rule alert, ~20 s after its minute | TAKE / WAIT / SKIP, confidence 1–10, three one-line reasons (✓ ✗ !), an exit rule, optional "book half at"; the same text on Telegram and as a Mac notification |
| Brief | 09:30 and every 15 min to 15:15 | two or three paragraphs on breadth, sectors, top ranks, what the pattern desk did, and a watch list |
| Ask | any time, from the tab | an answer about the session as the slider shows it, ≤120 words |
| Review | 15:40 on a trading day, or the **Run review** button for any stored day | what the rules made at the shown size, where the desk was wrong, one thing for tomorrow, data health |

Claude sees only: the symbol's last twelve 5-minute bars with volume and OI, its
ranking row, sector average, breadth, previous-day levels, the setup's recorded
hit rate and the ticket. Every card says so (`blind to: news · index level · order book`).

Setup (once):

```bash
docker compose exec -T postgres psql -U scanner -d scanner -f /migrations/004_desk.sql
bash configure-env.sh --set ANTHROPIC_API_KEY
docker compose up -d --build api
```

Get the key at console.anthropic.com with **prepaid credit and auto-reload off**:
the credit is the outer ceiling. Inside it, `DESK_BUDGET_INR` (default ₹100 a day)
is a hard stop: briefs pause at 70 % of it, verdicts at 100 %, and the tab's meter
shows the spend. `DESK_MODEL` (default `claude-opus-5-5`; `claude-sonnet-5-5` is
about half the cost, `claude-haiku-5-5` a tenth), `DESK_MIN_SCORE` / `DESK_MIN_RR`
(default 0: every trigger gets a verdict), `DESK_RISK_INR` (sizing, default 2000),
`DESK_TELEGRAM=0` to keep verdicts off Telegram, `DESK=0` to switch the desk off.
Without a key the tab reads "desk off" and nothing else changes.

Logs: `docker compose logs -f api | grep desk` shows `desk HH:MM: {'verdicts': n, ...}`
and any API error (a wrong key shows as `AuthenticationError`, no credit as
`BadRequestError ... credit balance`). Each call is stored in `desk_calls` with
its context, output, usage and rupee cost.

## 10. Watch tab (multi-ticker board)

The **Watch** tab shows a board of tickers you choose, each as a card that fills with the
day's colour in proportion to the move, with a rolling price, a PDL · PC · VWAP · PDH
position bar, the last hour as a sparkline, the OI label, RVOL, the buy/sell share of the
tick-rule delta, and chips for the symbol's patterns (with confidence) and desk verdicts.
A new trigger or verdict flashes the card. Type a symbol to add it, ✕ removes it, and the
groups load the scanner's picks, today's triggered names or a sector. Sorting by % change,
rank, RVOL or events re-ranks every five minutes, never on every tick. Clicking a card opens
a drawer with the full chart (every timeframe, VWAP, pivots, opening range, order block,
alert markers), the levels strip, the trade at your risk setting with lot-based size, the
symbol's patterns, desk verdicts and rule alerts, and buttons into the other tabs. The
board follows the date and time slider like everything else; the list is saved in the
browser. `GET /api/watch?symbols=A,B&day=&at=` serves it, up to 30 symbols per call.

## 11. Adding a scan

Create `scans/my_scan.json` (see the two examples). Sides are `{"number": x}`, `{"series": "prev_day_close|prev_day_high|prev_day_low"}`, or `{"off": N, "field": "open|high|low|close|volume|vwap|oi"}` where `off` 0 = forming candle, 1 = last closed, etc. Add `"sma": 13` or `"ema": 20` to an `off` side for a moving average of that field ending at that bar (Chartink's `Sma(Close, 13)`). Timeframes: 1m, 3m, 5m, 15m, 30m, 60m. Give the scan its own trade with `"trade": {"stop": SIDE | {"pct": 0.5}, "target": SIDE | {"pct": 1.2} | {"rr": 2.0}}` (for example the bar-2 high as the stop of a bounce-failure short, target at twice the risk): the engine, the replay, the outcomes, the rail stats, the dashboard R:R and the desk then all use those levels; a scan without `trade`, or a stop that resolves on the wrong side of the entry, falls back to the analytics' pivot levels (`levels: "pivot"` in the alert). `"alert": {"telegram": false}` records fires without messaging; `"desk": false` keeps the scan out of Claude's verdicts. Check a new scan's record before switching either on: `docker compose run --rm -T api python scripts/backtest_scan.py --scan "Name" --days 20`. The engine reloads the folder every minute.

## 12. Known limits / to-do

- Auto-login replays Kite's browser flow; if Zerodha changes the login page, fix `ingest/kite_auth.py`.
- Volume delta uses the tick rule (uptick = buy); it is an approximation.
- Previous-day OI comes from the historical API with `oi=True`; on expiry-rollover days the nearest contract changes, so OI % that morning compares against the new contract's prior day.
- NSE holidays are not known to the scheduler: it logs in and opens the socket; nothing arrives. Harmless.
- Historical backfill / replay / backtest of scans is not included yet — the candles are stored, so it's a query away.
- Kite Connect plan: ₹500/month, top up credits in the developer console before expiry or the 08:55 login fails.
