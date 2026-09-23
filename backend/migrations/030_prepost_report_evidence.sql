ALTER TABLE prepost_analysis_days ADD COLUMN IF NOT EXISTS market_snapshot_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE prepost_analysis_days ADD COLUMN IF NOT EXISTS score_breakdown_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE prepost_analysis_days ADD COLUMN IF NOT EXISTS pre_market_prediction_json TEXT NOT NULL DEFAULT '{}';
