from collections.abc import Generator
from datetime import date, timedelta
import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
logger = logging.getLogger(__name__)
OPERATIONAL_TABLES = (
    "chip_flow_snapshots",
    "day_trading_signals",
    "day_trading_candidate_snapshots",
    "limit_up_ai_snapshots",
)
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


def database_connection_info(expected_host: str = "") -> dict[str, object]:
    """Return non-secret database target metadata for logs and health checks."""
    url = make_url(settings.database_url)
    backend = url.get_backend_name()
    host = url.host or ("local-file" if backend == "sqlite" else "")
    database = url.database or ""
    expected = expected_host.strip()
    return {
        "dialect": backend,
        "driver": url.get_driver_name(),
        "host": host,
        "port": url.port,
        "database": Path(database).name if backend == "sqlite" else database,
        "expectedHost": expected or None,
        "matchesExpectedHost": (host == expected) if expected else None,
    }


def log_database_target(expected_host: str = "") -> None:
    info = database_connection_info(expected_host)
    logger.warning(
        "database target: dialect=%s driver=%s host=%s port=%s database=%s expectedHost=%s matchesExpectedHost=%s",
        info["dialect"],
        info["driver"],
        info["host"],
        info["port"],
        info["database"],
        info["expectedHost"],
        info["matchesExpectedHost"],
    )


