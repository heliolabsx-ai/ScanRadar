-- 002_patterns: double/triple bottom and top setups per symbol-day. Idempotent.
-- Apply: docker compose exec -T postgres psql -U scanner -d scanner -f /migrations/002_patterns.sql

CREATE TABLE IF NOT EXISTS patterns (
  id            BIGSERIAL PRIMARY KEY,
  day           DATE NOT NULL,
  symbol        TEXT NOT NULL,
  kind          TEXT NOT NULL,                     -- double_bottom | triple_bottom | double_top | triple_top
  tf            TEXT NOT NULL DEFAULT '5m',
  side          TEXT NOT NULL,                     -- long | short
  formed_at     TIMESTAMPTZ NOT NULL,
  triggered_at  TIMESTAMPTZ,
  failed_at     TIMESTAMPTZ,
  abandoned_at  TIMESTAMPTZ,
  superseded_at TIMESTAMPTZ,                       -- double extended into a triple at this minute
  neckline      DOUBLE PRECISION,
  entry         DOUBLE PRECISION,
  stop          DOUBLE PRECISION,
  target        DOUBLE PRECISION,
  points        JSONB NOT NULL,
  checks        JSONB,
  score         INT,
  outcome       JSONB,
  source        TEXT NOT NULL DEFAULT 'replay',    -- replay | live
  detector      TEXT NOT NULL,                     -- version + params hash
  UNIQUE (day, symbol, kind, formed_at)
);
CREATE INDEX IF NOT EXISTS patterns_day ON patterns (day);
CREATE INDEX IF NOT EXISTS patterns_symbol_day ON patterns (symbol, day);
