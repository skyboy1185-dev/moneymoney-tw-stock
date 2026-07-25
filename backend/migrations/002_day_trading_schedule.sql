CREATE TABLE IF NOT EXISTS day_trading_schedule_settings (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(80) NOT NULL UNIQUE,
  timezone VARCHAR(40) NOT NULL DEFAULT 'Asia/Taipei',
  preheat_time VARCHAR(5) NOT NULL DEFAULT '08:30',
  stock_pool_time VARCHAR(5) NOT NULL DEFAULT '08:45',
  health_check_time VARCHAR(5) NOT NULL DEFAULT '08:55',
  market_open_time VARCHAR(5) NOT NULL DEFAULT '09:00',
  market_close_time VARCHAR(5) NOT NULL DEFAULT '13:30',
  warmup_minutes INTEGER NOT NULL DEFAULT 3,
  recommendation_refresh_seconds INTEGER NOT NULL DEFAULT 10,
  replacement_score_gap INTEGER NOT NULL DEFAULT 5,
  minimum_retention_minutes INTEGER NOT NULL DEFAULT 3,
  minimum_live_samples INTEGER NOT NULL DEFAULT 3,
  maximum_stop_distance DOUBLE PRECISION NOT NULL DEFAULT 3
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_day_schedule_settings_user
  ON day_trading_schedule_settings(user_id);
