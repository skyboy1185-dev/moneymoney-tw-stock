ALTER TABLE strong_stock_orders ADD COLUMN IF NOT EXISTS execution_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE strong_stock_positions ADD COLUMN IF NOT EXISTS execution_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE strong_stock_trades ADD COLUMN IF NOT EXISTS execution_json TEXT NOT NULL DEFAULT '{}';
