-- 004_desk: Claude desk calls (verdicts, briefs, asks, reviews). Idempotent.
-- Apply: docker compose exec -T postgres psql -U scanner -d scanner -f /migrations/004_desk.sql

CREATE TABLE IF NOT EXISTS desk_calls (
  id         BIGSERIAL PRIMARY KEY,
  day        DATE NOT NULL,
  kind       TEXT NOT NULL,                 -- verdict | brief | ask | review
  ts         TIMESTAMPTZ NOT NULL,          -- the minute the call is about (IST)
  symbol     TEXT,
  ref        TEXT NOT NULL,                 -- pattern:SYM|kind|formed_at · alert:ID · brief:HH:MM · ask:HH:MM:SS · review
  model      TEXT,
  context    JSONB,
  output     JSONB,
  usage      JSONB,
  cost_inr   DOUBLE PRECISION DEFAULT 0,
  latency_ms INT,
  error      TEXT,
  UNIQUE (day, kind, ref)
);
CREATE INDEX IF NOT EXISTS desk_calls_day ON desk_calls (day, ts);
