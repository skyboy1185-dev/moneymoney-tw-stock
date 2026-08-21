from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint, create_engine, select
from sqlalchemy.engine import make_url

from app.services.database_sync import normalize_database_url, sync_databases


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def sample_metadata() -> tuple[MetaData, Table]:
    metadata = MetaData()
    records = Table(
        "records",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("code", String(20), nullable=False),
        Column("value", String(100), nullable=False),
        UniqueConstraint("code"),
    )
    return metadata, records


def test_sync_updates_source_rows_and_preserves_target_only_rows(tmp_path: Path) -> None:
    metadata, records = sample_metadata()
    source_url = database_url(tmp_path / "source.db")
    target_url = database_url(tmp_path / "target.db")
    source = create_engine(source_url)
    target = create_engine(target_url)
    metadata.create_all(source)
    metadata.create_all(target)
    with source.begin() as connection:
        connection.execute(records.insert(), [
            {"id": 1, "code": "A", "value": "new"},
            {"id": 3, "code": "C", "value": "added"},
        ])
    with target.begin() as connection:
        connection.execute(records.insert(), [
            {"id": 1, "code": "A", "value": "old"},
            {"id": 2, "code": "B", "value": "target-only"},
        ])

    result = sync_databases(source_url, target_url, metadata=metadata, batch_size=1)

    with target.connect() as connection:
        rows = connection.execute(select(records).order_by(records.c.id)).mappings().all()
    assert [dict(row) for row in rows] == [
        {"id": 1, "code": "A", "value": "new"},
        {"id": 2, "code": "B", "value": "target-only"},
        {"id": 3, "code": "C", "value": "added"},
    ]
    assert result.total_rows == 2
    assert result.skipped_tables == []


def test_sync_rolls_back_all_target_writes_on_conflict(tmp_path: Path) -> None:
    metadata = MetaData()
    records = Table(
        "records",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("code", String(20), nullable=False, unique=True),
        Column("alias", String(20), nullable=False, unique=True),
        Column("value", String(100), nullable=False),
    )
    source_url = database_url(tmp_path / "source.db")
    target_url = database_url(tmp_path / "target.db")
    source = create_engine(source_url)
    target = create_engine(target_url)
    metadata.create_all(source)
    metadata.create_all(target)
    with source.begin() as connection:
        connection.execute(records.insert(), [
            {"id": 1, "code": "C", "alias": "Z", "value": "would-insert"},
            {"id": 2, "code": "B", "alias": "X", "value": "ambiguous"},
        ])
    with target.begin() as connection:
        connection.execute(records.insert(), [
            {"id": 1, "code": "A", "alias": "X", "value": "must-stay"},
            {"id": 9, "code": "B", "alias": "Y", "value": "existing"},
        ])

    with pytest.raises(RuntimeError, match="multiple target records"):
        sync_databases(source_url, target_url, metadata=metadata, batch_size=1)

    with target.connect() as connection:
        rows = connection.execute(select(records).order_by(records.c.id)).mappings().all()
    assert [dict(row) for row in rows] == [
        {"id": 1, "code": "A", "alias": "X", "value": "must-stay"},
        {"id": 9, "code": "B", "alias": "Y", "value": "existing"},
    ]


def test_sync_reconciles_business_keys_and_remaps_foreign_keys(tmp_path: Path) -> None:
    metadata = MetaData()
    parents = Table(
        "parents",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("business_key", String(20), nullable=False, unique=True),
        Column("value", String(100), nullable=False),
    )
    children = Table(
        "children",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", ForeignKey("parents.id"), nullable=False),
        Column("name", String(20), nullable=False),
        UniqueConstraint("parent_id", "name"),
    )
    source_url = database_url(tmp_path / "source.db")
    target_url = database_url(tmp_path / "target.db")
    source = create_engine(source_url)
    target = create_engine(target_url)
    metadata.create_all(source)
    metadata.create_all(target)
    with source.begin() as connection:
        connection.execute(parents.insert(), {
            "id": 1, "business_key": "same-parent", "value": "source-wins"
        })
        connection.execute(children.insert(), {"id": 1, "parent_id": 1, "name": "child"})
    with target.begin() as connection:
        connection.execute(parents.insert(), {
            "id": 9, "business_key": "same-parent", "value": "old"
        })

    sync_databases(source_url, target_url, metadata=metadata, batch_size=1)

    with target.connect() as connection:
        parent = connection.execute(select(parents)).mappings().one()
        child = connection.execute(select(children)).mappings().one()
    assert dict(parent) == {"id": 9, "business_key": "same-parent", "value": "source-wins"}
    assert dict(child) == {"id": 1, "parent_id": 9, "name": "child"}


def test_sync_preserves_target_row_when_source_id_collides_with_different_business_key(
    tmp_path: Path,
) -> None:
    metadata, records = sample_metadata()
    source_url = database_url(tmp_path / "source.db")
    target_url = database_url(tmp_path / "target.db")
    source = create_engine(source_url)
    target = create_engine(target_url)
    metadata.create_all(source)
    metadata.create_all(target)
    with source.begin() as connection:
        connection.execute(records.insert(), {"id": 1, "code": "NEW", "value": "source"})
    with target.begin() as connection:
        connection.execute(records.insert(), {"id": 1, "code": "OLD", "value": "target"})

    sync_databases(source_url, target_url, metadata=metadata)

    with target.connect() as connection:
        rows = connection.execute(select(records).order_by(records.c.id)).mappings().all()
    assert [dict(row) for row in rows] == [
        {"id": 1, "code": "OLD", "value": "target"},
        {"id": 2, "code": "NEW", "value": "source"},
    ]


def test_normalize_database_url_resolves_local_sqlite_path(tmp_path: Path) -> None:
    normalized = normalize_database_url("sqlite:///./data/local.db", base_dir=tmp_path)
    assert Path(make_url(normalized).database or "") == (tmp_path / "data" / "local.db").resolve()


def test_sync_rejects_same_database(tmp_path: Path) -> None:
    metadata, _ = sample_metadata()
    url = database_url(tmp_path / "same.db")
    with pytest.raises(ValueError, match="same"):
        sync_databases(url, url, metadata=metadata)


def test_sync_skips_identical_rows_with_equivalent_datetime_timezones(tmp_path: Path) -> None:
    metadata = MetaData()
    records = Table(
        "records",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("recorded_at", DateTime(timezone=True), nullable=False),
    )
    source_url = database_url(tmp_path / "source.db")
    target_url = database_url(tmp_path / "target.db")
    source = create_engine(source_url)
    target = create_engine(target_url)
    metadata.create_all(source)
    metadata.create_all(target)
    value = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    with source.begin() as connection:
        connection.execute(records.insert(), {"id": 1, "recorded_at": value})
    with target.begin() as connection:
        connection.execute(records.insert(), {"id": 1, "recorded_at": value})

    result = sync_databases(source_url, target_url, metadata=metadata)

    assert result.total_rows == 0
