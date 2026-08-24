ALTER TABLE day_trading_positions
  ADD COLUMN IF NOT EXISTS holding_period VARCHAR(20) NOT NULL DEFAULT 'intraday';

ALTER TABLE day_trading_positions
  ADD COLUMN IF NOT EXISTS entry_confidence DOUBLE PRECISION NOT NULL DEFAULT 0;

ALTER TABLE day_trading_positions
  ADD COLUMN IF NOT EXISTS strategy_confidence DOUBLE PRECISION NOT NULL DEFAULT 0;
