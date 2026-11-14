"""Copy a migrated SQLite snapshot into an empty PostgreSQL public schema.

This is an explicit one-time migration tool. It never edits the source file and
refuses to copy into a target containing application rows.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import DateTime, MetaData, create_engine, func, select, text


BATCH_SIZE = 500
ALEMBIC_TABLE = "alembic_version"


def _set_sequences(connection, table_names: list[str]) -> None:
    """Advance serial sequences after explicit IDs have been copied."""
    for table_name in table_names:
        sequence = connection.execute(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {"table_name": f"public.{table_name}"},
        ).scalar_one_or_none()
        if not sequence:
            continue
        max_id = connection.execute(
            text(f'SELECT MAX("id") FROM "{table_name}"')
        ).scalar_one_or_none()
        if max_id is None:
            connection.execute(
                text("SELECT setval(CAST(:sequence AS regclass), 1, false)"),
                {"sequence": sequence},
            )
        else:
            connection.execute(
                text("SELECT setval(CAST(:sequence AS regclass), :max_id, true)"),
                {"sequence": sequence, "max_id": max_id},
            )


def migrate(source_path: Path, target_url: str) -> dict[str, int]:
    source_path = source_path.resolve()
    project_root = Path(__file__).resolve().parents[1]
    original_db = (project_root / "ravn.db").resolve()
    if source_path == original_db:
        raise ValueError("Refusing to read the original ravn.db; pass a separate copy.")
    if not source_path.is_file():
        raise FileNotFoundError("The supplied SQLite snapshot does not exist.")
    if not target_url.startswith(("postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")):
        raise ValueError("DATABASE_URL must point to PostgreSQL for this migration.")

    source_engine = create_engine(f"sqlite:///{source_path.as_posix()}")
    target_engine = create_engine(target_url)
    source_metadata = MetaData()
    target_metadata = MetaData()
    try:
        source_metadata.reflect(bind=source_engine)
        target_metadata.reflect(bind=target_engine)
        if ALEMBIC_TABLE not in source_metadata.tables or ALEMBIC_TABLE not in target_metadata.tables:
            raise ValueError("Both databases must have an Alembic version table.")
        with source_engine.connect() as source_connection:
            source_revision = source_connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
        with target_engine.connect() as target_connection:
            target_revision = target_connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
        if not source_revision or source_revision != target_revision:
            raise ValueError("SQLite snapshot and PostgreSQL target must be at the same Alembic revision.")

        tables = [table for table in target_metadata.sorted_tables if table.name != ALEMBIC_TABLE]
        source_names = set(source_metadata.tables)
        target_names = {table.name for table in tables}
        missing = target_names - source_names
        if missing:
            raise ValueError(f"SQLite snapshot is missing target tables: {', '.join(sorted(missing))}")

        with target_engine.connect() as connection:
            occupied = []
            for table in tables:
                if connection.execute(select(table).limit(1)).first() is not None:
                    occupied.append(table.name)
            if occupied:
                raise ValueError(
                    "Refusing to copy into non-empty target tables: " + ", ".join(occupied)
                )

        counts: dict[str, int] = {}
        with source_engine.connect() as source, target_engine.begin() as target:
            for target_table in tables:
                source_table = source_metadata.tables[target_table.name]
                source_columns = set(source_table.c.keys())
                target_columns = [column for column in target_table.columns if column.name in source_columns]
                target_column_names = {column.name for column in target_columns}
                required = {
                    column.name for column in target_table.columns
                    if not column.nullable and column.default is None
                    and column.server_default is None and not column.primary_key
                }
                if required - target_column_names:
                    raise ValueError(
                        f"SQLite snapshot lacks required columns in {target_table.name}: "
                        + ", ".join(sorted(required - target_column_names))
                    )

                copied = 0
                result = source.execute(select(source_table))
                while rows := result.fetchmany(BATCH_SIZE):
                    batch = []
                    for row in rows:
                        values = {name: row._mapping[name] for name in target_column_names}
                        for column in target_columns:
                            value = values[column.name]
                            if (
                                isinstance(value, datetime)
                                and isinstance(column.type, DateTime)
                                and column.type.timezone
                                and value.tzinfo is None
                            ):
                                values[column.name] = value.replace(tzinfo=timezone.utc)
                        batch.append(values)
                    if batch:
                        target.execute(target_table.insert(), batch)
                        copied += len(batch)
                counts[target_table.name] = copied

            _set_sequences(target, [table.name for table in tables])
            for table in tables:
                actual = target.execute(select(func.count()).select_from(table)).scalar_one()
                if actual != counts[table.name]:
                    raise RuntimeError(f"Row count mismatch after copying {table.name}.")
        return counts
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path, help="Path to a COPY of ravn.db migrated to head")
    args = parser.parse_args()
    load_dotenv()
    target_url = os.getenv("DATABASE_URL")
    if not target_url:
        parser.error("DATABASE_URL is not set")
    try:
        counts = migrate(args.source_db, target_url)
    except Exception as exc:
        safe_message = str(exc).replace(target_url, "<DATABASE_URL>")
        parser.exit(1, f"Migration stopped ({type(exc).__name__}): {safe_message}\n")
    print("Copied rows by table:")
    for table_name, count in counts.items():
        print(f"  {table_name}: {count}")


if __name__ == "__main__":
    main()
