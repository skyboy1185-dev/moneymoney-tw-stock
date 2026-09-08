CREATE TABLE IF NOT EXISTS strong_stock_scan_archives (
    id VARCHAR(80) PRIMARY KEY,
    trade_date DATE NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    strategy_version VARCHAR(30) NOT NULL,
    config_json TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_strong_stock_scan_archives_trade_date
    ON strong_stock_scan_archives (trade_date);
