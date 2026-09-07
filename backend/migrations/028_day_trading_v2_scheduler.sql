CREATE TABLE IF NOT EXISTS day_trade_v2_runtime_states (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL DEFAULT 'PAPER',
  trading_date DATE NOT NULL, status VARCHAR(30) NOT NULL DEFAULT 'WAITING',
  phase VARCHAR(40) NOT NULL DEFAULT 'BEFORE_INITIALIZATION', auto_start BOOLEAN NOT NULL DEFAULT TRUE,
  initialized BOOLEAN NOT NULL DEFAULT FALSE, receiving_quotes BOOLEAN NOT NULL DEFAULT FALSE,
  scanning BOOLEAN NOT NULL DEFAULT FALSE, order_allowed BOOLEAN NOT NULL DEFAULT FALSE,
  heartbeat_at TIMESTAMPTZ, last_quote_at TIMESTAMPTZ, last_scan_at TIMESTAMPTZ, last_bar_at TIMESTAMPTZ,
  next_scan_at TIMESTAMPTZ, next_event_type VARCHAR(60) NOT NULL DEFAULT '', next_event_at TIMESTAMPTZ,
  scanned_stock_count INTEGER NOT NULL DEFAULT 0, candidate_count INTEGER NOT NULL DEFAULT 0,
  signal_count INTEGER NOT NULL DEFAULT 0, order_count INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER NOT NULL DEFAULT 0, completed_trade_count INTEGER NOT NULL DEFAULT 0,
  latest_error TEXT NOT NULL DEFAULT '', lease_owner VARCHAR(100) NOT NULL DEFAULT '', lease_until TIMESTAMPTZ,
  started_at TIMESTAMPTZ, initialized_at TIMESTAMPTZ, closed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_runtime_day UNIQUE(user_id,mode,trading_date)
);
CREATE INDEX IF NOT EXISTS ix_dtv2_runtime_user_day ON day_trade_v2_runtime_states(user_id,trading_date);

CREATE TABLE IF NOT EXISTS day_trade_v2_candidate_states (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  trading_date DATE NOT NULL, symbol VARCHAR(12) NOT NULL, stock_name VARCHAR(80) NOT NULL DEFAULT '',
  sector VARCHAR(100) NOT NULL DEFAULT '', strategy_id VARCHAR(60) NOT NULL DEFAULT '',
  confidence NUMERIC(12,6) NOT NULL DEFAULT 0, signal_level VARCHAR(30) NOT NULL DEFAULT 'GENERAL',
  primary_reason TEXT NOT NULL DEFAULT '', reasons_json TEXT NOT NULL DEFAULT '[]',
  quote_at TIMESTAMPTZ, bar_at TIMESTAMPTZ, scanned_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_dtv2_candidate_day_symbol UNIQUE(user_id,mode,trading_date,symbol)
);
CREATE INDEX IF NOT EXISTS ix_dtv2_candidate_rank ON day_trade_v2_candidate_states(user_id,trading_date,confidence DESC);

CREATE TABLE IF NOT EXISTS day_trade_v2_skip_stats (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  trading_date DATE NOT NULL, reason VARCHAR(200) NOT NULL, occurrence_count INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_skip_reason_day UNIQUE(user_id,mode,trading_date,reason)
);

CREATE TABLE IF NOT EXISTS day_trade_v2_schedule_events (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  trading_date DATE NOT NULL, event_type VARCHAR(60) NOT NULL, scheduled_at TIMESTAMPTZ NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'PENDING', payload_json TEXT NOT NULL DEFAULT '{}',
  executed_at TIMESTAMPTZ, error_message TEXT NOT NULL DEFAULT '',
  CONSTRAINT uq_dtv2_schedule_event_day UNIQUE(user_id,mode,trading_date,event_type)
);

CREATE TABLE IF NOT EXISTS day_trade_v2_calendar_holidays (
  holiday_date DATE PRIMARY KEY, name VARCHAR(160) NOT NULL,
  source VARCHAR(80) NOT NULL DEFAULT 'TWSE', fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
