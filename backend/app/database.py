from collections.abc import Generator
from datetime import date, timedelta
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
logger = logging.getLogger(__name__)
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
pool_options = {} if settings.database_url.startswith("sqlite") else {
    # The dashboard has several independent polling panels. Keep enough steady
    # connections for them, but fail fast instead of freezing the event loop for
    # SQLAlchemy's 30-second default when PostgreSQL is saturated.
    "pool_size": 10,
    "max_overflow": 10,
    "pool_timeout": 5,
    "pool_recycle": 900,
    "pool_use_lifo": True,
}
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=connect_args,
    **pool_options,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Background scanners perform network I/O and bursty writes. Giving them their
# own deliberately small pool prevents a stalled provider or scan cycle from
# consuming every connection needed by interactive HTTP requests.
if settings.database_url.startswith("sqlite"):
    background_engine = engine
    BackgroundSessionLocal = SessionLocal
else:
    background_engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=4,
        max_overflow=0,
        pool_timeout=2,
        pool_recycle=900,
        pool_use_lifo=True,
    )
    BackgroundSessionLocal = sessionmaker(
        bind=background_engine,
        autoflush=False,
        expire_on_commit=False,
    )


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_tables() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # create_all does not add columns to an existing PostgreSQL table. Keep the
    # long-term NAV fields backward-compatible for deployments created by 011.
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE long_term_portfolio_runs "
                "ADD COLUMN IF NOT EXISTS portfolio_nav NUMERIC(16,6) NOT NULL DEFAULT 100"
            ))
            connection.execute(text(
                "ALTER TABLE long_term_portfolio_runs "
                "ADD COLUMN IF NOT EXISTS daily_return_pct NUMERIC(12,6) NOT NULL DEFAULT 0"
            ))
            connection.execute(text(
                "ALTER TABLE long_term_positions "
                "ADD COLUMN IF NOT EXISTS allocation_weight_pct NUMERIC(9,4) NOT NULL DEFAULT 10"
            ))
            connection.execute(text(
                "ALTER TABLE long_term_positions "
                "ADD COLUMN IF NOT EXISTS allocated_capital NUMERIC(20,2) NOT NULL DEFAULT 100000"
            ))
            connection.execute(text(
                "ALTER TABLE long_term_positions "
                "ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 0"
            ))
    # Database synchronization can merge two independently created portfolio
    # batches. Quarantine overflow before enforcing one open row per symbol.
    from .services.long_term_selection import repair_long_term_position_overflow
    with SessionLocal() as session:
        repaired = repair_long_term_position_overflow(session)
        session.commit()
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_long_term_open_mode_stock "
            "ON long_term_positions (portfolio_mode, stock_code) WHERE status = 'open'"
        ))
    if any(repaired.values()):
        logger.warning("long-term portfolio overflow repaired: %s", repaired)


def cleanup_expired_operational_data(retention_days: int = 7) -> dict[str, int]:
    """Prune reconstructable intraday data without touching user portfolios/trades."""
    cutoff = date.today() - timedelta(days=max(1, retention_days))
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            snapshot_result = connection.execute(
                text("DELETE FROM chip_flow_snapshots WHERE trade_date < :cutoff"),
                {"cutoff": cutoff},
            )
            signal_result = connection.execute(
                text(
                    "DELETE FROM day_trading_signals "
                    "WHERE generated_at < CURRENT_TIMESTAMP - INTERVAL '7 days'"
                )
            )
        else:
            snapshot_result = connection.execute(
                text("DELETE FROM chip_flow_snapshots WHERE trade_date < :cutoff"),
                {"cutoff": cutoff.isoformat()},
            )
            signal_result = connection.execute(
                text(
                    "DELETE FROM day_trading_signals "
                    "WHERE generated_at < datetime('now', '-7 days')"
                )
            )
    deleted = {
        "chip_flow_snapshots": max(0, snapshot_result.rowcount or 0),
        "day_trading_signals": max(0, signal_result.rowcount or 0),
    }
    if engine.dialect.name == "postgresql":
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("VACUUM (ANALYZE) chip_flow_snapshots"))
            connection.execute(text("VACUUM (ANALYZE) day_trading_signals"))
    logger.warning("operational database retention cleanup completed: %s", deleted)
    return deleted
