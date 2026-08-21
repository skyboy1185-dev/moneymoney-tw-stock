CREATE TABLE IF NOT EXISTS gmail_delivery_logs (
  id BIGSERIAL PRIMARY KEY,
  recipient VARCHAR(254) NOT NULL,
  event_type VARCHAR(40) NOT NULL,
  signal_id VARCHAR(120),
  symbol VARCHAR(12),
  action VARCHAR(80) NOT NULL,
  dedupe_key VARCHAR(220) NOT NULL,
  subject VARCHAR(300) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  error_message VARCHAR(500),
  message_preview TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  sent_at TIMESTAMPTZ,
  CONSTRAINT uq_gmail_delivery_recipient_dedupe UNIQUE(recipient, dedupe_key)
);

CREATE INDEX IF NOT EXISTS ix_gmail_delivery_status_created
  ON gmail_delivery_logs(status, created_at);
CREATE INDEX IF NOT EXISTS ix_gmail_delivery_symbol_created
  ON gmail_delivery_logs(symbol, created_at);
