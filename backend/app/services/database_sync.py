from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import Engine, Integer, MetaData, UniqueConstraint, create_engine, inspect, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url


@dataclass(slots=True)
class TableSyncResult:
    table: str
    rows: int


@dataclass(slots=True)
class DatabaseSyncResult:
    source: str
    target: str
    tables: list[TableSyncResult] = field(default_factory=list)
    skipped_tables: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(item.rows for item in self.tables)


def normalize_database_url(value: str, *, base_dir: Path) -> str:
    value = value.strip().strip('"').strip("'")
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    if value.startswith("sqlite:///./"):
        relative_path = value.removeprefix("sqlite:///./")
        return f"sqlite:///{(base_dir / relative_path).resolve().as_posix()}"
    return value


def safe_database_label(value: str) -> str:
    url = make_url(value)
    if url.get_backend_name() == "sqlite":
        return f"sqlite:///{url.database}"
    host = url.host or "unknown-host"
    port = f":{url.port}" if url.port else ""
    database = f"/{url.database}" if url.database else ""
    return f"{url.get_backend_name()}://{host}{port}{database}"


def create_sync_engine(database_url: str, *, connect_timeout: int = 8) -> Engine:
    backend = make_url(database_url).get_backend_name()
    connect_args: dict[str, object] = {}
    if backend == "sqlite":
        connect_args["check_same_thread"] = False
    elif backend == "postgresql":
        connect_args["connect_timeout"] = connect_timeout
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


