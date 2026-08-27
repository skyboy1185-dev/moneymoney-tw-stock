CREATE TABLE IF NOT EXISTS limit_up_ai_settings (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(80) NOT NULL UNIQUE,
  capital NUMERIC(20,2) NOT NULL DEFAULT 3000000,
  min_price NUMERIC(20,4) NOT NULL DEFAULT 20,
  max_price NUMERIC(20,4) NOT NULL DEFAULT 500,
  min_average_turnover_20d NUMERIC(20,2) NOT NULL DEFAULT 100000000,
  min_volume_ratio_20d NUMERIC(12,4) NOT NULL DEFAULT 1.8,
  first_position_pct NUMERIC(7,4) NOT NULL DEFAULT 0.10,
  max_position_pct NUMERIC(7,4) NOT NULL DEFAULT 0.20,
  max_positions INTEGER NOT NULL DEFAULT 3,
  max_loss_per_trade_pct NUMERIC(7,4) NOT NULL DEFAULT 0.005,
  max_daily_loss_pct NUMERIC(7,4) NOT NULL DEFAULT 0.01,
  max_consecutive_stops INTEGER NOT NULL DEFAULT 3,
  overnight_total_pct NUMERIC(7,4) NOT NULL DEFAULT 0.30,
  overnight_single_pct NUMERIC(7,4) NOT NULL DEFAULT 0.15,
  exclude_locked_limit_up BOOLEAN NOT NULL DEFAULT TRUE,
  sound_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS limit_up_ai_snapshots (
  id BIGSERIAL PRIMARY KEY,
  signal_id VARCHAR(120) NOT NULL,
  trading_date DATE NOT NULL,
  snapshot_at TIMESTAMPTZ NOT NULL,
  symbol VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL,
  market VARCHAR(20) NOT NULL,
  rank INTEGER NOT NULL,
  category VARCHAR(30) NOT NULL,
  setup_type VARCHAR(40) NOT NULL,
  score NUMERIC(7,2) NOT NULL,
  price NUMERIC(20,4) NOT NULL,
  change_pct NUMERIC(12,4) NOT NULL,
  limit_distance_pct NUMERIC(12,4) NOT NULL,
  payload_json TEXT NOT NULL,
  CONSTRAINT uq_limit_up_ai_snapshot_signal_time UNIQUE (signal_id, snapshot_at)
);

CREATE INDEX IF NOT EXISTS ix_limit_up_ai_snapshot_date_rank
  ON limit_up_ai_snapshots(trading_date, rank);

CREATE INDEX IF NOT EXISTS ix_limit_up_ai_snapshot_symbol_time
  ON limit_up_ai_snapshots(symbol, snapshot_at);

CREATE TABLE IF NOT EXISTS limit_up_ai_positions (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(80) NOT NULL,
  symbol VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL,
  market VARCHAR(20) NOT NULL,
  setup_type VARCHAR(40) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'open',
  entry_at TIMESTAMPTZ NOT NULL,
  exit_at TIMESTAMPTZ,
  entry_price NUMERIC(20,4) NOT NULL,
  current_price NUMERIC(20,4) NOT NULL,
  exit_price NUMERIC(20,4),
  quantity INTEGER NOT NULL,
  remaining_quantity INTEGER NOT NULL,
  stop_loss NUMERIC(20,4) NOT NULL,
  target1 NUMERIC(20,4) NOT NULL,
  target2 NUMERIC(20,4) NOT NULL,
  highest_price NUMERIC(20,4) NOT NULL,
  lowest_price NUMERIC(20,4) NOT NULL,
  take_profit_stage INTEGER NOT NULL DEFAULT 0,
  score_entry NUMERIC(7,2) NOT NULL,
  score_current NUMERIC(7,2) NOT NULL,
  overnight_score NUMERIC(7,2) NOT NULL DEFAULT 0,
  overnight_hold_pct NUMERIC(7,4) NOT NULL DEFAULT 0,
  realized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0,
  unrealized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0,
  latest_action VARCHAR(120) NOT NULL DEFAULT '模擬持有',
  payload_json TEXT NOT NULL DEFAULT '{}',
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_limit_up_ai_position_user_status
  ON limit_up_ai_positions(user_id, status);

CREATE INDEX IF NOT EXISTS ix_limit_up_ai_position_symbol
  ON limit_up_ai_positions(symbol, status);

CREATE TABLE IF NOT EXISTS limit_up_ai_trades (
  id BIGSERIAL PRIMARY KEY,
  position_id BIGINT REFERENCES limit_up_ai_positions(id),
  user_id VARCHAR(80) NOT NULL,
  symbol VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL,
  action VARCHAR(30) NOT NULL,
  setup_type VARCHAR(40) NOT NULL,
  price NUMERIC(20,4) NOT NULL,
  quantity INTEGER NOT NULL,
  gross_amount NUMERIC(20,2) NOT NULL,
  realized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0,
  reason TEXT NOT NULL,
  executed_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_limit_up_ai_trade_time
  ON limit_up_ai_trades(executed_at, symbol);
