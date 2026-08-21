CREATE TABLE IF NOT EXISTS long_term_backtest_cache (
  backtest_key VARCHAR(40) PRIMARY KEY,
  as_of_date DATE NOT NULL,
  payload_json TEXT NOT NULL,
  calculated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_long_term_backtest_cache_as_of_date
  ON long_term_backtest_cache (as_of_date);
