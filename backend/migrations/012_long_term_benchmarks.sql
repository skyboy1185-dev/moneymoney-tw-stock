ALTER TABLE long_term_portfolio_runs
  ADD COLUMN IF NOT EXISTS portfolio_nav NUMERIC(16,6) NOT NULL DEFAULT 100;

ALTER TABLE long_term_portfolio_runs
  ADD COLUMN IF NOT EXISTS daily_return_pct NUMERIC(12,6) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS long_term_benchmarks (
  symbol VARCHAR(12) PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  start_date DATE NOT NULL,
  entry_price NUMERIC(20,4) NOT NULL,
  last_price NUMERIC(20,4) NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
