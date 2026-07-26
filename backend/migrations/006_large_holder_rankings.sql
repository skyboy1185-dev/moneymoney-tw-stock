CREATE TABLE IF NOT EXISTS shareholder_distribution_weekly (
  id BIGSERIAL PRIMARY KEY,
  stock_code VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL DEFAULT '',
  market VARCHAR(20) NOT NULL DEFAULT '未知',
  industry VARCHAR(80) NOT NULL DEFAULT '未分類',
  report_date DATE NOT NULL,
  holding_level INTEGER NOT NULL,
  holder_count BIGINT NOT NULL,
  share_count BIGINT NOT NULL,
  holding_ratio NUMERIC(12,6) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_holder_distribution_stock_date_level UNIQUE(stock_code, report_date, holding_level)
);
CREATE INDEX IF NOT EXISTS ix_holder_distribution_date_stock
  ON shareholder_distribution_weekly(report_date, stock_code);

CREATE TABLE IF NOT EXISTS large_holder_weekly_summary (
  id BIGSERIAL PRIMARY KEY,
  stock_code VARCHAR(12) NOT NULL,
  report_date DATE NOT NULL,
  holders_over_400_count BIGINT NOT NULL,
  shares_over_400 BIGINT NOT NULL,
  ratio_over_400 NUMERIC(12,6) NOT NULL,
  holders_over_1000_count BIGINT NOT NULL,
  shares_over_1000 BIGINT NOT NULL,
  ratio_over_1000 NUMERIC(12,6) NOT NULL,
  total_shareholders BIGINT NOT NULL,
  total_shares BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_large_holder_summary_stock_date UNIQUE(stock_code, report_date)
);
CREATE INDEX IF NOT EXISTS ix_large_holder_summary_date
  ON large_holder_weekly_summary(report_date);

CREATE TABLE IF NOT EXISTS large_holder_weekly_change (
  id BIGSERIAL PRIMARY KEY,
  stock_code VARCHAR(12) NOT NULL,
  current_report_date DATE NOT NULL,
  previous_report_date DATE NOT NULL,
  current_ratio_over_400 NUMERIC(12,6) NOT NULL,
  previous_ratio_over_400 NUMERIC(12,6) NOT NULL,
  change_pp_over_400 NUMERIC(12,6) NOT NULL,
  change_pct_over_400 NUMERIC(14,6),
  current_ratio_over_1000 NUMERIC(12,6) NOT NULL,
  previous_ratio_over_1000 NUMERIC(12,6) NOT NULL,
  change_pp_over_1000 NUMERIC(12,6) NOT NULL,
  change_pct_over_1000 NUMERIC(14,6),
  holder_count_change_over_400 BIGINT NOT NULL,
  holder_count_change_over_1000 BIGINT NOT NULL,
  anomaly_flag BOOLEAN NOT NULL DEFAULT FALSE,
  anomaly_reason TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_large_holder_change_period UNIQUE(stock_code, current_report_date, previous_report_date)
);
CREATE INDEX IF NOT EXISTS ix_large_holder_change_current
  ON large_holder_weekly_change(current_report_date, change_pp_over_400 DESC);

CREATE TABLE IF NOT EXISTS large_holder_monitors (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(80) NOT NULL,
  stock_code VARCHAR(12) NOT NULL,
  stock_name VARCHAR(80) NOT NULL,
  monitor_type VARCHAR(20) NOT NULL DEFAULT 'over400',
  line_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  added_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_large_holder_monitor_user_stock UNIQUE(user_id, stock_code)
);
CREATE INDEX IF NOT EXISTS ix_large_holder_monitor_user_active
  ON large_holder_monitors(user_id, active);
