-- Independent pure-long strong-stock paper trading and point-in-time ranking ledger.
CREATE TABLE IF NOT EXISTS strong_stock_settings (
  user_id VARCHAR(80) PRIMARY KEY, paper_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  config_json TEXT NOT NULL DEFAULT '{}', updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS strong_stock_strategy_versions (
  id BIGSERIAL PRIMARY KEY, strategy_id VARCHAR(60) NOT NULL, version VARCHAR(30) NOT NULL,
  parameters_json TEXT NOT NULL DEFAULT '{}', data_requirements_json TEXT NOT NULL DEFAULT '{}',
  status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  activated_at TIMESTAMPTZ, disabled_at TIMESTAMPTZ,
  CONSTRAINT uq_strong_stock_strategy_version UNIQUE(strategy_id, version)
);
CREATE TABLE IF NOT EXISTS strong_stock_accounts (
  user_id VARCHAR(80) PRIMARY KEY, mode VARCHAR(20) NOT NULL DEFAULT 'PAPER',
  initial_capital NUMERIC(20,2) NOT NULL DEFAULT 3000000, cash NUMERIC(20,2) NOT NULL DEFAULT 3000000,
  realized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0, trading_paused BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS strong_stock_data_runs (
  id VARCHAR(80) PRIMARY KEY, trade_date DATE NOT NULL, status VARCHAR(30) NOT NULL,
  source_json TEXT NOT NULL DEFAULT '{}', missing_json TEXT NOT NULL DEFAULT '[]', error_message TEXT NOT NULL DEFAULT '',
  started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_strong_stock_data_run_date ON strong_stock_data_runs(trade_date, status);
CREATE TABLE IF NOT EXISTS strong_stock_market_regimes (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL UNIQUE, regime VARCHAR(30) NOT NULL,
  label VARCHAR(40) NOT NULL, confidence NUMERIC(12,4) NOT NULL, suggested_exposure_pct NUMERIC(12,4) NOT NULL,
  reasons_json TEXT NOT NULL DEFAULT '[]', source_snapshot_json TEXT NOT NULL DEFAULT '{}',
  calculated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS strong_stock_industry_rankings (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL, industry VARCHAR(100) NOT NULL,
  score NUMERIC(12,4) NOT NULL, rank INTEGER NOT NULL, percentile NUMERIC(12,4) NOT NULL,
  member_count INTEGER NOT NULL DEFAULT 0, details_json TEXT NOT NULL DEFAULT '{}', calculated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_strong_stock_industry_day UNIQUE(trade_date, industry)
);
CREATE INDEX IF NOT EXISTS ix_strong_stock_industry_rank ON strong_stock_industry_rankings(trade_date, rank);
CREATE TABLE IF NOT EXISTS strong_stock_rankings (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL, symbol VARCHAR(12) NOT NULL, name VARCHAR(80) NOT NULL,
  market VARCHAR(20) NOT NULL, industry VARCHAR(100) NOT NULL, rank INTEGER NOT NULL,
  total_score NUMERIC(12,4) NOT NULL, relative_strength_score NUMERIC(12,4) NOT NULL,
  trend_score NUMERIC(12,4) NOT NULL, industry_score NUMERIC(12,4) NOT NULL,
  volume_chip_score NUMERIC(12,4) NOT NULL, fundamental_score NUMERIC(12,4) NOT NULL,
  valuation_risk_score NUMERIC(12,4) NOT NULL, data_completeness NUMERIC(12,4) NOT NULL,
  close_price NUMERIC(20,4) NOT NULL, high_52w_distance_pct NUMERIC(12,4),
  entry_low NUMERIC(20,4), entry_high NUMERIC(20,4), breakout_price NUMERIC(20,4), pullback_price NUMERIC(20,4),
  stop_price NUMERIC(20,4), add_price NUMERIC(20,4), risk_reward NUMERIC(12,4),
  suggested_capital NUMERIC(20,2) NOT NULL DEFAULT 0, status VARCHAR(30) NOT NULL, entry_type VARCHAR(30) NOT NULL DEFAULT 'WATCH',
  reasons_json TEXT NOT NULL DEFAULT '[]', blocked_reasons_json TEXT NOT NULL DEFAULT '[]', score_details_json TEXT NOT NULL DEFAULT '{}',
  source_snapshot_json TEXT NOT NULL DEFAULT '{}', strategy_version VARCHAR(30) NOT NULL,
  calculated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_strong_stock_ranking_day UNIQUE(trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS ix_strong_stock_rank ON strong_stock_rankings(trade_date, rank);
CREATE TABLE IF NOT EXISTS strong_stock_orders (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, signal_key VARCHAR(160) NOT NULL UNIQUE,
  symbol VARCHAR(12) NOT NULL, name VARCHAR(80) NOT NULL, side VARCHAR(10) NOT NULL DEFAULT 'BUY',
  order_type VARCHAR(20) NOT NULL DEFAULT 'LIMIT', limit_price NUMERIC(20,4) NOT NULL,
  quantity INTEGER NOT NULL, filled_quantity INTEGER NOT NULL DEFAULT 0, status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
  entry_type VARCHAR(30) NOT NULL, tranche_pct INTEGER NOT NULL DEFAULT 30, strategy_version VARCHAR(30) NOT NULL,
  reason TEXT NOT NULL, valid_date DATE NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_strong_stock_order_user_status ON strong_stock_orders(user_id, status);
CREATE TABLE IF NOT EXISTS strong_stock_positions (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, symbol VARCHAR(12) NOT NULL, name VARCHAR(80) NOT NULL,
  industry VARCHAR(100) NOT NULL, quantity INTEGER NOT NULL, average_cost NUMERIC(20,4) NOT NULL,
  current_price NUMERIC(20,4) NOT NULL, initial_stop NUMERIC(20,4) NOT NULL, trailing_stop NUMERIC(20,4) NOT NULL,
  next_add_price NUMERIC(20,4), invested_capital NUMERIC(20,2) NOT NULL, initial_risk NUMERIC(20,2) NOT NULL,
  current_score NUMERIC(12,4) NOT NULL, entry_type VARCHAR(30) NOT NULL, strategy_version VARCHAR(30) NOT NULL,
  tranches_json TEXT NOT NULL DEFAULT '[]', reasons_json TEXT NOT NULL DEFAULT '[]', warnings_json TEXT NOT NULL DEFAULT '[]',
  status VARCHAR(20) NOT NULL DEFAULT 'OPEN', entry_at TIMESTAMPTZ NOT NULL, exit_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_strong_stock_position_user ON strong_stock_positions(user_id, status);
CREATE TABLE IF NOT EXISTS strong_stock_trades (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, position_id VARCHAR(80) NOT NULL,
  symbol VARCHAR(12) NOT NULL, name VARCHAR(80) NOT NULL, industry VARCHAR(100) NOT NULL, entry_type VARCHAR(30) NOT NULL,
  quantity INTEGER NOT NULL, entry_price NUMERIC(20,4) NOT NULL, exit_price NUMERIC(20,4) NOT NULL,
  entry_at TIMESTAMPTZ NOT NULL, exit_at TIMESTAMPTZ NOT NULL, gross_pnl NUMERIC(20,2) NOT NULL,
  buy_fee NUMERIC(20,2) NOT NULL, sell_fee NUMERIC(20,2) NOT NULL, tax NUMERIC(20,2) NOT NULL,
  slippage NUMERIC(20,2) NOT NULL, net_pnl NUMERIC(20,2) NOT NULL, return_pct NUMERIC(12,4) NOT NULL,
  strategy_version VARCHAR(30) NOT NULL, entry_reason TEXT NOT NULL, exit_reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_strong_stock_trade_user_exit ON strong_stock_trades(user_id, exit_at);
CREATE TABLE IF NOT EXISTS strong_stock_equity_snapshots (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, trade_date DATE NOT NULL,
  cash NUMERIC(20,2) NOT NULL, market_value NUMERIC(20,2) NOT NULL, total_equity NUMERIC(20,2) NOT NULL,
  daily_pnl NUMERIC(20,2) NOT NULL, drawdown_pct NUMERIC(12,4) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_strong_stock_equity_day UNIQUE(user_id, trade_date)
);
CREATE TABLE IF NOT EXISTS strong_stock_notifications (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, event_key VARCHAR(160) NOT NULL,
  event_type VARCHAR(50) NOT NULL, title VARCHAR(180) NOT NULL, message TEXT NOT NULL,
  priority VARCHAR(20) NOT NULL DEFAULT 'NORMAL', read BOOLEAN NOT NULL DEFAULT FALSE,
  email_sent BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_strong_stock_notification_event UNIQUE(user_id, event_key)
);
CREATE TABLE IF NOT EXISTS strong_stock_backtest_jobs (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL,
  status VARCHAR(30) NOT NULL, data_status_json TEXT NOT NULL DEFAULT '{}', request_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}', error_message TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_strong_stock_backtest_user ON strong_stock_backtest_jobs(user_id);
CREATE TABLE IF NOT EXISTS strong_stock_audit_events (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, action VARCHAR(80) NOT NULL,
  entity_type VARCHAR(50) NOT NULL, entity_id VARCHAR(100) NOT NULL DEFAULT '', details_json TEXT NOT NULL DEFAULT '{}',
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
