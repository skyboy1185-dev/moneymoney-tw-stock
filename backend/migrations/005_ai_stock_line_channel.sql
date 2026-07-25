CREATE TABLE IF NOT EXISTS ai_stock_line_groups (
  id BIGSERIAL PRIMARY KEY,
  group_id VARCHAR(80) NOT NULL UNIQUE,
  display_name VARCHAR(120) NOT NULL DEFAULT 'AI 選股通知群組',
  active BOOLEAN NOT NULL DEFAULT TRUE,
  bound_at TIMESTAMPTZ NOT NULL,
  unbound_at TIMESTAMPTZ,
  last_webhook_at TIMESTAMPTZ,
  last_push_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_ai_stock_line_groups_active
  ON ai_stock_line_groups(active, bound_at);

CREATE TABLE IF NOT EXISTS ai_stock_line_delivery_logs (
  id BIGSERIAL PRIMARY KEY,
  group_id VARCHAR(80) NOT NULL,
  event_type VARCHAR(40) NOT NULL,
  signal_id VARCHAR(120),
  symbol VARCHAR(12),
  action VARCHAR(80) NOT NULL,
  priority INTEGER NOT NULL,
  dedupe_key VARCHAR(220) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  response_status INTEGER,
  error_message VARCHAR(500),
  message_preview TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  sent_at TIMESTAMPTZ,
  CONSTRAINT uq_ai_stock_line_delivery_group_dedupe UNIQUE(group_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS ix_ai_stock_line_delivery_status
  ON ai_stock_line_delivery_logs(status, priority, created_at);
CREATE INDEX IF NOT EXISTS ix_ai_stock_line_delivery_symbol
  ON ai_stock_line_delivery_logs(symbol, created_at);

CREATE TABLE IF NOT EXISTS ai_stock_line_webhook_events (
  id BIGSERIAL PRIMARY KEY,
  webhook_event_id VARCHAR(120) NOT NULL UNIQUE,
  event_type VARCHAR(40) NOT NULL,
  group_id_masked VARCHAR(40),
  received_at TIMESTAMPTZ NOT NULL
);
