-- 型態選股機器人：與 AI 當沖機器人的帳務、訊號及績效完全隔離。
CREATE TABLE IF NOT EXISTS pattern_robot_settings (
  id INTEGER PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT TRUE,
  robot_mode VARCHAR(20) NOT NULL DEFAULT 'SWING', performance_mode VARCHAR(20) NOT NULL DEFAULT 'PAPER_LIVE',
  initial_capital NUMERIC(20,2) NOT NULL DEFAULT 1000000, cash NUMERIC(20,2) NOT NULL DEFAULT 1000000,
  paper_live_cash NUMERIC(20,2) NOT NULL DEFAULT 1000000,
  manual_paper_cash NUMERIC(20,2) NOT NULL DEFAULT 1000000,
  backtest_cash NUMERIC(20,2) NOT NULL DEFAULT 1000000,
  max_positions INTEGER NOT NULL DEFAULT 5, max_position_pct NUMERIC(7,2) NOT NULL DEFAULT 20,
  max_sector_pct NUMERIC(7,2) NOT NULL DEFAULT 40, risk_per_trade_pct NUMERIC(7,4) NOT NULL DEFAULT 0.5,
  minimum_score NUMERIC(7,2) NOT NULL DEFAULT 70, minimum_risk_reward NUMERIC(7,2) NOT NULL DEFAULT 2,
  pivot_window INTEGER NOT NULL DEFAULT 5, minimum_swing_pct NUMERIC(7,2) NOT NULL DEFAULT 3,
  allow_probe BOOLEAN NOT NULL DEFAULT TRUE, allow_add BOOLEAN NOT NULL DEFAULT TRUE,
  trailing_stop_enabled BOOLEAN NOT NULL DEFAULT TRUE, opening_reminder_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  broker_fee_discount NUMERIC(7,4) NOT NULL DEFAULT 0.6, slippage_rate NUMERIC(9,6) NOT NULL DEFAULT 0.001,
  day_trade_close_time VARCHAR(5) NOT NULL DEFAULT '13:20', settings_version INTEGER NOT NULL DEFAULT 1,
  updated_by VARCHAR(80) NOT NULL DEFAULT 'system', updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS pattern_detections (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL, stock_code VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL, market_type VARCHAR(20) NOT NULL, sector_name VARCHAR(80) NOT NULL DEFAULT '其他',
  pattern_type VARCHAR(40) NOT NULL, pattern_status VARCHAR(30) NOT NULL, pattern_score NUMERIC(7,2) NOT NULL,
  primary_pattern BOOLEAN NOT NULL DEFAULT FALSE, start_date DATE NOT NULL, confirmed_at TIMESTAMPTZ,
  detected_at TIMESTAMPTZ NOT NULL, pivot_confirmed_at TIMESTAMPTZ,
  neckline_price NUMERIC(20,4) NOT NULL, breakout_price NUMERIC(20,4) NOT NULL,
  current_price NUMERIC(20,4) NOT NULL, target_price NUMERIC(20,4) NOT NULL,
  invalidation_price NUMERIC(20,4) NOT NULL, stop_loss_price NUMERIC(20,4) NOT NULL,
  entry_price_low NUMERIC(20,4) NOT NULL, entry_price_high NUMERIC(20,4) NOT NULL,
  add_price NUMERIC(20,4), take_profit_1 NUMERIC(20,4) NOT NULL, take_profit_2 NUMERIC(20,4) NOT NULL,
  trailing_stop_price NUMERIC(20,4), volume_ratio NUMERIC(12,4) NOT NULL,
  distance_to_breakout_pct NUMERIC(12,4) NOT NULL, risk_reward_ratio NUMERIC(12,4) NOT NULL,
  completion_pct NUMERIC(7,2) NOT NULL, action VARCHAR(30) NOT NULL, action_label VARCHAR(60) NOT NULL,
  suggested_position_pct NUMERIC(7,2) NOT NULL DEFAULT 0, suggested_quantity INTEGER NOT NULL DEFAULT 0,
  market_regime VARCHAR(30) NOT NULL DEFAULT 'neutral', sector_strength NUMERIC(7,2) NOT NULL DEFAULT 50,
  volume_confirmed BOOLEAN NOT NULL DEFAULT FALSE, key_points_json TEXT NOT NULL DEFAULT '[]',
  score_breakdown_json TEXT NOT NULL DEFAULT '{}', reasons_json TEXT NOT NULL DEFAULT '[]',
  missing_conditions_json TEXT NOT NULL DEFAULT '[]', risk_warnings_json TEXT NOT NULL DEFAULT '[]',
  all_patterns_json TEXT NOT NULL DEFAULT '[]', source_json TEXT NOT NULL DEFAULT '{}', notified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_pattern_detection_date_stock_type UNIQUE (trade_date, stock_code, pattern_type)
);
CREATE INDEX IF NOT EXISTS ix_pattern_detection_date_score ON pattern_detections (trade_date, pattern_score);
CREATE INDEX IF NOT EXISTS ix_pattern_detection_status ON pattern_detections (trade_date, pattern_status);

CREATE TABLE IF NOT EXISTS pattern_watchlist (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, detection_id BIGINT REFERENCES pattern_detections(id),
  stock_code VARCHAR(12) NOT NULL, stock_name VARCHAR(80) NOT NULL, pattern_type VARCHAR(40) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE, trade_paused BOOLEAN NOT NULL DEFAULT FALSE,
  reminder_only BOOLEAN NOT NULL DEFAULT FALSE, added_at TIMESTAMPTZ NOT NULL, removed_at TIMESTAMPTZ,
  removed_reason TEXT, updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_pattern_watch_user_stock_type UNIQUE (user_id, stock_code, pattern_type)
);
CREATE INDEX IF NOT EXISTS ix_pattern_watch_user_active ON pattern_watchlist (user_id, active);

CREATE TABLE IF NOT EXISTS pattern_signals (
  id BIGSERIAL PRIMARY KEY, detection_id BIGINT NOT NULL REFERENCES pattern_detections(id), trade_date DATE NOT NULL,
  stock_code VARCHAR(12) NOT NULL, stock_name VARCHAR(80) NOT NULL, pattern_type VARCHAR(40) NOT NULL,
  signal_type VARCHAR(30) NOT NULL, signal_version INTEGER NOT NULL DEFAULT 1, action VARCHAR(30) NOT NULL,
  signal_price NUMERIC(20,4) NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
  reasons_json TEXT NOT NULL DEFAULT '[]', signal_time TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ,
  CONSTRAINT uq_pattern_signal_version UNIQUE (trade_date, stock_code, pattern_type, signal_type, signal_version)
);
CREATE INDEX IF NOT EXISTS ix_pattern_signal_time ON pattern_signals (signal_time, stock_code);

CREATE TABLE IF NOT EXISTS pattern_orders (
  id BIGSERIAL PRIMARY KEY, signal_id BIGINT NOT NULL REFERENCES pattern_signals(id),
  performance_mode VARCHAR(20) NOT NULL, order_action VARCHAR(30) NOT NULL, stock_code VARCHAR(12) NOT NULL,
  quantity INTEGER NOT NULL, order_price NUMERIC(20,4) NOT NULL, status VARCHAR(30) NOT NULL DEFAULT 'CREATED',
  filled_quantity INTEGER NOT NULL DEFAULT 0, rejection_reason TEXT, created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL, CONSTRAINT uq_pattern_order_signal_action UNIQUE (signal_id, order_action)
);
CREATE INDEX IF NOT EXISTS ix_pattern_order_status ON pattern_orders (status, created_at);

CREATE TABLE IF NOT EXISTS pattern_fills (
  id BIGSERIAL PRIMARY KEY, order_id BIGINT NOT NULL REFERENCES pattern_orders(id),
  signal_id BIGINT NOT NULL REFERENCES pattern_signals(id), stock_code VARCHAR(12) NOT NULL,
  side VARCHAR(10) NOT NULL, signal_price NUMERIC(20,4) NOT NULL, filled_price NUMERIC(20,4) NOT NULL,
  quantity INTEGER NOT NULL, gross_amount NUMERIC(20,2) NOT NULL, fee NUMERIC(20,2) NOT NULL,
  tax NUMERIC(20,2) NOT NULL, slippage NUMERIC(20,2) NOT NULL, net_amount NUMERIC(20,2) NOT NULL,
  realized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0, filled_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pattern_fill_time ON pattern_fills (filled_at, stock_code);

CREATE TABLE IF NOT EXISTS pattern_trade_cycles (
  id BIGSERIAL PRIMARY KEY, stock_code VARCHAR(12) NOT NULL, stock_name VARCHAR(80) NOT NULL,
  primary_pattern VARCHAR(40) NOT NULL, all_patterns_json TEXT NOT NULL DEFAULT '[]', pattern_score NUMERIC(7,2) NOT NULL,
  robot_mode VARCHAR(20) NOT NULL, performance_mode VARCHAR(20) NOT NULL, market_regime VARCHAR(30) NOT NULL,
  sector_strength NUMERIC(7,2) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
  first_entry_at TIMESTAMPTZ NOT NULL, closed_at TIMESTAMPTZ, cumulative_buy_quantity INTEGER NOT NULL DEFAULT 0,
  cumulative_buy_amount NUMERIC(20,2) NOT NULL DEFAULT 0, cumulative_sell_quantity INTEGER NOT NULL DEFAULT 0,
  cumulative_sell_amount NUMERIC(20,2) NOT NULL DEFAULT 0, realized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0,
  unrealized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0, trading_cost NUMERIC(20,2) NOT NULL DEFAULT 0,
  mfe NUMERIC(20,2) NOT NULL DEFAULT 0, mae NUMERIC(20,2) NOT NULL DEFAULT 0,
  exit_reason VARCHAR(40), reasons_json TEXT NOT NULL DEFAULT '[]', created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pattern_cycle_mode_status ON pattern_trade_cycles (performance_mode, status);

CREATE TABLE IF NOT EXISTS pattern_positions (
  id BIGSERIAL PRIMARY KEY, trade_cycle_id BIGINT NOT NULL REFERENCES pattern_trade_cycles(id),
  stock_code VARCHAR(12) NOT NULL, stock_name VARCHAR(80) NOT NULL, primary_pattern VARCHAR(40) NOT NULL,
  pattern_status VARCHAR(30) NOT NULL, robot_mode VARCHAR(20) NOT NULL, performance_mode VARCHAR(20) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'OPEN', quantity INTEGER NOT NULL, sellable_quantity INTEGER NOT NULL,
  average_cost NUMERIC(20,4) NOT NULL, current_price NUMERIC(20,4) NOT NULL,
  invested_cost NUMERIC(20,2) NOT NULL, realized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0,
  unrealized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0, stop_loss_price NUMERIC(20,4) NOT NULL,
  take_profit_1 NUMERIC(20,4) NOT NULL, take_profit_2 NUMERIC(20,4) NOT NULL,
  pattern_target_price NUMERIC(20,4) NOT NULL, trailing_stop_price NUMERIC(20,4),
  highest_price NUMERIC(20,4) NOT NULL, lowest_price NUMERIC(20,4) NOT NULL,
  take_profit_stage INTEGER NOT NULL DEFAULT 0, auto_trade_paused BOOLEAN NOT NULL DEFAULT FALSE,
  note TEXT NOT NULL DEFAULT '', first_entry_at TIMESTAMPTZ NOT NULL, last_add_at TIMESTAMPTZ,
  closed_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pattern_position_mode_status ON pattern_positions (performance_mode, status);
CREATE INDEX IF NOT EXISTS ix_pattern_position_stock ON pattern_positions (stock_code, status);

CREATE TABLE IF NOT EXISTS pattern_position_lots (
  id BIGSERIAL PRIMARY KEY, position_id BIGINT NOT NULL REFERENCES pattern_positions(id),
  fill_id BIGINT NOT NULL REFERENCES pattern_fills(id), quantity INTEGER NOT NULL,
  remaining_quantity INTEGER NOT NULL, cost_per_share NUMERIC(20,4) NOT NULL, acquired_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pattern_lot_position ON pattern_position_lots (position_id, remaining_quantity);

CREATE TABLE IF NOT EXISTS pattern_trade_messages (
  id BIGSERIAL PRIMARY KEY, signal_id BIGINT REFERENCES pattern_signals(id), message_type VARCHAR(40) NOT NULL,
  message_version INTEGER NOT NULL DEFAULT 1, stock_code VARCHAR(12), stock_name VARCHAR(80), pattern_type VARCHAR(40),
  action VARCHAR(30), title VARCHAR(160) NOT NULL, message TEXT NOT NULL, price NUMERIC(20,4), quantity INTEGER,
  amount NUMERIC(20,2), cash_impact NUMERIC(20,2), position_impact INTEGER,
  reasons_json TEXT NOT NULL DEFAULT '[]', is_read BOOLEAN NOT NULL DEFAULT FALSE, read_at TIMESTAMPTZ,
  remind_after TIMESTAMPTZ, displayed_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_pattern_message_version UNIQUE (signal_id, message_type, message_version)
);
CREATE INDEX IF NOT EXISTS ix_pattern_message_unread ON pattern_trade_messages (is_read, created_at);

CREATE TABLE IF NOT EXISTS pattern_daily_equity (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL, robot_mode VARCHAR(30) NOT NULL,
  performance_mode VARCHAR(20) NOT NULL, cash NUMERIC(20,2) NOT NULL, market_value NUMERIC(20,2) NOT NULL,
  total_equity NUMERIC(20,2) NOT NULL, daily_pnl NUMERIC(20,2) NOT NULL,
  cumulative_pnl NUMERIC(20,2) NOT NULL, realized_pnl NUMERIC(20,2) NOT NULL,
  unrealized_pnl NUMERIC(20,2) NOT NULL, drawdown_pct NUMERIC(12,4) NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_pattern_equity_date_robot_performance_mode UNIQUE (trade_date, robot_mode, performance_mode)
);
CREATE INDEX IF NOT EXISTS ix_pattern_equity_date ON pattern_daily_equity (trade_date, performance_mode);

CREATE TABLE IF NOT EXISTS pattern_performance_snapshots (
  id BIGSERIAL PRIMARY KEY, performance_mode VARCHAR(20) NOT NULL, robot_mode VARCHAR(20) NOT NULL,
  parameter_version INTEGER NOT NULL, metrics_json TEXT NOT NULL, by_pattern_json TEXT NOT NULL,
  calculated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pattern_perf_mode_time ON pattern_performance_snapshots (performance_mode, calculated_at);

CREATE TABLE IF NOT EXISTS pattern_robot_runs (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL, run_type VARCHAR(30) NOT NULL DEFAULT 'OPEN_SCAN',
  status VARCHAR(30) NOT NULL, scanned_count INTEGER NOT NULL DEFAULT 0, matched_count INTEGER NOT NULL DEFAULT 0,
  counts_json TEXT NOT NULL DEFAULT '{}', error_message TEXT, parameter_version INTEGER NOT NULL DEFAULT 1,
  source_json TEXT NOT NULL DEFAULT '{}', started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ,
  CONSTRAINT uq_pattern_run_date_type UNIQUE (trade_date, run_type)
);
CREATE INDEX IF NOT EXISTS ix_pattern_run_status ON pattern_robot_runs (status, started_at);
