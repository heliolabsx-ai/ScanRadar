-- 006_alarms: Telegram deliveries (did the alarm ring) and the NIFTY 50 minute bars for the Alarms board.
-- Apply: docker compose exec -T postgres psql -U scanner -d scanner < db/migrations/006_alarms.sql
CREATE TABLE IF NOT EXISTS deliveries (
  id       BIGSERIAL PRIMARY KEY,
  day      DATE NOT NULL,
  ref      TEXT NOT NULL,            -- alert:ID · pattern:SYM|kind|formed_at (as in desk_calls / trade_rows)
  channel  TEXT NOT NULL,            -- telegram (the alarm's own message) · telegram-desk (the desk ticket)
  sent_at  TIMESTAMPTZ NOT NULL,
  ok       BOOLEAN NOT NULL,
  error    TEXT,                     -- 'not configured' when the token/chat id are empty
  silent   BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS deliveries_day_ref ON deliveries (day, ref);
CREATE TABLE IF NOT EXISTS index_1m (
  ts      TIMESTAMPTZ NOT NULL,
  symbol  TEXT NOT NULL,             -- NIFTY50
  open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,
  PRIMARY KEY (symbol, ts)
);
