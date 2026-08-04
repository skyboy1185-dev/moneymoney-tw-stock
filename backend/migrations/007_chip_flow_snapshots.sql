CREATE TABLE IF NOT EXISTS chip_flow_snapshots (
  id BIGSERIAL PRIMARY KEY,
  trade_date DATE NOT NULL,
  stock_id VARCHAR(12) NOT NULL,
  snapshot_time TIMESTAMPTZ NOT NULL,
  large_buy_shares BIGINT NOT NULL DEFAULT 0,
  large_sell_shares BIGINT NOT NULL DEFAULT 0,
  large_net_shares BIGINT NOT NULL DEFAULT 0,
  medium_buy_shares BIGINT NOT NULL DEFAULT 0,
  medium_sell_shares BIGINT NOT NULL DEFAULT 0,
  medium_net_shares BIGINT NOT NULL DEFAULT 0,
  small_buy_shares BIGINT NOT NULL DEFAULT 0,
  small_sell_shares BIGINT NOT NULL DEFAULT 0,
  small_net_shares BIGINT NOT NULL DEFAULT 0,
  unknown_shares BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_chip_flow_stock_date_minute
    UNIQUE(trade_date, stock_id, snapshot_time)
);

CREATE INDEX IF NOT EXISTS ix_chip_flow_stock_date_time
  ON chip_flow_snapshots(stock_id, trade_date, snapshot_time);