def verify_database_connection(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def _upsert_statement(table, rows: list[dict[str, object]], dialect_name: str):
    if dialect_name == "sqlite":
        statement = sqlite_insert(table).values(rows)
    elif dialect_name == "postgresql":
        statement = postgresql_insert(table).values(rows)
    else:
        raise ValueError(f"Unsupported target database dialect: {dialect_name}")

    primary_keys = [column.name for column in table.primary_key.columns]
    if not primary_keys:
        raise ValueError(f"Table {table.name} has no primary key and cannot be synchronized safely")

    update_values = {
        column.name: statement.excluded[column.name]
        for column in table.columns
        if column.name not in primary_keys and column.name in rows[0]
    }
    if not update_values:
        return statement.on_conflict_do_nothing(index_elements=primary_keys)
    return statement.on_conflict_do_update(
        index_elements=primary_keys,
        set_=update_values,
    )


def _chunks(result, batch_size: int) -> Iterable[list[dict[str, object]]]:
    mappings = result.mappings()
    while batch := mappings.fetchmany(batch_size):
        yield [dict(row) for row in batch]


def _unique_column_groups(table) -> list[tuple[str, ...]]:
    groups: list[tuple[str, ...]] = []
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            groups.append(tuple(column.name for column in constraint.columns))
    for index in table.indexes:
        if index.unique:
            groups.append(tuple(column.name for column in index.columns))
    primary_key = tuple(column.name for column in table.primary_key.columns)
    return list(dict.fromkeys(group for group in groups if group and group != primary_key))


def _identity_key(row: dict[str, object], columns: tuple[str, ...]) -> tuple[object, ...] | None:
    values = tuple(row[column] for column in columns)
    # SQL unique constraints allow multiple NULL values, so a nullable identity
    # cannot safely be used to decide that two rows represent the same record.
    return None if any(value is None for value in values) else values


def _target_identities(connection, table):
    primary_keys = tuple(column.name for column in table.primary_key.columns)
    unique_groups = _unique_column_groups(table)
    rows = connection.execute(select(table)).mappings()
    occupied: set[tuple[object, ...]] = set()
    target_rows: dict[tuple[object, ...], dict[str, object]] = {}
    natural: dict[tuple[str, ...], dict[tuple[object, ...], tuple[object, ...]]] = {
        group: {} for group in unique_groups
    }
    for item in rows:
        row = dict(item)
        primary_key = tuple(row[name] for name in primary_keys)
        occupied.add(primary_key)
        target_rows[primary_key] = row
        for group in unique_groups:
            identity = _identity_key(row, group)
            if identity is not None:
                natural[group][identity] = primary_key
    return primary_keys, unique_groups, occupied, natural, target_rows


def _comparable_value(value: object) -> object:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _rows_match(source: dict[str, object], target: dict[str, object] | None) -> bool:
    if target is None:
        return False
    return all(
        _comparable_value(value) == _comparable_value(target.get(name))
        for name, value in source.items()
    )


def _rewrite_foreign_keys(
    table,
    row: dict[str, object],
    primary_key_remaps: dict[str, dict[tuple[object, ...], tuple[object, ...]]],
) -> None:
    for constraint in table.foreign_key_constraints:
        source_values = tuple(row[element.parent.name] for element in constraint.elements)
        referenced_table = constraint.referred_table.name
        remapped = primary_key_remaps.get(referenced_table, {}).get(source_values)
        if remapped is None:
            continue
        for element, value in zip(constraint.elements, remapped, strict=True):
            row[element.parent.name] = value


def _reconcile_primary_keys(
    connection,
    table,
    batches: Iterable[list[dict[str, object]]],
    primary_key_remaps: dict[str, dict[tuple[object, ...], tuple[object, ...]]],
) -> Iterable[list[dict[str, object]]]:
    primary_keys, unique_groups, occupied, natural, target_rows = _target_identities(
        connection, table
    )
    table_remaps = primary_key_remaps.setdefault(table.name, {})
    primary_key_column = table.c[primary_keys[0]] if len(primary_keys) == 1 else None
    can_allocate_integer_id = (
        primary_key_column is not None and isinstance(primary_key_column.type, Integer)
    )
    integer_ids = [key[0] for key in occupied if isinstance(key[0], int)]
    next_integer_id = max(integer_ids, default=0) + 1

    for batch in batches:
        reconciled: list[dict[str, object]] = []
        for row in batch:
            _rewrite_foreign_keys(table, row, primary_key_remaps)
            source_primary_key = tuple(row[name] for name in primary_keys)
            natural_matches: set[tuple[object, ...]] = set()
            identities: dict[tuple[str, ...], tuple[object, ...]] = {}
            for group in unique_groups:
                identity = _identity_key(row, group)
                if identity is None:
                    continue
                identities[group] = identity
                if target_primary_key := natural[group].get(identity):
                    natural_matches.add(target_primary_key)

            if len(natural_matches) > 1:
                raise RuntimeError(
                    f"Source row in {table.name} matches multiple target records by unique keys"
                )
            if natural_matches:
                target_primary_key = natural_matches.pop()
            elif source_primary_key not in occupied:
                target_primary_key = source_primary_key
            elif unique_groups and can_allocate_integer_id:
                target_primary_key = (next_integer_id,)
                next_integer_id += 1
            elif unique_groups:
                raise RuntimeError(
                    f"Primary key collision in {table.name} cannot be reconciled automatically"
                )
            else:
                # Without a business unique key, the primary key is the only
                # available record identity and the active/source row wins.
                target_primary_key = source_primary_key

            for name, value in zip(primary_keys, target_primary_key, strict=True):
                row[name] = value
            table_remaps[source_primary_key] = target_primary_key
            occupied.add(target_primary_key)
            for group, identity in identities.items():
                natural[group][identity] = target_primary_key
            if not _rows_match(row, target_rows.get(target_primary_key)):
                reconciled.append(row)
                target_rows[target_primary_key] = dict(row)
        if reconciled:
            yield reconciled


def _reset_postgresql_sequences(connection, metadata: MetaData) -> None:
    preparer = connection.dialect.identifier_preparer
    for table in metadata.sorted_tables:
        for column in table.primary_key.columns:
            if not column.autoincrement or len(table.primary_key.columns) != 1:
                continue
            sequence = connection.scalar(
                text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                {"table_name": table.name, "column_name": column.name},
            )
            if not sequence:
                continue
            table_name = preparer.quote(table.name)
            column_name = preparer.quote(column.name)
            connection.execute(
                text(
                    "SELECT setval(CAST(:sequence AS regclass), "
                    f"COALESCE((SELECT MAX({column_name}) FROM {table_name}), 1), "
                    f"EXISTS(SELECT 1 FROM {table_name}))"
                ),
                {"sequence": sequence},
            )


def sync_databases(
    source_url: str,
    target_url: str,
    *,
    metadata: MetaData,
    batch_size: int = 500,
    connect_timeout: int = 8,
) -> DatabaseSyncResult:
    """Merge the source into the target without deleting target-only records.

    Rows are matched by primary key. When a primary key exists in both databases,
    the source row wins. All target writes happen in one transaction, so a failed
    synchronization does not leave a partially updated target.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if make_url(source_url) == make_url(target_url):
        raise ValueError("Source and target databases are the same")

    source_engine = create_sync_engine(source_url, connect_timeout=connect_timeout)
    target_engine = create_sync_engine(target_url, connect_timeout=connect_timeout)
    result = DatabaseSyncResult(
        source=safe_database_label(source_url),
        target=safe_database_label(target_url),
    )
    try:
        verify_database_connection(source_engine)
        verify_database_connection(target_engine)
        metadata.create_all(target_engine)

        source_tables = set(inspect(source_engine).get_table_names())
        target_columns = {
            table.name: {item["name"] for item in inspect(target_engine).get_columns(table.name)}
            for table in metadata.sorted_tables
        }
        for table in metadata.sorted_tables:
            missing_target_columns = {column.name for column in table.columns} - target_columns[table.name]
            if missing_target_columns:
                names = ", ".join(sorted(missing_target_columns))
                raise RuntimeError(f"Target table {table.name} is missing columns: {names}")

        with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
            source_inspector = inspect(source_connection)
            primary_key_remaps: dict[
                str, dict[tuple[object, ...], tuple[object, ...]]
            ] = {}
            for table in metadata.sorted_tables:
                if table.name not in source_tables:
                    result.skipped_tables.append(table.name)
                    continue

                source_columns = {
                    item["name"] for item in source_inspector.get_columns(table.name)
                }
                selected_columns = [
                    column for column in table.columns if column.name in source_columns
                ]
                primary_keys = {column.name for column in table.primary_key.columns}
                if not primary_keys.issubset(source_columns):
                    raise RuntimeError(f"Source table {table.name} is missing its primary key columns")

                row_count = 0
                query_result = source_connection.execute(select(*selected_columns))
                batches = _reconcile_primary_keys(
                    target_connection,
                    table,
                    _chunks(query_result, batch_size),
                    primary_key_remaps,
                )
                for rows in batches:
                    target_connection.execute(
                        _upsert_statement(table, rows, target_engine.dialect.name)
                    )
                    row_count += len(rows)
                result.tables.append(TableSyncResult(table=table.name, rows=row_count))

            if target_engine.dialect.name == "postgresql":
                _reset_postgresql_sequences(target_connection, metadata)
        return result
    finally:
        source_engine.dispose()
        target_engine.dispose()
