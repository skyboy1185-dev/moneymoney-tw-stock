CREATE TABLE IF NOT EXISTS long_term_trade_events (
  id SERIAL PRIMARY KEY,
  event_key VARCHAR(140) NOT NULL,
  portfolio_mode VARCHAR(20) NOT NULL,
  position_id INTEGER NOT NULL REFERENCES long_term_positions(id),
  stock_code VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL,
  direction VARCHAR(10) NOT NULL DEFAULT 'long',
  event_type VARCHAR(12) NOT NULL,
  trade_date DATE NOT NULL,
  price NUMERIC(20,4) NOT NULL,
  allocation_weight_pct NUMERIC(9,4) NOT NULL,
  allocated_capital NUMERIC(20,2) NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 0,
  pnl NUMERIC(20,2),
  pnl_percent NUMERIC(12,4),
  reason TEXT NOT NULL,
  is_read BOOLEAN NOT NULL DEFAULT FALSE,
  read_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_long_term_trade_event_key UNIQUE (event_key)
);

CREATE INDEX IF NOT EXISTS ix_long_term_trade_event_mode_id
  ON long_term_trade_events (portfolio_mode, id);

CREATE INDEX IF NOT EXISTS ix_long_term_trade_event_created
  ON long_term_trade_events (created_at);
