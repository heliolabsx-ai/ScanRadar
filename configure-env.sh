#!/usr/bin/env bash
# Interactive .env writer for ScanRadar. Run on the VPS from the repo root:
#
#     bash configure-env.sh
#
# Asks for every value (secrets are not echoed), generates the Caddy bcrypt hash
# for the dashboard password, and writes .env with the quoting docker compose
# needs. Re-running shows the current non-secret values as defaults; press
# Enter to keep them. Secrets must be re-entered each run.
set -euo pipefail
cd "$(dirname "$0")"
ENV_FILE=.env

cur() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d "'" || true; }

ask() {            # ask VAR "label" [default]
    local var=$1 label=$2 def=${3:-} v
    while true; do
        if [ -n "$def" ]; then read -r -p "$label [$def]: " v; v=${v:-$def}; else read -r -p "$label: " v; fi
        [ -n "$v" ] && break
        echo "  required"
    done
    printf -v "$var" '%s' "$v"
}

ask_secret() {     # ask_secret VAR "label"
    local var=$1 label=$2 v
    while true; do
        read -r -s -p "$label: " v; echo
        [ -n "$v" ] && break
        echo "  required"
    done
    printf -v "$var" '%s' "$v"
}

check_quote() {    # single-quoted dotenv values cannot contain a single quote
    case "$2" in *"'"*) echo "ERROR: $1 contains a single quote ('), which .env cannot store. Choose another value."; exit 1;; esac
}

command -v docker >/dev/null || { echo "docker not found; run this on the VPS"; exit 1; }

echo "== Kite Connect (developers.kite.trade -> your app)"
ask         KITE_API_KEY     "KITE_API_KEY"     "$(cur KITE_API_KEY | grep -v '^xxx' || true)"
ask_secret  KITE_API_SECRET  "KITE_API_SECRET (hidden)"
echo
echo "== Kite login (your Zerodha credentials; used only by the 08:55 auto-login)"
ask         KITE_USER_ID     "KITE_USER_ID (e.g. AB1234)" "$(cur KITE_USER_ID | grep -v '^AB1234$' || true)"
ask_secret  KITE_PASSWORD    "KITE_PASSWORD (hidden)"
ask_secret  KITE_TOTP_SECRET "KITE_TOTP_SECRET base32 key from Kite > Password & security > External 2FA TOTP (hidden)"
KITE_TOTP_SECRET=$(echo "$KITE_TOTP_SECRET" | tr -d ' ' | tr '[:lower:]' '[:upper:]')
case "$KITE_TOTP_SECRET" in *[!A-Z2-7=]*) echo "ERROR: TOTP secret must be base32 (A-Z, 2-7)"; exit 1;; esac
echo
echo "== Telegram (optional: press Enter to skip; alerts are then only logged by the engine)"
read -r -s -p "TELEGRAM_BOT_TOKEN from @BotFather (hidden, Enter = skip): " TELEGRAM_BOT_TOKEN; echo
TELEGRAM_CHAT_ID=""
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
    ask TELEGRAM_CHAT_ID "TELEGRAM_CHAT_ID" "$(cur TELEGRAM_CHAT_ID | grep -v '^123456789$' || true)"
fi
echo
echo "== Postgres"
PG_EXISTING=$(cur POSTGRES_PASSWORD | grep -v '^change-me$' || true)
if [ -n "$PG_EXISTING" ]; then
    ask     POSTGRES_PASSWORD "POSTGRES_PASSWORD (Enter = keep current)" "$PG_EXISTING"
else
    ask     POSTGRES_PASSWORD "POSTGRES_PASSWORD (Enter = generated)" "$(openssl rand -hex 16 2>/dev/null || head -c 24 /dev/urandom | base64 | tr -d '/+=')"
fi
if docker volume ls -q 2>/dev/null | grep -qx scanradar_pgdata && [ -n "$PG_EXISTING" ] && [ "$PG_EXISTING" != "$POSTGRES_PASSWORD" ]; then
    echo "NOTE: a Postgres volume already exists; a changed password only applies after 'docker compose down -v' (destroys stored candles)."
fi
echo
echo "== Dashboard basic auth"
ask         DASH_USER "DASH_USER (login name for the dashboard, not a password)" "$(cur DASH_USER)"
[ -n "$DASH_USER" ] || DASH_USER=saurabh
while true; do
    ask_secret DASH_PW  "Dashboard password (hidden)"
    ask_secret DASH_PW2 "Repeat dashboard password"
    [ "$DASH_PW" = "$DASH_PW2" ] && break
    echo "  passwords differ, try again"
done
echo "hashing dashboard password..."
DASH_HASH=$(docker run --rm caddy:2.8-alpine caddy hash-password --plaintext "$DASH_PW")
unset DASH_PW DASH_PW2

for k in KITE_API_KEY KITE_API_SECRET KITE_USER_ID KITE_PASSWORD KITE_TOTP_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID POSTGRES_PASSWORD DASH_USER DASH_HASH; do
    check_quote "$k" "${!k}"
done

umask 077
cat > "$ENV_FILE" <<EOF
# Written by configure-env.sh on $(date '+%Y-%m-%d %H:%M %Z'). Values are single-quoted so docker compose takes them literally.
KITE_API_KEY='$KITE_API_KEY'
KITE_API_SECRET='$KITE_API_SECRET'
KITE_USER_ID='$KITE_USER_ID'
KITE_PASSWORD='$KITE_PASSWORD'
KITE_TOTP_SECRET='$KITE_TOTP_SECRET'

TELEGRAM_BOT_TOKEN='$TELEGRAM_BOT_TOKEN'
TELEGRAM_CHAT_ID='$TELEGRAM_CHAT_ID'

POSTGRES_PASSWORD='$POSTGRES_PASSWORD'
REDIS_URL='redis://redis:6379/0'
DATABASE_URL='postgresql://scanner:$POSTGRES_PASSWORD@postgres:5432/scanner'
TZ='Asia/Kolkata'

DASH_USER='$DASH_USER'
DASH_HASH='$DASH_HASH'
EOF
chmod 600 "$ENV_FILE"

echo
echo "wrote $ENV_FILE (mode 600):"
echo "  KITE_API_KEY=$KITE_API_KEY  KITE_USER_ID=$KITE_USER_ID  TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID  DASH_USER=$DASH_USER"
echo "  secrets: set (not shown)"
echo
echo "next:"
echo "  docker compose up -d --build postgres redis && docker compose build ingest && docker compose run --rm ingest python preflight.py"
