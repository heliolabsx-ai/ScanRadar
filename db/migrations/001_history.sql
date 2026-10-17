-- 001_history: daily candles, alert provenance. Idempotent; safe to re-run.
-- Apply: docker compose exec -T postgres psql -U scanner -d scanner -f /migrations/001_history.sql

CREATE TABLE IF NOT EXISTS candles_1d (
  day     DATE NOT NULL,
  symbol  TEXT NOT NULL,
  open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,
  volume  BIGINT,
  oi      BIGINT,
  PRIMARY KEY (symbol, day)
);

ALTER TABLE alerts ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'live';   -- 'live' | 'replay'
CREATE INDEX IF NOT EXISTS alerts_scan_ts ON alerts (scan, ts);
