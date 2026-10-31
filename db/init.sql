CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS instruments (
  symbol      TEXT PRIMARY KEY,
  eq_token    BIGINT NOT NULL,
  fut_token   BIGINT NOT NULL,
  fut_symbol  TEXT NOT NULL,
  expiry      DATE NOT NULL,
  lot_size    INT,
  sector      TEXT,
  updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS candles_1m (
  ts      TIMESTAMPTZ NOT NULL,
  symbol  TEXT NOT NULL,
  open    DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,
  volume  BIGINT,
  oi      BIGINT,
  delta   BIGINT,
  PRIMARY KEY (symbol, ts)
);
SELECT create_hypertable('candles_1m', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS daily_ref (
  day        DATE NOT NULL,
  symbol     TEXT NOT NULL,
  prev_close DOUBLE PRECISION, prev_high DOUBLE PRECISION, prev_low DOUBLE PRECISION,
  prev_oi    BIGINT,
  avg_vol_1m DOUBLE PRECISION,
  PRIMARY KEY (day, symbol)
);

CREATE TABLE IF NOT EXISTS alerts (
  id        BIGSERIAL PRIMARY KEY,
  ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
  scan      TEXT NOT NULL,
  symbol    TEXT NOT NULL,
  price     DOUBLE PRECISION,
  payload   JSONB
);
CREATE INDEX IF NOT EXISTS alerts_ts ON alerts (ts DESC);

CREATE TABLE IF NOT EXISTS scan_snapshots (
  ts      TIMESTAMPTZ NOT NULL,
  symbol  TEXT NOT NULL,
  score   DOUBLE PRECISION,
  rank    INT,
  payload JSONB,
  PRIMARY KEY (symbol, ts)
);
SELECT create_hypertable('scan_snapshots', 'ts', if_not_exists => TRUE);

-- 001_history (kept in sync with db/migrations/001_history.sql)
CREATE TABLE IF NOT EXISTS candles_1d (
  day     DATE NOT NULL,
  symbol  TEXT NOT NULL,
  open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,
  volume  BIGINT,
  oi      BIGINT,
  PRIMARY KEY (symbol, day)
);
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'live';
CREATE INDEX IF NOT EXISTS alerts_scan_ts ON alerts (scan, ts);

-- 002_patterns (kept in sync with db/migrations/002_patterns.sql)
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

-- 003_patterns_facts
ALTER TABLE patterns ADD COLUMN IF NOT EXISTS facts JSONB;

-- 005_trades (kept in sync with db/migrations/005_trades.sql)
CREATE TABLE IF NOT EXISTS trade_rows (
  id          BIGSERIAL PRIMARY KEY,
  day         DATE NOT NULL,
  ts          TIMESTAMPTZ NOT NULL,                -- trigger minute
  ref         TEXT NOT NULL,                       -- pattern:SYM|kind|formed_at · alert:ID (same refs as desk_calls)
  symbol      TEXT NOT NULL,
  side        TEXT NOT NULL,
  source      TEXT NOT NULL,                       -- pattern | alert
  entry       DOUBLE PRECISION,
  stop        DOUBLE PRECISION,
  target      DOUBLE PRECISION,                    -- T1 for patterns, the scan's target for alerts
  target2     DOUBLE PRECISION,                    -- the pattern's pivot target (runner, not graded)
  rr          DOUBLE PRECISION,
  odds        JSONB,                               -- replay output + past counts + verdict word
  size        JSONB,                               -- shares, risk_inr, position_inr, limited_by, settings snapshot
  gate        TEXT,
  gate_reason TEXT,
  outcome     JSONB,                               -- core.gate.settle: outcome + exit_px, pnl_inr, settled_at
  paper       JSONB,                               -- {logged_at, note} when logged as a paper trade
  UNIQUE (day, ref)
);
CREATE INDEX IF NOT EXISTS trade_rows_day ON trade_rows (day, ts);

CREATE TABLE IF NOT EXISTS trade_settings (
  key        TEXT PRIMARY KEY,                     -- one row 'current'
  value      JSONB,
  updated_at TIMESTAMPTZ
);
