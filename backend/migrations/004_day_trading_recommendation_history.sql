CREATE TABLE IF NOT EXISTS day_trading_recommendation_history (
  id BIGSERIAL PRIMARY KEY,
  signal_id VARCHAR(80) NOT NULL UNIQUE,
  trading_date DATE NOT NULL,
  symbol VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL,
  market VARCHAR(20) NOT NULL,
  direction VARCHAR(12) NOT NULL,
  action VARCHAR(80) NOT NULL,
  payload_json TEXT NOT NULL,
  recommended_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_day_recommendation_history_date
  ON day_trading_recommendation_history(trading_date, recommended_at);
