CREATE TABLE IF NOT EXISTS limit_up_ai_notifications (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(80) NOT NULL,
  dedupe_key VARCHAR(180) NOT NULL,
  notification_type VARCHAR(30) NOT NULL,
  priority INTEGER NOT NULL DEFAULT 4,
  title VARCHAR(160) NOT NULL,
  message TEXT NOT NULL,
  symbol VARCHAR(12),
  stock_name VARCHAR(80),
  setup_type VARCHAR(40),
  price NUMERIC(20,4),
  quantity INTEGER,
  amount NUMERIC(20,2),
  realized_pnl NUMERIC(20,2),
  score NUMERIC(7,2),
  reason TEXT NOT NULL,
  is_read BOOLEAN NOT NULL DEFAULT FALSE,
  read_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_limit_up_ai_notification_dedupe UNIQUE (user_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS ix_limit_up_ai_notification_user_time
  ON limit_up_ai_notifications(user_id, created_at);

CREATE INDEX IF NOT EXISTS ix_limit_up_ai_notification_unread
  ON limit_up_ai_notifications(user_id, is_read, created_at);
