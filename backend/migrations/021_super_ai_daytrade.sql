CREATE TABLE IF NOT EXISTS super_ai_daytrade_settings (
  id INTEGER PRIMARY KEY DEFAULT 1,
  system_name VARCHAR(80) NOT NULL DEFAULT '超強AI當沖系統',
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  trading_mode VARCHAR(20) NOT NULL DEFAULT 'PAPER',
  max_capital NUMERIC(20,2) NOT NULL DEFAULT 5000000,
  available_capital NUMERIC(20,2) NOT NULL DEFAULT 5000000,
  risk_per_trade_pct NUMERIC(7,4) NOT NULL DEFAULT 0.25,
  daily_max_loss_pct NUMERIC(7,4) NOT NULL DEFAULT 1.0,
  weekly_drawdown_pct NUMERIC(7,4) NOT NULL DEFAULT 3.0,
  min_ai_score_to_trade NUMERIC(7,2) NOT NULL DEFAULT 80,
  min_ai_score_to_watch NUMERIC(7,2) NOT NULL DEFAULT 70,
  min_risk_reward NUMERIC(7,2) NOT NULL DEFAULT 2,
  max_positions INTEGER NOT NULL DEFAULT 5,
  max_position_pct NUMERIC(7,2) NOT NULL DEFAULT 20,
  email_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  email_buy_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  email_sell_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  email_add_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  email_stop_loss_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  email_take_profit_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  email_risk_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  email_daily_summary_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  email_error_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  stop_new_trades BOOLEAN NOT NULL DEFAULT FALSE,
  stop_reason TEXT,
  consecutive_stop_losses INTEGER NOT NULL DEFAULT 0,
  settings_version INTEGER NOT NULL DEFAULT 1,
  updated_by VARCHAR(80) NOT NULL DEFAULT 'system',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT ck_super_ai_daytrade_settings_one_row CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS super_ai_daytrade_notifications (
  id BIGSERIAL PRIMARY KEY,
  source VARCHAR(40) NOT NULL,
  category VARCHAR(40) NOT NULL,
  level VARCHAR(20) NOT NULL,
  symbol VARCHAR(12),
  symbol_name VARCHAR(80),
  title VARCHAR(180) NOT NULL,
  message TEXT NOT NULL,
  strategy VARCHAR(40),
  side VARCHAR(10),
  price NUMERIC(20,4),
  quantity INTEGER,
  stop_loss NUMERIC(20,4),
  take_profit_1 NUMERIC(20,4),
  take_profit_2 NUMERIC(20,4),
  ai_score NUMERIC(7,2),
  risk_reward NUMERIC(12,4),
  dedupe_key VARCHAR(220) NOT NULL,
  email_sent BOOLEAN NOT NULL DEFAULT FALSE,
  popup_shown BOOLEAN NOT NULL DEFAULT FALSE,
  is_read BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  read_at TIMESTAMPTZ,
  CONSTRAINT uq_super_ai_daytrade_notification_dedupe UNIQUE (source, dedupe_key)
);
CREATE INDEX IF NOT EXISTS ix_super_ai_daytrade_notifications_source_time
  ON super_ai_daytrade_notifications (source, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_super_ai_daytrade_notifications_read
  ON super_ai_daytrade_notifications (is_read, created_at DESC);

ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS side VARCHAR(10) NOT NULL DEFAULT 'LONG';
ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS trade_mode VARCHAR(20) NOT NULL DEFAULT 'PAPER';
ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS ai_score NUMERIC(7,2) NOT NULL DEFAULT 0;
ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS market_regime VARCHAR(20) NOT NULL DEFAULT 'UNCERTAIN';
ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS sector_status VARCHAR(80) NOT NULL DEFAULT '';
ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS initial_capital NUMERIC(20,2) NOT NULL DEFAULT 5000000;
ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS risk_amount NUMERIC(20,2) NOT NULL DEFAULT 0;
ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS initial_r NUMERIC(20,4) NOT NULL DEFAULT 0;
ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS realized_r NUMERIC(12,4) NOT NULL DEFAULT 0;
ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS entry_reasons_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE adaptive_paper_trades ADD COLUMN IF NOT EXISTS exit_reasons_json TEXT NOT NULL DEFAULT '[]';

INSERT INTO super_ai_daytrade_settings (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;
