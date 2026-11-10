"""Shared helpers for fast, isolated web tests."""

from __future__ import annotations

import atexit
import shutil
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, null, table, text
from sqlalchemy import column as sql_column
from sqlalchemy import inspect as sql_inspect
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from pandrator.runtime import DataPaths
from pandrator.web.database import upgrade_database

_TEMPLATE_DIRECTORY = tempfile.TemporaryDirectory(prefix="pandrator-web-test-schema-")
atexit.register(_TEMPLATE_DIRECTORY.cleanup)
_TEMPLATE_LOCK = threading.Lock()
_TEMPLATE_DATABASE: Path | None = None


def prepare_web_test_data_root(value: str | Path) -> DataPaths:
    """Create an isolated data root seeded from one migrated database template."""
    global _TEMPLATE_DATABASE

    paths = DataPaths.from_value(value).ensure()
    with _TEMPLATE_LOCK:
        if _TEMPLATE_DATABASE is None:
            template_paths = DataPaths.from_value(_TEMPLATE_DIRECTORY.name).ensure()
            upgrade_database(template_paths.database)
            _TEMPLATE_DATABASE = template_paths.database
        shutil.copy2(_TEMPLATE_DATABASE, paths.database)
    return paths


@contextmanager
def suspended_triggers(session: Session, *names: str) -> Iterator[None]:
    """Drop the named triggers for one block, then restore each from its stored SQL.

    Emulates corrupt historical rows that a scope trigger would now refuse. Flush the
    seed inside the block, because SQLite fires triggers when the statement runs.

    The drop runs inside a transaction: in the driver's legacy mode, DDL issued before
    any write would otherwise commit on its own, and a failed seed would lose the
    trigger for the rest of the database's life. So a rollback, after a failed flush or
    a later failure, restores the triggers, and a normal exit re-creates them.

    Every statement runs on the session's connection, not the session: SQLAlchemy 2.1
    autoflushes before text statements too, which would flush the seed before the drop.
    """
    lookup = text("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name")
    connection = session.connection()
    definitions: list[tuple[str, str]] = []
    for name in names:
        sql = connection.scalar(lookup, {"name": name})
        assert sql is not None, f"trigger {name!r} does not exist"
        definitions.append((name, sql))
    if not connection.connection.dbapi_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")
    try:
        for name, _ in definitions:
            connection.execute(text(f'DROP TRIGGER IF EXISTS "{name}"'))
        yield
    finally:
        # A failed flush leaves the session inactive; its rollback restores the triggers.
        if session.is_active:
            for name, sql in definitions:
                if connection.scalar(lookup, {"name": name}) is None:
                    connection.execute(text(sql))


def present_columns(connection: Connection, table_name: str) -> set[str]:
    """Return the columns the table has in the database's current schema."""
    rows = connection.exec_driver_sql(f'PRAGMA table_info("{table_name}")').all()
    return {row[1] for row in rows}


def insert_at_revision(session: Session, *objects: object) -> None:
    """Insert transient model objects into a database held at an older revision.

    A seed at a pinned revision is the test's own data, so it bypasses the ORM, whose
    current models name columns the old table lacks. Each object contributes the
    attributes it sets, plus the model's Python defaults for the unset ones, restricted
    to the columns the table has. An attribute that was set to a column the revision
    lacks raises, naming it. An explicit None is stored as NULL, not replaced by a
    default. The defaults are written back to the objects, so ``obj.id`` reads.

    Product code must not run against the schema this seeds; it runs only at head.
    """
    connection = session.connection()
    for obj in objects:
        state = sql_inspect(obj)
        mapper = state.mapper
        present = present_columns(connection, mapper.local_table.name)
        values: dict[str, object] = {}
        defaults: dict[str, object] = {}
        for attribute in mapper.column_attrs:
            source = attribute.columns[0]
            if attribute.key in state.dict:
                if source.name not in present:
                    raise AssertionError(
                        f"{mapper.local_table.name}.{source.name} is set but the revision lacks it"
                    )
                value = state.dict[attribute.key]
                values[source.name] = null() if value is None else value
            elif source.name in present and source.default is not None:
                default = source.default
                if default.is_callable:
                    value = default.arg(None)
                elif default.is_scalar:
                    value = default.arg
                else:
                    continue
                values[source.name] = value
                defaults[attribute.key] = value
        target = table(
            mapper.local_table.name,
            *[sql_column(name, mapper.local_table.c[name].type) for name in values],
        )
        connection.execute(target.insert().values(values))
        for key, value in defaults.items():
            set_committed_value(obj, key, value)


class _RevisionRow:
    """A row read at an old revision; an attribute the revision lacks raises."""

    def __init__(self, model_name: str, values: dict[str, object], missing: set[str]) -> None:
        self.__dict__["_values"] = values
        self.__dict__["_missing"] = missing
        self.__dict__["_model_name"] = model_name

    def __getattr__(self, name: str) -> object:
        values = self.__dict__["_values"]
        if name in values:
            return values[name]
        if name in self.__dict__["_missing"]:
            raise AttributeError(f"{self.__dict__['_model_name']}.{name} is absent at this revision")
        raise AttributeError(name)


def get_at_revision(session: Session, model: type, ident: object) -> _RevisionRow | None:
    """Load one row by primary key from an older revision, or None.

    Selects only the columns the table has. Reading an attribute the revision lacks
    raises AttributeError naming it.
    """
    connection = session.connection()
    mapper = sql_inspect(model)
    local = mapper.local_table
    present = present_columns(connection, local.name)
    attributes = [attribute for attribute in mapper.column_attrs]
    selected = [a for a in attributes if a.columns[0].name in present]
    primary_key = mapper.primary_key[0]
    statement = (
        table(local.name, *[sql_column(a.columns[0].name, a.columns[0].type) for a in selected])
        .select()
    )
    key_column = sql_column(primary_key.name, primary_key.type)
    row = connection.execute(statement.where(key_column == ident)).mappings().first()
    if row is None:
        return None
    values = {a.key: row[a.columns[0].name] for a in selected}
    missing = {a.key for a in attributes if a.columns[0].name not in present}
    return _RevisionRow(model.__name__, values, missing)


PROJECT_LINK_TABLES = ("project_link_sessions", "project_link_submissions", "project_links")


def drop_project_link_tables(connection: Connection) -> None:
    """Drop the tables whose key points at a table that only exists from 0073.

    Revision 0001 builds them from today's metadata. At an older revision they hold a
    key to ``browser_auth_sessions``, so deleting a project fails there. They are
    unused at those revisions, so dropping them leaves the schema under test intact.
    """
    for name in PROJECT_LINK_TABLES:
        connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{name}"')
