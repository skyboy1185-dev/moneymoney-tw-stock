-- Asynchronous preparation and progress metadata for automatic minute backtests.
ALTER TABLE day_trade_v2_backtest_jobs ADD COLUMN IF NOT EXISTS dataset_id VARCHAR(80) NOT NULL DEFAULT '';
ALTER TABLE day_trade_v2_backtest_jobs ADD COLUMN IF NOT EXISTS progress_pct NUMERIC(12,6) NOT NULL DEFAULT 0;
ALTER TABLE day_trade_v2_backtest_jobs ADD COLUMN IF NOT EXISTS progress_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE day_trade_v2_backtest_jobs ADD COLUMN IF NOT EXISTS universe_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE day_trade_v2_backtest_jobs ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(120) NOT NULL DEFAULT '';
ALTER TABLE day_trade_v2_backtest_jobs ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;
ALTER TABLE day_trade_v2_backtest_jobs ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_dtv2_backtest_status ON day_trade_v2_backtest_jobs(status, created_at);
