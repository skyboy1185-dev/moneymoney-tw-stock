CREATE TABLE IF NOT EXISTS market_regime (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL UNIQUE,
  regime VARCHAR(20) NOT NULL, provisional_regime VARCHAR(20) NOT NULL,
  regime_score NUMERIC(7,2) NOT NULL, taiex_score NUMERIC(7,2) NOT NULL DEFAULT 0,
  otc_score NUMERIC(7,2) NOT NULL DEFAULT 0, electronic_index_score NUMERIC(7,2) NOT NULL DEFAULT 0,
  breadth_score NUMERIC(7,2) NOT NULL DEFAULT 0, volume_score NUMERIC(7,2) NOT NULL DEFAULT 0,
  institutional_score NUMERIC(7,2) NOT NULL DEFAULT 0, volatility_score NUMERIC(7,2) NOT NULL DEFAULT 0,
  confirmation_days INTEGER NOT NULL DEFAULT 1,
  recommended_exposure_min NUMERIC(7,2) NOT NULL DEFAULT 20,
  recommended_exposure_max NUMERIC(7,2) NOT NULL DEFAULT 40,
  trigger_reasons TEXT NOT NULL DEFAULT '[]', indicators_json TEXT NOT NULL DEFAULT '{}',
  source_status_json TEXT NOT NULL DEFAULT '{}', missing_fields_json TEXT NOT NULL DEFAULT '[]',
  switched_at TIMESTAMPTZ, evaluated_at TIMESTAMPTZ NOT NULL,
  is_current BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_market_regime_current ON market_regime(is_current, trade_date);

CREATE TABLE IF NOT EXISTS electronic_industry_mapping (
  id BIGSERIAL PRIMARY KEY, stock_code VARCHAR(12) NOT NULL UNIQUE,
  stock_name VARCHAR(80) NOT NULL, market_type VARCHAR(20) NOT NULL,
  industry_code VARCHAR(10) NOT NULL, main_industry VARCHAR(80) NOT NULL,
  sub_industry VARCHAR(80) NOT NULL, listing_date DATE,
  is_electronic BOOLEAN NOT NULL DEFAULT FALSE, is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  source VARCHAR(120) NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_electronic_mapping_enabled ON electronic_industry_mapping(is_electronic, is_enabled);

CREATE TABLE IF NOT EXISTS electronic_industry_strength (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL, sub_industry VARCHAR(80) NOT NULL,
  return_1d NUMERIC(12,4), return_3d NUMERIC(12,4), return_5d NUMERIC(12,4), return_20d NUMERIC(12,4),
  advance_ratio NUMERIC(12,4), new_high_ratio NUMERIC(12,4), volume_growth NUMERIC(12,4),
  foreign_net_buy NUMERIC(20,2), investment_trust_net_buy NUMERIC(20,2), large_holder_change NUMERIC(12,4),
  strength_score NUMERIC(7,2) NOT NULL, strength_rank INTEGER NOT NULL,
  continuation_days INTEGER NOT NULL DEFAULT 0, score_breakdown_json TEXT NOT NULL DEFAULT '{}',
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_electronic_strength_date_industry UNIQUE(trade_date, sub_industry)
);
CREATE INDEX IF NOT EXISTS ix_electronic_strength_rank ON electronic_industry_strength(trade_date, strength_rank);

CREATE TABLE IF NOT EXISTS adaptive_stock_candidates (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL, stock_code VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL, market_type VARCHAR(20) NOT NULL,
  main_industry VARCHAR(80) NOT NULL, sub_industry VARCHAR(80) NOT NULL,
  strategy_type VARCHAR(20) NOT NULL, total_score NUMERIC(7,2) NOT NULL,
  technical_score NUMERIC(7,2) NOT NULL DEFAULT 0, chip_score NUMERIC(7,2) NOT NULL DEFAULT 0,
  fundamental_score NUMERIC(7,2) NOT NULL DEFAULT 0, industry_score NUMERIC(7,2) NOT NULL DEFAULT 0,
  market_score NUMERIC(7,2) NOT NULL DEFAULT 0, health_score NUMERIC(7,2) NOT NULL,
  previous_health_score NUMERIC(7,2), current_price NUMERIC(20,4) NOT NULL,
  entry_price_low NUMERIC(20,4) NOT NULL, entry_price_high NUMERIC(20,4) NOT NULL,
  breakout_price NUMERIC(20,4) NOT NULL, stop_loss_price NUMERIC(20,4) NOT NULL,
  target_price_1 NUMERIC(20,4) NOT NULL, target_price_2 NUMERIC(20,4) NOT NULL,
  allocation_percent NUMERIC(7,2) NOT NULL DEFAULT 0, relative_strength NUMERIC(12,4) NOT NULL DEFAULT 0,
  volume_status VARCHAR(80) NOT NULL DEFAULT '資料不足', foreign_net_buy NUMERIC(20,2),
  investment_trust_net_buy NUMERIC(20,2), holder_400_change NUMERIC(12,4), holder_1000_change NUMERIC(12,4),
  retail_holder_change NUMERIC(12,4), margin_change NUMERIC(12,4),
  industry_strength NUMERIC(7,2) NOT NULL DEFAULT 0, false_breakout_risk NUMERIC(7,2) NOT NULL DEFAULT 0,
  candidate_status VARCHAR(40) NOT NULL, rank INTEGER NOT NULL,
  score_breakdown_json TEXT NOT NULL DEFAULT '{}', selected_reasons TEXT NOT NULL DEFAULT '[]',
  risk_reasons TEXT NOT NULL DEFAULT '[]', missing_data_json TEXT NOT NULL DEFAULT '[]',
  quote_source VARCHAR(120) NOT NULL, quote_timestamp TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_adaptive_candidate_date_stock_strategy UNIQUE(trade_date, stock_code, strategy_type)
);
CREATE INDEX IF NOT EXISTS ix_adaptive_candidate_rank ON adaptive_stock_candidates(trade_date, total_score DESC);

CREATE TABLE IF NOT EXISTS stock_monitoring (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, stock_code VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL, strategy_type VARCHAR(20) NOT NULL, added_date DATE NOT NULL,
  trigger_price NUMERIC(20,4) NOT NULL, entry_price NUMERIC(20,4), stop_loss_price NUMERIC(20,4) NOT NULL,
  target_price_1 NUMERIC(20,4) NOT NULL, target_price_2 NUMERIC(20,4) NOT NULL,
  allocation_percent NUMERIC(7,2) NOT NULL DEFAULT 0, health_score NUMERIC(7,2) NOT NULL,
  monitor_status VARCHAR(40) NOT NULL DEFAULT 'monitoring', last_signal VARCHAR(80),
  last_notification_time TIMESTAMPTZ, removed_reason TEXT, updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_adaptive_monitor_user_stock UNIQUE(user_id, stock_code)
);
CREATE INDEX IF NOT EXISTS ix_adaptive_monitor_status ON stock_monitoring(user_id, monitor_status);

CREATE TABLE IF NOT EXISTS strategy_parameters (
  id BIGSERIAL PRIMARY KEY, parameter_group VARCHAR(80) NOT NULL,
  parameter_name VARCHAR(120) NOT NULL, parameter_value NUMERIC(20,6) NOT NULL,
  description TEXT NOT NULL DEFAULT '', is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_strategy_parameter_name UNIQUE(parameter_group, parameter_name)
);

CREATE TABLE IF NOT EXISTS adaptive_signals (
  id BIGSERIAL PRIMARY KEY, signal_key VARCHAR(180) NOT NULL UNIQUE,
  stock_code VARCHAR(12), stock_name VARCHAR(80), signal_type VARCHAR(50) NOT NULL,
  action VARCHAR(80) NOT NULL, strategy_type VARCHAR(20), price NUMERIC(20,4),
  health_score NUMERIC(7,2), reasons_json TEXT NOT NULL DEFAULT '[]',
  line_push_status VARCHAR(30) NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL,
  sent_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_adaptive_signals_created ON adaptive_signals(created_at DESC);
