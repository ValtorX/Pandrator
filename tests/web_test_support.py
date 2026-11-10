"""Shared helpers for fast, isolated web tests."""

from __future__ import annotations

import atexit
import shutil
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

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
    """
    lookup = text("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name")
    definitions: list[tuple[str, str]] = []
    for name in names:
        sql = session.scalar(lookup, {"name": name})
        assert sql is not None, f"trigger {name!r} does not exist"
        definitions.append((name, sql))
    connection = session.connection()
    if not connection.connection.dbapi_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")
    try:
        for name, _ in definitions:
            session.execute(text(f'DROP TRIGGER IF EXISTS "{name}"'))
        yield
    finally:
        # A failed flush leaves the session inactive; its rollback restores the triggers.
        if session.is_active:
            for name, sql in definitions:
                if session.scalar(lookup, {"name": name}) is None:
                    session.execute(text(sql))
