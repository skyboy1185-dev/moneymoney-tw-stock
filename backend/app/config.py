from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Moneymoney 台股分析 API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://moneymoney:moneymoney@localhost:5432/moneymoney"
    cors_origins: str = "http://localhost:3000"
    mock_data_enabled: bool = True
    redis_url: str | None = None
    quote_refresh_seconds: float = 5.0
    day_trading_stream_seconds: float = 2.0
    ai_stock_monitor_seconds: int = 60
    ai_stock_scanner_url: str = ""
    ai_stock_scanner_timeout_seconds: float = 30.0
    ai_stock_automation_user_id: str = "system-ai-stock"
    adaptive_electronic_enabled: bool = True
    adaptive_electronic_scanner_url: str = ""
    adaptive_electronic_scanner_token: str = ""
    adaptive_electronic_scanner_timeout_seconds: float = 900.0
    adaptive_electronic_admin_token: str = ""
    large_holder_auto_sync_enabled: bool = True
    large_holder_sync_interval_seconds: int = 21_600
    twse_timezone: str = "Asia/Taipei"
    twse_holidays: str = ""
    line_channel_access_token: str = ""
    line_channel_secret: str = ""
    line_target_group_id: str = ""
    line_notifications_enabled: bool = True
    ai_stock_line_channel_access_token: str = ""
    ai_stock_line_channel_secret: str = ""
    ai_stock_line_target_group_id: str = ""
    ai_stock_line_notifications_enabled: bool = True
    public_web_url: str = ""
    chip_flow_large_order_amount: int = 2_000_000
    chip_flow_small_order_amount: int = 500_000
    chip_flow_dynamic_large_order_enabled: bool = True
    chip_flow_dynamic_large_order_percentile: float = 0.99
    chip_flow_dynamic_large_order_min_samples: int = 100
    chip_flow_alert_window_minutes: int = 5
    chip_flow_alert_min_recent_net_lots: float = 10.0
    chip_flow_alert_min_buy_sell_ratio: float = 1.5
    chip_flow_alert_min_positive_steps: int = 2
    chip_flow_alert_max_stale_minutes: int = 10
    chip_flow_electronic_scan_interval_seconds: float = 2.0
    fugle_marketdata_api_key: str = ""
    fugle_marketdata_base_url: str = "https://api.fugle.tw/marketdata/v1.0"
    fugle_chip_flow_page_size: int = 500
    fugle_chip_flow_max_pages: int = 100
    fugle_chip_flow_include_odd_lot: bool = True
    fugle_chip_flow_timeout_seconds: float = 15.0

    @field_validator("database_url", mode="after")
    @classmethod
    def normalize_postgres_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
