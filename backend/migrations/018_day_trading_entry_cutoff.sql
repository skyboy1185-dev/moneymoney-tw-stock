ALTER TABLE day_trading_settings
  ALTER COLUMN latest_entry_time SET DEFAULT '11:00';

UPDATE day_trading_settings
SET latest_entry_time = '11:00'
WHERE latest_entry_time <> '11:00';
