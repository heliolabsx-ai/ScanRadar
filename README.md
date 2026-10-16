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
| `2FA failed: Invalid TOTP` on every attempt | wrong TOTP secret, or VPS clock skew | re-copy the key from Kite; `timedatectl` must show synchronised |
| `Connect login stopped redirecting: HTTP 200` | Zerodha changed the login flow (or the API key is inactive / the Kite Connect subscription lapsed) | check developers.kite.trade billing; then fix `ingest/kite_auth.py` |
| `historical ... throttled` repeatedly / refs `N without prev-day OI` | 429 from the historical API | raise `HIST_PACE` in `ingest/main.py`; refs for throttled symbols are retried 4× |
| `futures candle has no 'oi' key` (preflight) | historical add-on not active on the Kite Connect app | enable it in the developer console |
| `ws error 403` / `not started` | token invalid: another login with the same API key (e.g. the official Kite app, a second script) invalidated it | one API key = one live token; don't log in elsewhere with it, or restart ingest after 08:55 |
| `flush: 0 candles persisted` all day | no equity ticks reaching the state map | check `ws connected; subscribed` appeared and that tokens are ints |
| TLS never comes up | DNS still on parking IP | section 1 |

Manual token fallback: set `KITE_PASSWORD=` blank, then `docker compose exec redis redis-cli set kite:access_token <token>` and restart ingest; it validates the token with `profile()` and proceeds.

## 7. Adding a scan

Create `scans/my_scan.json` (see the two examples). Sides are `{"number": x}`, `{"series": "prev_day_close|prev_day_high|prev_day_low"}`, or `{"off": N, "field": "open|high|low|close|volume|vwap|oi"}` where `off` 0 = forming candle, 1 = last closed, etc. Timeframes: 1m, 3m, 5m, 15m, 30m, 60m. The engine reloads the folder every minute.

## 8. Known limits / to-do

- Auto-login replays Kite's browser flow; if Zerodha changes the login page, fix `ingest/kite_auth.py`.
- Volume delta uses the tick rule (uptick = buy); it is an approximation.
- Previous-day OI comes from the historical API with `oi=True`; on expiry-rollover days the nearest contract changes, so OI % that morning compares against the new contract's prior day.
- NSE holidays are not known to the scheduler: it logs in and opens the socket; nothing arrives. Harmless.
- Historical backfill / replay / backtest of scans is not included yet — the candles are stored, so it's a query away.
- Kite Connect plan: ₹500/month, top up credits in the developer console before expiry or the 08:55 login fails.