def database_runtime_status(session: Session, expected_host: str = "") -> dict[str, object]:
    """Return lightweight database health and storage metadata without secrets."""
    session.execute(text("SELECT 1"))
    info = database_connection_info(expected_host)
    status: dict[str, object] = {
        **info,
        "connected": True,
        "databaseSizeMB": None,
        "operationalTables": [],
    }
    if engine.dialect.name == "postgresql":
        size_bytes = session.execute(
            text("SELECT pg_database_size(current_database())")
        ).scalar_one()
        status["databaseSizeMB"] = round(float(size_bytes) / 1024 / 1024, 3)
        table_names = "', '".join(OPERATIONAL_TABLES)
        rows = session.execute(text(
            "SELECT c.relname, pg_total_relation_size(c.oid), "
            "GREATEST(COALESCE(s.n_live_tup, c.reltuples), 0) "
            "FROM pg_class c "
            "LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid "
            f"WHERE c.relkind = 'r' AND c.relname IN ('{table_names}') "
            "ORDER BY c.relname"
        )).all()
        status["operationalTables"] = [
            {
                "name": name,
                "estimatedRows": int(estimated_rows),
                "sizeMB": round(float(size_bytes) / 1024 / 1024, 3),
            }
            for name, size_bytes, estimated_rows in rows
        ]
    elif engine.dialect.name == "sqlite":
        database = make_url(settings.database_url).database
        if database and Path(database).exists():
            status["databaseSizeMB"] = round(Path(database).stat().st_size / 1024 / 1024, 3)
        tables = []
        for table in OPERATIONAL_TABLES:
            try:
                count = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            except SQLAlchemyError:
                continue
            tables.append({"name": table, "estimatedRows": int(count), "sizeMB": None})
        status["operationalTables"] = tables
    return status


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
            connection.execute(text(
                "ALTER TABLE day_trading_positions "
                "ADD COLUMN IF NOT EXISTS holding_period VARCHAR(20) NOT NULL DEFAULT 'intraday'"
            ))
            connection.execute(text(
                "ALTER TABLE day_trading_positions "
                "ADD COLUMN IF NOT EXISTS entry_confidence DOUBLE PRECISION NOT NULL DEFAULT 0"
            ))
            connection.execute(text(
                "ALTER TABLE day_trading_positions "
                "ADD COLUMN IF NOT EXISTS strategy_confidence DOUBLE PRECISION NOT NULL DEFAULT 0"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS side VARCHAR(10) NOT NULL DEFAULT 'LONG'"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS trade_mode VARCHAR(20) NOT NULL DEFAULT 'PAPER'"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS ai_score NUMERIC(7,2) NOT NULL DEFAULT 0"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS market_regime VARCHAR(20) NOT NULL DEFAULT 'UNCERTAIN'"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS sector_status VARCHAR(80) NOT NULL DEFAULT ''"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS initial_capital NUMERIC(20,2) NOT NULL DEFAULT 5000000"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS risk_amount NUMERIC(20,2) NOT NULL DEFAULT 0"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS initial_r NUMERIC(20,4) NOT NULL DEFAULT 0"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS realized_r NUMERIC(12,4) NOT NULL DEFAULT 0"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS entry_reasons_json TEXT NOT NULL DEFAULT '[]'"
            ))
            connection.execute(text(
                "ALTER TABLE adaptive_paper_trades "
                "ADD COLUMN IF NOT EXISTS exit_reasons_json TEXT NOT NULL DEFAULT '[]'"
            ))
            connection.execute(text(
                "ALTER TABLE super_ai_daytrade_settings "
                "ADD COLUMN IF NOT EXISTS commission_discount NUMERIC(7,4) NOT NULL DEFAULT 0.2"
            ))
            connection.execute(text(
                "ALTER TABLE super_ai_daytrade_settings "
                "ADD COLUMN IF NOT EXISTS max_stop_distance_pct NUMERIC(7,4) NOT NULL DEFAULT 1.0"
            ))
            connection.execute(text(
                "INSERT INTO super_ai_daytrade_settings ("
                "id, system_name, enabled, trading_mode, max_capital, "
                "available_capital, risk_per_trade_pct, daily_max_loss_pct, "
                "weekly_drawdown_pct, min_ai_score_to_trade, min_ai_score_to_watch, "
                "min_risk_reward, max_positions, max_position_pct, "
                "max_stop_distance_pct, commission_discount, email_enabled, "
                "email_buy_enabled, email_sell_enabled, email_add_enabled, "
                "email_stop_loss_enabled, email_take_profit_enabled, "
                "email_risk_enabled, email_daily_summary_enabled, email_error_enabled, "
                "stop_new_trades, consecutive_stop_losses, settings_version, "
                "updated_by, updated_at"
                ") VALUES ("
                "1, 'Super AI Daytrade', TRUE, 'PAPER', 5000000, "
                "5000000, 0.25, 1.0, 3.0, 80, 70, 2, 5, 20, "
                "1.0, 0.2, TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, "
                "FALSE, 0, 1, 'system', CURRENT_TIMESTAMP"
                ") ON CONFLICT (id) DO NOTHING"
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


def _rowcount(value) -> int:
    return max(0, value.rowcount or 0)


def cleanup_expired_operational_data(
    retention_days: int = 3,
    intraday_snapshot_retention_hours: int = 2,
) -> dict[str, int]:
    """Prune reconstructable intraday data without touching user portfolios/trades."""
    cutoff = date.today() - timedelta(days=max(1, retention_days))
    retention_hours = max(1, intraday_snapshot_retention_hours)
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
            candidate_snapshot_result = connection.execute(
                text(
                    "DELETE FROM day_trading_candidate_snapshots "
                    "WHERE trading_date < :cutoff "
                    "OR snapshot_at < CURRENT_TIMESTAMP - (:retention_hours * INTERVAL '1 hour')"
                ),
                {"cutoff": cutoff, "retention_hours": retention_hours},
            )
            limit_up_snapshot_result = connection.execute(
                text(
                    "DELETE FROM limit_up_ai_snapshots "
                    "WHERE trading_date < :cutoff "
                    "OR snapshot_at < CURRENT_TIMESTAMP - (:retention_hours * INTERVAL '1 hour')"
                ),
                {"cutoff": cutoff, "retention_hours": retention_hours},
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
            candidate_snapshot_result = connection.execute(
                text(
                    "DELETE FROM day_trading_candidate_snapshots "
                    "WHERE trading_date < :cutoff OR snapshot_at < datetime('now', :retention_modifier)"
                ),
                {"cutoff": cutoff.isoformat(), "retention_modifier": f"-{retention_hours} hours"},
            )
            limit_up_snapshot_result = connection.execute(
                text(
                    "DELETE FROM limit_up_ai_snapshots "
                    "WHERE trading_date < :cutoff OR snapshot_at < datetime('now', :retention_modifier)"
                ),
                {"cutoff": cutoff.isoformat(), "retention_modifier": f"-{retention_hours} hours"},
            )
    deleted = {
        "chip_flow_snapshots": _rowcount(snapshot_result),
        "day_trading_signals": _rowcount(signal_result),
        "day_trading_candidate_snapshots": _rowcount(candidate_snapshot_result),
        "limit_up_ai_snapshots": _rowcount(limit_up_snapshot_result),
    }
    if engine.dialect.name == "postgresql":
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            for table in (
                "chip_flow_snapshots",
                "day_trading_signals",
                "day_trading_candidate_snapshots",
                "limit_up_ai_snapshots",
            ):
                try:
                    connection.execute(text(f"VACUUM (ANALYZE) {table}"))
                except SQLAlchemyError:
                    logger.exception("operational database vacuum failed for %s", table)
    logger.warning("operational database retention cleanup completed: %s", deleted)
    return deleted
