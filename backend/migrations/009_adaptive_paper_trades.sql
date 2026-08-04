CREATE TABLE IF NOT EXISTS adaptive_paper_trades (
  id BIGSERIAL PRIMARY KEY,
  stock_code VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL,
  strategy_type VARCHAR(20) NOT NULL,
  entry_signal_key VARCHAR(180) NOT NULL UNIQUE,
  exit_signal_key VARCHAR(180),
  quantity_shares INTEGER NOT NULL DEFAULT 1000,
  entry_price NUMERIC(20,4) NOT NULL,
  entry_time TIMESTAMPTZ NOT NULL,
  entry_reason TEXT NOT NULL,
  stop_loss_price NUMERIC(20,4) NOT NULL,
  target_price_1 NUMERIC(20,4) NOT NULL,
  target_price_2 NUMERIC(20,4) NOT NULL,
  last_price NUMERIC(20,4) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'open',
  exit_price NUMERIC(20,4),
  exit_time TIMESTAMPTZ,
  exit_reason TEXT,
  gross_profit NUMERIC(20,2) NOT NULL DEFAULT 0,
  trading_cost NUMERIC(20,2) NOT NULL DEFAULT 0,
  net_profit NUMERIC(20,2) NOT NULL DEFAULT 0,
  return_percentage NUMERIC(12,4) NOT NULL DEFAULT 0,
  unrealized_profit NUMERIC(20,2) NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_adaptive_paper_trade_status ON adaptive_paper_trades(status, entry_time DESC);
CREATE INDEX IF NOT EXISTS ix_adaptive_paper_trade_exit ON adaptive_paper_trades(exit_time DESC);
