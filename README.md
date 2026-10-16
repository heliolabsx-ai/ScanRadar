# ScanRadar — deploy guide

Intraday NSE F&O scanner: Kite Connect WebSocket → 1-min candles with OI → ranking scanner + Chartink-style rule scans → Telegram alerts → dashboard at https://scanradar.tech.

```
ingest/   Kite auto-login (TOTP), universe build, tick→candle, Postgres + Redis
engine/   analytics, ranking, rule engine, Telegram
api/      FastAPI: /api/scan, /api/candles/{sym}, /api/alerts, /ws/scan
web/      dashboard (static)
scans/    rule scans as JSON — drop a file in, no restart needed
db/       TimescaleDB schema
```

## 1. DNS (do this first — certificates need it)

At your domain registrar, add:

| Type | Name | Value |
|---|---|---|
| A | @ | 200.234.37.207 |
| A | www | 200.234.37.207 |
| TXT | @ | `v=spf1 -all` |

Check with `dig +short scanradar.tech` from anywhere; proceed when it returns the IP.

## 2. VPS prep (once)

```bash
ssh root@200.234.37.207
timedatectl set-timezone Asia/Kolkata
apt update && apt install -y ufw git
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
# Docker is pre-installed on the Hostinger Docker image; otherwise: curl -fsSL https://get.docker.com | sh
```

Harden SSH: in `/etc/ssh/sshd_config` set `PasswordAuthentication no` after you've added your key, then `systemctl restart ssh`.

## 3. Deploy

```bash
mkdir -p /opt && cd /opt
# copy the scanradar folder here (scp -r scanradar root@200.234.37.207:/opt/)
cd /opt/scanradar
cp .env.example .env
nano .env        # fill every value
```

Generate the dashboard password hash and paste it as DASH_HASH:

```bash
docker run --rm caddy:2.8-alpine caddy hash-password --plaintext 'your-dashboard-password'
```

Kite TOTP secret: Kite web → My profile → Password & security → External 2FA TOTP → *Can't scan? Copy the key*. If you already use an authenticator app, you must re-set up TOTP to see the key again (your phone app keeps working; add the same key there).

Telegram: message @BotFather → `/newbot` → copy the token. Start a chat with your bot, send "hi", then open `https://api.telegram.org/bot<TOKEN>/getUpdates` to read your `chat.id`.

```bash
docker compose up -d --build
docker compose logs -f ingest      # watch login + universe + ref load
```

First run on a weekday between 08:55 and 15:30 triggers setup immediately; otherwise it waits for 08:55 next trading day.

## 4. Verify

- `https://scanradar.tech/api/health` → `{"ingest":"ready","ws":"connected",...}` during market hours
- `https://scanradar.tech` → dashboard (basic-auth prompt)
- Telegram: first alert arrives when any scan in `scans/` fires

## 5. Daily lifecycle

| Time | Action |
|---|---|
| 08:55 | auto-login, instrument dump, previous-day refs (~3 min for ~200 stocks) |
| 09:14 | WebSocket opens; equity ticks in QUOTE mode, futures in FULL mode (OI) |
| every minute | candles flushed; engine re-ranks and evaluates scans |
| 15:31 | WebSocket closed; data stays in Postgres |

Logs: `docker compose logs -f engine` shows `scan: N symbols, B bull / R bear, A alerts` each minute.

## 6. Adding a scan

Create `scans/my_scan.json` (see the two examples). Sides are `{"number": x}`, `{"series": "prev_day_close|prev_day_high|prev_day_low"}`, or `{"off": N, "field": "open|high|low|close|volume|vwap|oi"}` where `off` 0 = forming candle, 1 = last closed, etc. Timeframes: 1m, 3m, 5m, 15m, 30m, 60m. The engine reloads the folder every minute.

## 7. Known limits / to-do

- Auto-login replays Kite's browser flow; if Zerodha changes the login page, fix `ingest/kite_auth.py`. Fallback: set `KITE_PASSWORD` blank and paste a token into Redis (`redis-cli set kite:access_token <token>`).
- Volume delta uses the tick rule (uptick = buy); it is an approximation.
- Previous-day OI comes from the historical API with `oi=True`; on expiry-rollover days the nearest contract changes, so OI % that morning compares against the new contract's prior day.
- Historical backfill / replay / backtest of scans is not included yet — the candles are stored, so it's a query away.
- Kite Connect plan: ₹500/month, top up credits in the developer console before expiry or the 08:55 login fails.
