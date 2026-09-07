-- Unified long-only intraday system. Monetary values never use floating point.
CREATE TABLE IF NOT EXISTS day_trade_v2_settings (
  user_id VARCHAR(80) PRIMARY KEY, trade_mode VARCHAR(20) NOT NULL DEFAULT 'PAPER',
  live_enabled BOOLEAN NOT NULL DEFAULT FALSE, config_json TEXT NOT NULL DEFAULT '{}',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS day_trade_v2_robots (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, strategy_id VARCHAR(60) NOT NULL,
  name VARCHAR(100) NOT NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE, side VARCHAR(10) NOT NULL DEFAULT 'LONG',
  allocation NUMERIC(20,2) NOT NULL, consecutive_losses INTEGER NOT NULL DEFAULT 0,
  status VARCHAR(30) NOT NULL DEFAULT 'READY', status_date DATE, parameters_json TEXT NOT NULL DEFAULT '{}',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_robot_user_strategy UNIQUE(user_id,strategy_id)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_strategy_versions (
  id BIGSERIAL PRIMARY KEY, strategy_id VARCHAR(60) NOT NULL, version VARCHAR(30) NOT NULL,
  definition_json TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_strategy_version UNIQUE(strategy_id,version)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_signals (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  strategy_id VARCHAR(60) NOT NULL, strategy_version VARCHAR(30) NOT NULL, symbol VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL DEFAULT '', sector VARCHAR(100) NOT NULL DEFAULT '', side VARCHAR(10) NOT NULL DEFAULT 'LONG',
  signal_time TIMESTAMPTZ NOT NULL, signal_price NUMERIC(18,4) NOT NULL, confidence NUMERIC(12,6) NOT NULL,
  risk_reward NUMERIC(12,6) NOT NULL, stop_price NUMERIC(18,4) NOT NULL, target_price NUMERIC(18,4) NOT NULL,
  status VARCHAR(30) NOT NULL, reasons_json TEXT NOT NULL DEFAULT '[]', skip_reason TEXT NOT NULL DEFAULT '',
  market_context_json TEXT NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_dtv2_signal_user_time ON day_trade_v2_signals(user_id,signal_time);
CREATE INDEX IF NOT EXISTS ix_dtv2_signal_symbol_time ON day_trade_v2_signals(symbol,signal_time);
CREATE TABLE IF NOT EXISTS day_trade_v2_orders (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  signal_id VARCHAR(80) NOT NULL DEFAULT '', client_order_id VARCHAR(100) NOT NULL, broker_order_id VARCHAR(100) NOT NULL DEFAULT '',
  symbol VARCHAR(12) NOT NULL, side VARCHAR(10) NOT NULL, order_type VARCHAR(20) NOT NULL DEFAULT 'LIMIT',
  order_price NUMERIC(18,4) NOT NULL, order_quantity INTEGER NOT NULL, filled_quantity INTEGER NOT NULL DEFAULT 0,
  status VARCHAR(30) NOT NULL DEFAULT 'PENDING', signal_at TIMESTAMPTZ, sent_at TIMESTAMPTZ,
  broker_accepted_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  error_message TEXT NOT NULL DEFAULT '', CONSTRAINT uq_dtv2_order_client UNIQUE(user_id,client_order_id)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_fills (
  id BIGSERIAL PRIMARY KEY, order_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  broker_execution_id VARCHAR(120) NOT NULL, price NUMERIC(18,4) NOT NULL, quantity INTEGER NOT NULL,
  exchange_time TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL,
  timezone VARCHAR(40) NOT NULL DEFAULT 'Asia/Taipei',
  CONSTRAINT uq_dtv2_fill_execution UNIQUE(mode,broker_execution_id)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_positions (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  strategy_id VARCHAR(60) NOT NULL, strategy_version VARCHAR(30) NOT NULL, signal_id VARCHAR(80) NOT NULL,
  symbol VARCHAR(12) NOT NULL, stock_name VARCHAR(80) NOT NULL DEFAULT '', sector VARCHAR(100) NOT NULL DEFAULT '',
  side VARCHAR(10) NOT NULL DEFAULT 'LONG', quantity INTEGER NOT NULL,
  entry_price NUMERIC(18,4) NOT NULL, current_price NUMERIC(18,4) NOT NULL,
  stop_price NUMERIC(18,4) NOT NULL, first_target_price NUMERIC(18,4) NOT NULL,
  trailing_stop_price NUMERIC(18,4) NOT NULL, used_capital NUMERIC(20,2) NOT NULL,
  entry_time TIMESTAMPTZ NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
  entry_reasons_json TEXT NOT NULL DEFAULT '[]', confidence NUMERIC(12,6) NOT NULL DEFAULT 0,
  risk_reward NUMERIC(12,6) NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_dtv2_position_user_mode ON day_trade_v2_positions(user_id,mode,status);
CREATE TABLE IF NOT EXISTS day_trade_v2_trades (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  symbol VARCHAR(12) NOT NULL, stock_name VARCHAR(80) NOT NULL DEFAULT '', strategy_id VARCHAR(60) NOT NULL,
  strategy_version VARCHAR(30) NOT NULL, side VARCHAR(10) NOT NULL DEFAULT 'LONG', quantity INTEGER NOT NULL,
  signal_time TIMESTAMPTZ NOT NULL, entry_order_time TIMESTAMPTZ NOT NULL, entry_fill_time TIMESTAMPTZ NOT NULL,
  entry_price NUMERIC(18,4) NOT NULL, exit_signal_time TIMESTAMPTZ NOT NULL,
  exit_order_time TIMESTAMPTZ NOT NULL, exit_fill_time TIMESTAMPTZ NOT NULL, exit_price NUMERIC(18,4) NOT NULL,
  gross_pnl NUMERIC(20,2) NOT NULL, buy_fee NUMERIC(20,2) NOT NULL, sell_fee NUMERIC(20,2) NOT NULL,
  transaction_tax NUMERIC(20,2) NOT NULL, slippage NUMERIC(20,2) NOT NULL, other_cost NUMERIC(20,2) NOT NULL,
  net_pnl NUMERIC(20,2) NOT NULL, net_return_pct NUMERIC(12,6) NOT NULL,
  entry_reason TEXT NOT NULL, exit_reason TEXT NOT NULL, market_context_json TEXT NOT NULL DEFAULT '{}',
  strategy_parameters_json TEXT NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_dtv2_trade_user_exit ON day_trade_v2_trades(user_id,exit_fill_time);
CREATE TABLE IF NOT EXISTS day_trade_v2_risk_daily (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL, trading_date DATE NOT NULL,
  realized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0, status VARCHAR(20) NOT NULL DEFAULT 'NORMAL', reason TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_risk_day UNIQUE(user_id,mode,trading_date)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_notifications (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, event_id VARCHAR(120) NOT NULL,
  mode VARCHAR(20) NOT NULL, event_type VARCHAR(50) NOT NULL, title VARCHAR(160) NOT NULL,
  message TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', read BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_notification_event UNIQUE(user_id,event_id)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_audit_events (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, action VARCHAR(80) NOT NULL,
  mode VARCHAR(20) NOT NULL, entity_type VARCHAR(50) NOT NULL DEFAULT 'SYSTEM', entity_id VARCHAR(100) NOT NULL DEFAULT '',
  details_json TEXT NOT NULL DEFAULT '{}', occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS day_trade_v2_backtest_jobs (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, backtest_mode VARCHAR(30) NOT NULL,
  strategy_id VARCHAR(60) NOT NULL DEFAULT 'ALL', start_date DATE NOT NULL, end_date DATE NOT NULL,
  status VARCHAR(30) NOT NULL, data_source VARCHAR(80) NOT NULL, data_precision VARCHAR(30) NOT NULL,
  request_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}', error_message TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS day_trade_v2_performance_snapshots (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  period_type VARCHAR(20) NOT NULL, period_key VARCHAR(20) NOT NULL, strategy_id VARCHAR(60) NOT NULL DEFAULT 'ALL',
  metrics_json TEXT NOT NULL, calculated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_performance_period UNIQUE(user_id,mode,period_type,period_key,strategy_id)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_system_errors (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL DEFAULT 'system', mode VARCHAR(20) NOT NULL DEFAULT 'PAPER',
  component VARCHAR(80) NOT NULL, error_code VARCHAR(80) NOT NULL, message TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}', occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
