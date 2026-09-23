ALTER TABLE super_ai_daytrade_notifications ADD COLUMN IF NOT EXISTS email_delivery_status VARCHAR(30) NOT NULL DEFAULT 'PENDING';
ALTER TABLE super_ai_daytrade_notifications ADD COLUMN IF NOT EXISTS email_sent_at TIMESTAMPTZ;
ALTER TABLE day_trade_v2_notifications ADD COLUMN IF NOT EXISTS email_delivery_status VARCHAR(30) NOT NULL DEFAULT 'PENDING';
ALTER TABLE day_trade_v2_notifications ADD COLUMN IF NOT EXISTS email_sent_at TIMESTAMPTZ;
UPDATE super_ai_daytrade_notifications SET email_delivery_status = 'SENT' WHERE email_sent = TRUE AND email_delivery_status = 'PENDING';
UPDATE day_trade_v2_notifications SET email_delivery_status = 'SENT' WHERE email_sent = TRUE AND email_delivery_status = 'PENDING';
