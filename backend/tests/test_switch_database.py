import json
from pathlib import Path
from types import SimpleNamespace

from scripts import switch_database


def test_replace_env_value_preserves_other_settings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=development\nDATABASE_URL=old\nTOKEN=secret\n", encoding="utf-8")

    switch_database.replace_env_value(env_file, "DATABASE_URL", "sqlite:///./data/local.db")

    assert env_file.read_text(encoding="utf-8") == (
        "APP_ENV=development\n"
        "DATABASE_URL=sqlite:///./data/local.db\n"
        "TOKEN=secret\n"
    )


def test_discover_railway_remote_url_uses_public_url(monkeypatch) -> None:
    values = {
        "DATABASE_URL": "postgresql://private:secret@postgres.railway.internal:5432/railway",
        "DATABASE_PUBLIC_URL": "postgresql://public:secret@example.com:1234/railway",
    }
    monkeypatch.setattr(switch_database.shutil, "which", lambda _: "railway")
    monkeypatch.setattr(
        switch_database.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(values)),
    )

    assert switch_database.discover_railway_remote_url() == values["DATABASE_PUBLIC_URL"]


def test_discover_railway_remote_url_does_not_fall_back_to_private_url(monkeypatch) -> None:
    monkeypatch.setattr(switch_database.shutil, "which", lambda _: "railway")
    monkeypatch.setattr(
        switch_database.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps({"DATABASE_URL": "postgresql://private-host/database"})
        ),
    )

    assert switch_database.discover_railway_remote_url() == ""
