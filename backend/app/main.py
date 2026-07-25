from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import get_settings
from .database import SessionLocal, create_tables
from .routers import content, day_trading, portfolio, screener, stocks

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_tables()
    yield


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
app.include_router(portfolio.router, prefix=settings.api_prefix)
app.include_router(day_trading.router, prefix=settings.api_prefix)


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
        "database": database_status,
        "mock_data": settings.mock_data_enabled,
        "checked_at": datetime.now(UTC),
    }
