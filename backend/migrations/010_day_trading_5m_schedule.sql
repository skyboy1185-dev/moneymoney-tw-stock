ALTER TABLE day_trading_settings
  ALTER COLUMN latest_entry_time SET DEFAULT '10:30';

UPDATE day_trading_settings
SET latest_entry_time = '10:30', close_reminder_time = '13:25'
WHERE latest_entry_time <> '10:30' OR close_reminder_time <> '13:25';

UPDATE day_trading_schedule_settings
SET market_close_time = '13:30'
WHERE market_close_time <> '13:30';
