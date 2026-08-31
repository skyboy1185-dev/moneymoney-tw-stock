import json

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import database
from app.config import Settings


def test_database_connection_info_hides_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        database,
        "settings",
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://user:secret@postgres-bml6.railway.internal:5432/railway",
        ),
    )

    info = database.database_connection_info("postgres-bml6.railway.internal")

    assert info["host"] == "postgres-bml6.railway.internal"
    assert info["database"] == "railway"
    assert info["matchesExpectedHost"] is True
    assert "secret" not in json.dumps(info)
    assert "user" not in json.dumps(info)


def test_database_connection_info_detects_wrong_host(monkeypatch) -> None:
    monkeypatch.setattr(
        database,
        "settings",
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://user:secret@old-postgres.railway.internal:5432/railway",
        ),
    )

    info = database.database_connection_info("postgres-bml6.railway.internal")

    assert info["matchesExpectedHost"] is False


def test_database_runtime_status_reports_sqlite_storage(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "health.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database,
        "settings",
        Settings(_env_file=None, database_url=f"sqlite:///{db_path.as_posix()}"),
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE limit_up_ai_snapshots (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO limit_up_ai_snapshots DEFAULT VALUES"))

    with Session(engine) as session:
        status = database.database_runtime_status(session)

    assert status["connected"] is True
    assert status["databaseSizeMB"] is not None
    assert {"name": "limit_up_ai_snapshots", "estimatedRows": 1, "sizeMB": None} in status[
        "operationalTables"
    ]
