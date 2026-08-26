ALTER TABLE super_ai_daytrade_settings
  ADD COLUMN IF NOT EXISTS max_stop_distance_pct NUMERIC(7,4) NOT NULL DEFAULT 1.0;
