from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import BACKEND_ROOT, Settings


def test_explicit_local_runtime_mode() -> None:
    settings = Settings(_env_file=None, app_runtime_mode="local")
    assert settings.runtime_mode == "local"


def test_auto_detects_railway_runtime_mode(monkeypatch) -> None:
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    settings = Settings(_env_file=None, app_runtime_mode="auto")
    assert settings.runtime_mode == "railway"


def test_auto_defaults_to_local_outside_railway(monkeypatch) -> None:
    for name in ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_SERVICE_ID"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None, app_runtime_mode="auto")
    assert settings.runtime_mode == "local"


def test_relative_sqlite_database_is_resolved_from_backend_root() -> None:
    settings = Settings(_env_file=None, database_url="sqlite:///./data/example.db")
    assert Path(make_url(settings.database_url).database or "") == (
        BACKEND_ROOT / "data" / "example.db"
    ).resolve()
