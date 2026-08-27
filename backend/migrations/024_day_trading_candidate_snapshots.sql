CREATE TABLE IF NOT EXISTS day_trading_candidate_snapshots (
  id BIGSERIAL PRIMARY KEY,
  signal_id VARCHAR(100) NOT NULL,
  trading_date DATE NOT NULL,
  snapshot_at TIMESTAMPTZ NOT NULL,
  symbol VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL,
  market VARCHAR(20) NOT NULL,
  direction VARCHAR(12) NOT NULL,
  rank INTEGER NOT NULL,
  is_official_recommendation BOOLEAN NOT NULL DEFAULT FALSE,
  confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  health_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  confirmation_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  large_order_force DOUBLE PRECISION NOT NULL DEFAULT 0,
  risk_reward_ratio DOUBLE PRECISION NOT NULL DEFAULT 0,
  liquidity_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL,
  CONSTRAINT uq_day_candidate_snapshot_signal_time UNIQUE (signal_id, snapshot_at)
);

CREATE INDEX IF NOT EXISTS ix_day_candidate_snapshots_date_time
  ON day_trading_candidate_snapshots(trading_date, snapshot_at);

CREATE INDEX IF NOT EXISTS ix_day_candidate_snapshots_symbol_time
  ON day_trading_candidate_snapshots(symbol, snapshot_at);
