CREATE TABLE IF NOT EXISTS rocket_accounts (
  id INTEGER PRIMARY KEY,
  initial_capital NUMERIC(20,2) NOT NULL DEFAULT 1000000,
  cash NUMERIC(20,2) NOT NULL DEFAULT 1000000,
  realized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0,
  broker_fee_discount NUMERIC(7,4) NOT NULL DEFAULT 0.6,
  slippage_rate NUMERIC(9,6) NOT NULL DEFAULT 0.001,
  sound_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS rocket_market_regime (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL UNIQUE, regime VARCHAR(30) NOT NULL,
  regime_label VARCHAR(40) NOT NULL, score NUMERIC(7,2) NOT NULL,
  maximum_exposure_pct NUMERIC(7,2) NOT NULL, strategy_label VARCHAR(120) NOT NULL,
  reasons_json TEXT NOT NULL DEFAULT '[]', indicators_json TEXT NOT NULL DEFAULT '{}',
  missing_fields_json TEXT NOT NULL DEFAULT '[]', evaluated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS rocket_sector_strength (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL, sector_name VARCHAR(80) NOT NULL,
  strength_rank INTEGER NOT NULL, strength_score NUMERIC(7,2) NOT NULL,
  return_1d NUMERIC(12,4), return_3d NUMERIC(12,4), return_5d NUMERIC(12,4),
  return_20d NUMERIC(12,4), advance_ratio NUMERIC(12,4), new_high_ratio NUMERIC(12,4),
  volume_growth NUMERIC(12,4), breakdown_json TEXT NOT NULL DEFAULT '{}', updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_rocket_sector_date_name UNIQUE (trade_date, sector_name)
);
CREATE INDEX IF NOT EXISTS ix_rocket_sector_date_rank ON rocket_sector_strength (trade_date, strength_rank);

CREATE TABLE IF NOT EXISTS rocket_candidates (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL, stock_code VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL, market_type VARCHAR(20) NOT NULL, sector_name VARCHAR(80) NOT NULL,
  sector_rank INTEGER NOT NULL, rank INTEGER NOT NULL, is_top5 BOOLEAN NOT NULL DEFAULT FALSE,
  candidate_status VARCHAR(30) NOT NULL, pattern_type VARCHAR(40) NOT NULL,
  market_regime VARCHAR(30) NOT NULL, current_price NUMERIC(20,4) NOT NULL,
  change_pct NUMERIC(12,4) NOT NULL, rocket_score NUMERIC(7,2) NOT NULL,
  chase_risk_score NUMERIC(7,2) NOT NULL, sector_score NUMERIC(7,2), momentum_score NUMERIC(7,2),
  volume_score NUMERIC(7,2), pattern_score NUMERIC(7,2), chip_score NUMERIC(7,2),
  institutional_score NUMERIC(7,2), quality_score NUMERIC(7,2),
  data_availability_pct NUMERIC(7,2) NOT NULL, volume_ratio NUMERIC(12,4) NOT NULL,
  breakout_price NUMERIC(20,4) NOT NULL, stop_loss_price NUMERIC(20,4) NOT NULL,
  target_price_1 NUMERIC(20,4) NOT NULL, target_price_2 NUMERIC(20,4) NOT NULL,
  risk_reward_ratio NUMERIC(12,4) NOT NULL, atr NUMERIC(20,4), ma5 NUMERIC(20,4),
  ma10 NUMERIC(20,4), ma20 NUMERIC(20,4), reasons_json TEXT NOT NULL DEFAULT '[]',
  missing_data_json TEXT NOT NULL DEFAULT '[]', score_breakdown_json TEXT NOT NULL DEFAULT '{}',
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_rocket_candidate_date_stock UNIQUE (trade_date, stock_code)
);
CREATE INDEX IF NOT EXISTS ix_rocket_candidate_date_rank ON rocket_candidates (trade_date, rank);
CREATE INDEX IF NOT EXISTS ix_rocket_candidate_status ON rocket_candidates (trade_date, candidate_status);

CREATE TABLE IF NOT EXISTS rocket_signals (
  id BIGSERIAL PRIMARY KEY, signal_key VARCHAR(180) NOT NULL UNIQUE, stock_code VARCHAR(12),
  stock_name VARCHAR(80), signal_type VARCHAR(30) NOT NULL, previous_status VARCHAR(30),
  new_status VARCHAR(30) NOT NULL, price NUMERIC(20,4), rocket_score NUMERIC(7,2),
  chase_risk NUMERIC(7,2), strategy_type VARCHAR(40), reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rocket_signal_created ON rocket_signals (created_at);

CREATE TABLE IF NOT EXISTS rocket_positions (
  id BIGSERIAL PRIMARY KEY, stock_code VARCHAR(12) NOT NULL, stock_name VARCHAR(80) NOT NULL,
  sector_name VARCHAR(80) NOT NULL, strategy_type VARCHAR(40) NOT NULL, market_regime VARCHAR(30) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'open', entry_time TIMESTAMPTZ NOT NULL,
  entry_price NUMERIC(20,4) NOT NULL, average_cost NUMERIC(20,4) NOT NULL,
  current_price NUMERIC(20,4) NOT NULL, target_allocation NUMERIC(20,2) NOT NULL,
  original_quantity INTEGER NOT NULL, remaining_quantity INTEGER NOT NULL,
  add_stage INTEGER NOT NULL DEFAULT 1, take_profit_stage INTEGER NOT NULL DEFAULT 0,
  stop_loss_price NUMERIC(20,4) NOT NULL, trailing_stop_price NUMERIC(20,4),
  target_price_1 NUMERIC(20,4) NOT NULL, target_price_2 NUMERIC(20,4) NOT NULL,
  highest_price NUMERIC(20,4) NOT NULL, lowest_price NUMERIC(20,4) NOT NULL,
  rocket_score_entry NUMERIC(7,2) NOT NULL, rocket_score_current NUMERIC(7,2) NOT NULL,
  chase_risk_current NUMERIC(7,2) NOT NULL, invested_cost NUMERIC(20,2) NOT NULL,
  realized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0, unrealized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0,
  max_favorable_excursion NUMERIC(20,2) NOT NULL DEFAULT 0,
  max_adverse_excursion NUMERIC(20,2) NOT NULL DEFAULT 0, latest_action VARCHAR(80) NOT NULL DEFAULT '持有',
  exit_time TIMESTAMPTZ, exit_price NUMERIC(20,4), exit_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rocket_position_status ON rocket_positions (status, updated_at);
CREATE INDEX IF NOT EXISTS ix_rocket_position_stock ON rocket_positions (stock_code, status);

CREATE TABLE IF NOT EXISTS rocket_trades (
  id BIGSERIAL PRIMARY KEY, position_id BIGINT NOT NULL REFERENCES rocket_positions(id),
  stock_code VARCHAR(12) NOT NULL, stock_name VARCHAR(80) NOT NULL, action VARCHAR(30) NOT NULL,
  strategy_type VARCHAR(40) NOT NULL, price NUMERIC(20,4) NOT NULL, quantity INTEGER NOT NULL,
  gross_amount NUMERIC(20,2) NOT NULL, fee NUMERIC(20,2) NOT NULL, tax NUMERIC(20,2) NOT NULL,
  slippage NUMERIC(20,2) NOT NULL, net_amount NUMERIC(20,2) NOT NULL,
  realized_pnl NUMERIC(20,2) NOT NULL DEFAULT 0, reason TEXT NOT NULL, executed_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rocket_trade_time ON rocket_trades (executed_at, stock_code);

CREATE TABLE IF NOT EXISTS rocket_daily_portfolio (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL UNIQUE, cash NUMERIC(20,2) NOT NULL,
  market_value NUMERIC(20,2) NOT NULL, total_equity NUMERIC(20,2) NOT NULL,
  daily_pnl NUMERIC(20,2) NOT NULL, cumulative_pnl NUMERIC(20,2) NOT NULL,
  realized_pnl NUMERIC(20,2) NOT NULL, unrealized_pnl NUMERIC(20,2) NOT NULL,
  drawdown_pct NUMERIC(12,4) NOT NULL, recorded_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS rocket_notifications (
  id BIGSERIAL PRIMARY KEY, dedupe_key VARCHAR(200) NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL,
  stock_code VARCHAR(12), stock_name VARCHAR(80), notification_type VARCHAR(30) NOT NULL,
  priority INTEGER NOT NULL DEFAULT 4, title VARCHAR(160) NOT NULL, message TEXT NOT NULL,
  price NUMERIC(20,4), rocket_score NUMERIC(7,2), chase_risk NUMERIC(7,2), quantity INTEGER,
  amount NUMERIC(20,2), pnl NUMERIC(20,2), pnl_percent NUMERIC(12,4), strategy_type VARCHAR(40),
  reason TEXT NOT NULL, is_read BOOLEAN NOT NULL DEFAULT FALSE, read_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_rocket_notification_created ON rocket_notifications (created_at);
CREATE INDEX IF NOT EXISTS ix_rocket_notification_unread ON rocket_notifications (is_read, priority);
