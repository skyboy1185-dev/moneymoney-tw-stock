-- Strategy controller, market-regime audit, and safe champion/challenger optimization.
ALTER TABLE day_trade_v2_strategy_versions ADD COLUMN IF NOT EXISTS parent_version VARCHAR(30) NOT NULL DEFAULT '';
ALTER TABLE day_trade_v2_strategy_versions ADD COLUMN IF NOT EXISTS parameters_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE day_trade_v2_strategy_versions ADD COLUMN IF NOT EXISTS change_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE day_trade_v2_strategy_versions ADD COLUMN IF NOT EXISTS data_period_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE day_trade_v2_strategy_versions ADD COLUMN IF NOT EXISTS backtest_result_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE day_trade_v2_strategy_versions ADD COLUMN IF NOT EXISTS oos_result_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE day_trade_v2_strategy_versions ADD COLUMN IF NOT EXISTS simulation_result_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE day_trade_v2_strategy_versions ADD COLUMN IF NOT EXISTS checksum VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE day_trade_v2_strategy_versions ADD COLUMN IF NOT EXISTS validation_status VARCHAR(30) NOT NULL DEFAULT 'UNVERIFIED';
ALTER TABLE day_trade_v2_signals ADD COLUMN IF NOT EXISTS controller_decision_id VARCHAR(80) NOT NULL DEFAULT '';
ALTER TABLE day_trade_v2_orders ADD COLUMN IF NOT EXISTS controller_decision_id VARCHAR(80) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS day_trade_v2_strategy_deployments (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, strategy_id VARCHAR(60) NOT NULL,
  version VARCHAR(30) NOT NULL, role VARCHAR(20) NOT NULL DEFAULT 'CHAMPION',
  status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE', approved_by VARCHAR(80) NOT NULL DEFAULT '',
  approved_at TIMESTAMPTZ, effective_date DATE, activated_at TIMESTAMPTZ,
  disabled_at TIMESTAMPTZ, disabled_reason TEXT NOT NULL DEFAULT '',
  rollback_to_version VARCHAR(30) NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_dtv2_deployment_active ON day_trade_v2_strategy_deployments(user_id,strategy_id,role,status);

CREATE TABLE IF NOT EXISTS day_trade_v2_strategy_health_snapshots (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  strategy_id VARCHAR(60) NOT NULL, strategy_version VARCHAR(30) NOT NULL, diagnosis_date DATE NOT NULL,
  status VARCHAR(30) NOT NULL, reasons_json TEXT NOT NULL DEFAULT '[]', metrics_json TEXT NOT NULL DEFAULT '{}',
  baseline_json TEXT NOT NULL DEFAULT '{}', recommended_action VARCHAR(30) NOT NULL DEFAULT 'NONE',
  capital_multiplier NUMERIC(12,6) NOT NULL DEFAULT 1, risk_multiplier NUMERIC(12,6) NOT NULL DEFAULT 1,
  calculated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_health_day UNIQUE(user_id,mode,strategy_id,diagnosis_date)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_strategy_risk_overrides (
  id BIGSERIAL PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  strategy_id VARCHAR(60) NOT NULL, capital_multiplier NUMERIC(12,6) NOT NULL DEFAULT 1,
  risk_multiplier NUMERIC(12,6) NOT NULL DEFAULT 1, paused BOOLEAN NOT NULL DEFAULT FALSE,
  reason TEXT NOT NULL DEFAULT '', source VARCHAR(30) NOT NULL DEFAULT 'HEALTH',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_risk_override UNIQUE(user_id,mode,strategy_id)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_optimization_datasets (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, name VARCHAR(160) NOT NULL,
  storage_path TEXT NOT NULL DEFAULT '', checksum VARCHAR(64) NOT NULL, data_format VARCHAR(20) NOT NULL,
  start_date DATE NOT NULL, end_date DATE NOT NULL, trading_day_count INTEGER NOT NULL DEFAULT 0,
  symbol_count INTEGER NOT NULL DEFAULT 0, row_count INTEGER NOT NULL DEFAULT 0,
  quality_status VARCHAR(30) NOT NULL DEFAULT 'PENDING', quality_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_dtv2_dataset_user ON day_trade_v2_optimization_datasets(user_id);
CREATE TABLE IF NOT EXISTS day_trade_v2_optimization_jobs (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, strategy_id VARCHAR(60) NOT NULL,
  champion_version VARCHAR(30) NOT NULL, candidate_version VARCHAR(30) NOT NULL DEFAULT '',
  trigger_type VARCHAR(30) NOT NULL DEFAULT 'MANUAL', dataset_id VARCHAR(80) NOT NULL DEFAULT '',
  status VARCHAR(30) NOT NULL DEFAULT 'QUEUED', progress_pct NUMERIC(12,6) NOT NULL DEFAULT 0,
  search_space_json TEXT NOT NULL DEFAULT '{}', walk_forward_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}', error_message TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_dtv2_optimization_user ON day_trade_v2_optimization_jobs(user_id);
CREATE TABLE IF NOT EXISTS day_trade_v2_challenger_runs (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, strategy_id VARCHAR(60) NOT NULL,
  champion_version VARCHAR(30) NOT NULL, challenger_version VARCHAR(30) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'RUNNING', started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  full_trading_days INTEGER NOT NULL DEFAULT 0, trade_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0, champion_metrics_json TEXT NOT NULL DEFAULT '{}',
  challenger_metrics_json TEXT NOT NULL DEFAULT '{}', completed_at TIMESTAMPTZ, last_counted_date DATE
);
CREATE INDEX IF NOT EXISTS ix_dtv2_challenger_user ON day_trade_v2_challenger_runs(user_id);
CREATE TABLE IF NOT EXISTS day_trade_v2_challenger_positions (
  id VARCHAR(80) PRIMARY KEY, run_id VARCHAR(80) NOT NULL, role VARCHAR(20) NOT NULL,
  strategy_version VARCHAR(30) NOT NULL, signal_key VARCHAR(180) NOT NULL,
  symbol VARCHAR(12) NOT NULL, quantity INTEGER NOT NULL, entry_price NUMERIC(18,4) NOT NULL,
  stop_price NUMERIC(18,4) NOT NULL, target_price NUMERIC(18,4) NOT NULL,
  opened_at TIMESTAMPTZ NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'OPEN', closed_at TIMESTAMPTZ,
  CONSTRAINT uq_dtv2_shadow_signal UNIQUE(run_id,role,signal_key)
);
CREATE INDEX IF NOT EXISTS ix_dtv2_challenger_position_run ON day_trade_v2_challenger_positions(run_id);
CREATE TABLE IF NOT EXISTS day_trade_v2_challenger_trades (
  id VARCHAR(80) PRIMARY KEY, run_id VARCHAR(80) NOT NULL, role VARCHAR(20) NOT NULL,
  symbol VARCHAR(12) NOT NULL, entry_time TIMESTAMPTZ NOT NULL, exit_time TIMESTAMPTZ NOT NULL,
  quantity INTEGER NOT NULL, entry_price NUMERIC(18,4) NOT NULL, exit_price NUMERIC(18,4) NOT NULL,
  net_pnl NUMERIC(20,2) NOT NULL, cost NUMERIC(20,2) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_dtv2_challenger_trade_run ON day_trade_v2_challenger_trades(run_id);
CREATE TABLE IF NOT EXISTS day_trade_v2_market_regime_snapshots (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  bucket_at TIMESTAMPTZ NOT NULL, proposed_regime VARCHAR(30) NOT NULL,
  effective_regime VARCHAR(30) NOT NULL, confidence NUMERIC(12,6) NOT NULL,
  data_blocked BOOLEAN NOT NULL DEFAULT FALSE, reasons_json TEXT NOT NULL DEFAULT '[]',
  inputs_json TEXT NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_regime_bucket UNIQUE(user_id,mode,bucket_at)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_controller_cycles (
  id VARCHAR(80) PRIMARY KEY, user_id VARCHAR(80) NOT NULL, mode VARCHAR(20) NOT NULL,
  cycle_key VARCHAR(120) NOT NULL, trading_date DATE NOT NULL, evaluated_at TIMESTAMPTZ NOT NULL,
  regime_snapshot_id VARCHAR(80) NOT NULL DEFAULT '', candidate_count INTEGER NOT NULL DEFAULT 0,
  selected_candidate_id VARCHAR(80) NOT NULL DEFAULT '', status VARCHAR(30) NOT NULL DEFAULT 'COMPLETED',
  block_reason TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_controller_cycle UNIQUE(user_id,mode,cycle_key)
);
CREATE TABLE IF NOT EXISTS day_trade_v2_controller_candidates (
  id VARCHAR(80) PRIMARY KEY, cycle_id VARCHAR(80) NOT NULL, user_id VARCHAR(80) NOT NULL,
  mode VARCHAR(20) NOT NULL, candidate_key VARCHAR(180) NOT NULL, symbol VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL DEFAULT '', sector VARCHAR(100) NOT NULL DEFAULT '',
  strategy_id VARCHAR(60) NOT NULL, strategy_version VARCHAR(30) NOT NULL,
  signal_time TIMESTAMPTZ NOT NULL, raw_score NUMERIC(12,6) NOT NULL,
  final_score NUMERIC(12,6) NOT NULL, rank INTEGER, entry_price NUMERIC(18,4) NOT NULL,
  stop_price NUMERIC(18,4) NOT NULL, target_price NUMERIC(18,4) NOT NULL,
  risk_reward NUMERIC(12,6) NOT NULL, planned_capital NUMERIC(20,2) NOT NULL DEFAULT 0,
  allowed BOOLEAN NOT NULL DEFAULT FALSE, status VARCHAR(30) NOT NULL DEFAULT 'REJECTED',
  score_details_json TEXT NOT NULL DEFAULT '{}', reasons_json TEXT NOT NULL DEFAULT '[]',
  blocked_reasons_json TEXT NOT NULL DEFAULT '[]', created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_controller_candidate UNIQUE(user_id,mode,candidate_key)
);
CREATE INDEX IF NOT EXISTS ix_dtv2_controller_candidate_cycle ON day_trade_v2_controller_candidates(cycle_id);
