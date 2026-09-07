ALTER TABLE day_trade_v2_trades
  ADD COLUMN IF NOT EXISTS entry_market_regime VARCHAR(30) NOT NULL DEFAULT 'UNKNOWN';

ALTER TABLE day_trade_v2_challenger_positions
  ADD COLUMN IF NOT EXISTS entry_market_regime VARCHAR(30) NOT NULL DEFAULT 'UNKNOWN';

ALTER TABLE day_trade_v2_challenger_trades
  ADD COLUMN IF NOT EXISTS entry_market_regime VARCHAR(30) NOT NULL DEFAULT 'UNKNOWN';

CREATE INDEX IF NOT EXISTS ix_dtv2_trade_regime
  ON day_trade_v2_trades(user_id, mode, strategy_id, entry_market_regime);

CREATE INDEX IF NOT EXISTS ix_dtv2_challenger_trade_regime
  ON day_trade_v2_challenger_trades(run_id, role, entry_market_regime);
