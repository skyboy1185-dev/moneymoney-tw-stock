CREATE TABLE IF NOT EXISTS day_trade_v2_optimization_trials (
  id VARCHAR(80) PRIMARY KEY,
  job_id VARCHAR(80) NOT NULL,
  candidate_index INTEGER NOT NULL,
  parameters_json TEXT NOT NULL DEFAULT '{}',
  validation_metrics_json TEXT NOT NULL DEFAULT '{}',
  oos_metrics_json TEXT NOT NULL DEFAULT '{}',
  selected BOOLEAN NOT NULL DEFAULT FALSE,
  passed BOOLEAN NOT NULL DEFAULT FALSE,
  status VARCHAR(30) NOT NULL DEFAULT 'VALIDATED',
  failures_json TEXT NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_optimization_trial_candidate UNIQUE(job_id,candidate_index)
);
CREATE INDEX IF NOT EXISTS ix_dtv2_optimization_trial_job ON day_trade_v2_optimization_trials(job_id);

CREATE TABLE IF NOT EXISTS day_trade_v2_challenger_events (
  id VARCHAR(80) PRIMARY KEY,
  event_id VARCHAR(220) NOT NULL,
  run_id VARCHAR(80) NOT NULL,
  role VARCHAR(20) NOT NULL,
  event_type VARCHAR(40) NOT NULL,
  strategy_id VARCHAR(60) NOT NULL,
  strategy_version VARCHAR(30) NOT NULL,
  signal_key VARCHAR(180) NOT NULL DEFAULT '',
  symbol VARCHAR(12) NOT NULL DEFAULT '',
  occurred_at TIMESTAMPTZ NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_dtv2_challenger_event UNIQUE(event_id)
);
CREATE INDEX IF NOT EXISTS ix_dtv2_challenger_event_run ON day_trade_v2_challenger_events(run_id,occurred_at);
