CREATE TABLE IF NOT EXISTS long_term_positions (
  id BIGSERIAL PRIMARY KEY,
  entry_key VARCHAR(120) NOT NULL UNIQUE,
  portfolio_mode VARCHAR(20) NOT NULL,
  stock_code VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL,
  market_type VARCHAR(20) NOT NULL,
  industry VARCHAR(80) NOT NULL,
  direction VARCHAR(10) NOT NULL,
  model_key VARCHAR(40) NOT NULL,
  model_name VARCHAR(80) NOT NULL,
  entry_date DATE NOT NULL,
  entry_time TIMESTAMPTZ NOT NULL,
  minimum_exit_date DATE NOT NULL,
  entry_price NUMERIC(20,4) NOT NULL,
  last_price NUMERIC(20,4) NOT NULL,
  selection_score NUMERIC(7,2) NOT NULL,
  current_score NUMERIC(7,2) NOT NULL,
  predicted_month_return_pct NUMERIC(12,4) NOT NULL,
  reasons_json TEXT NOT NULL DEFAULT '[]',
  status VARCHAR(20) NOT NULL DEFAULT 'open',
  exit_date DATE,
  exit_time TIMESTAMPTZ,
  exit_price NUMERIC(20,4),
  exit_reason TEXT,
  actual_return_pct NUMERIC(12,4) NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_long_term_position_mode_status ON long_term_positions (portfolio_mode, status);
CREATE INDEX IF NOT EXISTS ix_long_term_position_entry_date ON long_term_positions (entry_date, portfolio_mode);

CREATE TABLE IF NOT EXISTS long_term_position_snapshots (
  id BIGSERIAL PRIMARY KEY,
  position_id BIGINT NOT NULL REFERENCES long_term_positions(id),
  trade_date DATE NOT NULL,
  price NUMERIC(20,4) NOT NULL,
  actual_return_pct NUMERIC(12,4) NOT NULL,
  predicted_month_return_pct NUMERIC(12,4) NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_long_term_snapshot_position_date UNIQUE (position_id, trade_date)
);
CREATE INDEX IF NOT EXISTS ix_long_term_snapshot_date ON long_term_position_snapshots (trade_date, position_id);

CREATE TABLE IF NOT EXISTS long_term_portfolio_runs (
  id BIGSERIAL PRIMARY KEY,
  portfolio_mode VARCHAR(20) NOT NULL,
  trade_date DATE NOT NULL,
  selected_count INTEGER NOT NULL DEFAULT 0,
  opened_count INTEGER NOT NULL DEFAULT 0,
  closed_count INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL DEFAULT '{}',
  ran_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_long_term_run_mode_date UNIQUE (portfolio_mode, trade_date)
);
CREATE INDEX IF NOT EXISTS ix_long_term_run_date ON long_term_portfolio_runs (trade_date, portfolio_mode);
