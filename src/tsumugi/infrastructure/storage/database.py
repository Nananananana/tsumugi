"""One SQLite file, opened once, migrated explicitly.

The store and the index live in the same file because they describe the same
corpus and a person should have one thing to back up, one thing to delete, and
one thing to keep off a synced folder (``docs/threat-model.md``).

Migrations are explicit and numbered. There is no "create if not exists and
hope": a schema that drifted silently is a schema nobody can reason about, and
this file holds evidence.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Final

from ...errors import StorageError

__all__ = ["SCHEMA_VERSION", "connect", "requires_fts5"]

SCHEMA_VERSION: Final = 1

_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE documents (
        document_id  TEXT    NOT NULL,
        version      TEXT    NOT NULL,
        source_path  TEXT    NOT NULL,
        media_type   TEXT    NOT NULL,
        content      TEXT    NOT NULL,
        structure    TEXT    NOT NULL,
        metadata     TEXT    NOT NULL,
        ingested_at  TEXT    NOT NULL,
        is_current   INTEGER NOT NULL,
        PRIMARY KEY (document_id, version)
    );
    CREATE INDEX documents_by_path    ON documents (source_path, is_current);
    CREATE INDEX documents_by_current ON documents (document_id, is_current);

    CREATE VIRTUAL TABLE search USING fts5(
        terms,
        document_id UNINDEXED,
        version     UNINDEXED,
        tokenize    = 'unicode61 remove_diacritics 0'
    );

    CREATE TABLE index_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
}


def requires_fts5(connection: sqlite3.Connection) -> None:
    """Fail loudly and early if this SQLite has no FTS5.

    FTS5 is an optional compile-time feature. Without it the search layer
    cannot work at all (ADR-0007), and finding that out through forty confusing
    errors later is worse than one sentence here.
    """
    try:
        connection.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(x)")
        connection.execute("DROP TABLE temp.fts5_probe")
    except sqlite3.OperationalError as error:
        raise StorageError(
            "this Python's SQLite was built without FTS5, which tsumugi's search "
            f"requires (sqlite {sqlite3.sqlite_version}). Reinstall Python from "
            "python.org or your package manager's standard build."
        ) from error


def connect(path: Path | str, *, create: bool = True) -> sqlite3.Connection:
    """Open the index, migrating it to :data:`SCHEMA_VERSION`."""
    location = Path(path)
    if not create and not location.exists():
        raise StorageError(f"no index at {location}; run `tsumugi ingest` first")
    if create:
        location.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(location)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # An index is derived data that can be rebuilt from the corpus, so
    # durability is worth less here than the write speed of an ingest run.
    connection.execute("PRAGMA journal_mode = WAL")
    requires_fts5(connection)
    _migrate(connection)
    return connection


def _migrate(connection: sqlite3.Connection) -> None:
    current: int = connection.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise StorageError(
            f"this index is at schema version {current} and this tsumugi understands "
            f"{SCHEMA_VERSION}. It was written by a newer version; upgrade rather than "
            f"letting an older one write to it."
        )
    for version in range(current + 1, SCHEMA_VERSION + 1):
        with connection:
            connection.executescript(_MIGRATIONS[version])
            connection.execute(f"PRAGMA user_version = {version}")


def transactional(connection: sqlite3.Connection) -> Callable[[], sqlite3.Connection]:
    """The connection as a context manager, named for what it does."""
    return lambda: connection
