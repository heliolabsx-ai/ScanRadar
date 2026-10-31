-- 005_trades: trade rows (odds, size, gate, outcome, paper log per trigger) and the trade settings. Idempotent.
-- Apply: docker compose exec -T postgres psql -U scanner -d scanner -f /migrations/005_trades.sql

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
