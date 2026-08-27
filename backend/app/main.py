from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import get_settings
from .database import SessionLocal, cleanup_expired_operational_data, create_tables
from .routers import (
    adaptive_electronic,
    ai_stock,
    ai_stock_line_integration,
    content,
    chip_flow,
    day_trading,
    line_integration,
    large_holders,
    limit_up_ai,
    long_term,
    market_data,
    pattern_robot,
    portfolio,
    rocket_radar,
    screener,
    stocks,
)
from .services.adaptive_electronic_automation import adaptive_electronic_automation
from .services.ai_stock_automation import ai_stock_automation
from .services.chip_flow_alerts import electronic_chip_flow_alert_monitor
from .services.day_trading_automation import day_trading_automation
from .services.line_messaging import line_notification_dispatcher
from .services.large_holder_automation import large_holder_automation
from .services.limit_up_ai_automation import limit_up_ai_automation
from .services.long_term_automation import long_term_selection_automation
from .services.pattern_robot_automation import pattern_robot_automation
from .services.rocket_automation import rocket_radar_automation

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_tables()
    cleanup_expired_operational_data(retention_days=7)
    await line_notification_dispatcher.start()
    await day_trading_automation.start()
    # 型態掃描必須先於原本 AI 選股偵測啟動；09:00 後重啟會由此立即補掃。
    await pattern_robot_automation.start(persist=False)
    await ai_stock_automation.start()
    await adaptive_electronic_automation.start()
    await large_holder_automation.start()
    await electronic_chip_flow_alert_monitor.start()
    await long_term_selection_automation.start()
    await rocket_radar_automation.start()
    await limit_up_ai_automation.start()
    try:
        yield
    finally:
        await limit_up_ai_automation.stop()
        await rocket_radar_automation.stop()
        await long_term_selection_automation.stop()
        await electronic_chip_flow_alert_monitor.stop()
        await large_holder_automation.stop()
        await adaptive_electronic_automation.stop()
        await ai_stock_automation.stop()
        await pattern_robot_automation.stop(persist=False)
        await day_trading_automation.stop()
        await line_notification_dispatcher.stop()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Moneymoney 台股分析 MVP 後端；預設使用 Mock MarketDataProvider。",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(stocks.router, prefix=settings.api_prefix)
app.include_router(screener.router, prefix=settings.api_prefix)
app.include_router(content.router, prefix=settings.api_prefix)
app.include_router(chip_flow.router, prefix=settings.api_prefix)
app.include_router(portfolio.router, prefix=settings.api_prefix)
app.include_router(day_trading.router, prefix=settings.api_prefix)
app.include_router(pattern_robot.router, prefix=settings.api_prefix)
app.include_router(line_integration.router, prefix=settings.api_prefix)
app.include_router(ai_stock_line_integration.router, prefix=settings.api_prefix)
app.include_router(ai_stock.router, prefix=settings.api_prefix)
app.include_router(adaptive_electronic.router, prefix=settings.api_prefix)
app.include_router(large_holders.router, prefix=settings.api_prefix)
app.include_router(limit_up_ai.router, prefix=settings.api_prefix)
app.include_router(long_term.router, prefix=settings.api_prefix)
app.include_router(rocket_radar.router, prefix=settings.api_prefix)
app.include_router(market_data.router, prefix=settings.api_prefix)
app.include_router(line_integration.webhook_router)
app.include_router(ai_stock_line_integration.webhook_router)


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "docs": "/docs", "health": f"{settings.api_prefix}/health"}


@app.get(f"{settings.api_prefix}/health")
def health() -> dict:
    database_status = "connected"
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        database_status = "unavailable"
    return {
        "status": "ok" if database_status == "connected" else "degraded",
        "app": settings.app_name,
        "environment": settings.app_env,
        "runtime_mode": settings.runtime_mode,
        "database": database_status,
        "mock_data": settings.mock_data_enabled,
        "checked_at": datetime.now(UTC),
    }
