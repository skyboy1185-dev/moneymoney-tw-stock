ALTER TABLE super_ai_daytrade_settings
  ADD COLUMN IF NOT EXISTS commission_discount NUMERIC(7,4) NOT NULL DEFAULT 0.2;

UPDATE super_ai_daytrade_settings
SET commission_discount = 0.2,
    settings_version = settings_version + 1,
    updated_at = CURRENT_TIMESTAMP
WHERE id = 1
  AND (commission_discount IS NULL OR commission_discount <> 0.2);
