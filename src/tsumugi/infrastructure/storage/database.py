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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from ...errors import StorageError

__all__ = [
    "SCHEMA_VERSION",
    "connect",
    "empty",
    "opened",
    "rebuildable_writes",
    "requires_fts5",
]

SCHEMA_VERSION: Final = 4

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
        -- The section this row is for, as offsets into the *parent* document.
        -- One row per section rather than per file: bm25 scores what it can
        -- return, which on a real-sized document is the whole difference
        -- between 65.6% and 86.1% recall.
        start       UNINDEXED,
        end         UNINDEXED,
        tokenize    = 'unicode61 remove_diacritics 0'
    );

    CREATE TABLE index_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    2: """
    -- Where each document was read from, so staleness can be checked without
    -- the caller having to remember and re-supply the corpus root. Nullable:
    -- documents ingested under schema 1 predate it, and a NULL root simply
    -- means "cannot check", which is the honest answer rather than an error.
    ALTER TABLE documents ADD COLUMN corpus_root TEXT;
    """,
    3: """
    -- One search row per *section* rather than per file, so the unit bm25
    -- scores is the unit that can be returned. `start` and `end` are offsets
    -- into the parent document, never into a copy, so anchors are unchanged.
    --
    -- The table is dropped rather than altered: FTS5 has no ADD COLUMN, and an
    -- index is derived data that `tsumugi ingest --rebuild` restores. Emptied
    -- rather than left half-populated -- an index that answers some queries
    -- from the old shape and some from the new is worse than one that answers
    -- none, because only the second kind tells you.
    DROP TABLE IF EXISTS search;
    CREATE VIRTUAL TABLE search USING fts5(
        terms,
        document_id UNINDEXED,
        version     UNINDEXED,
        start       UNINDEXED,
        end         UNINDEXED,
        tokenize    = 'unicode61 remove_diacritics 0'
    );
    DELETE FROM index_meta WHERE key = 'tokenizer';
    """,
    4: """
    -- Which FTS rows each document owns, so they can be found by seek and
    -- deleted by rowid. `document_id` on the FTS table is UNINDEXED, and FTS5
    -- cannot seek on one of those: `DELETE FROM search WHERE document_id = ?`
    -- scanned every row in the table, for every document ingested, whether or
    -- not anything was there to delete -- 3.2 ms at 14,000 rows, 12.9 ms at
    -- 70,000, so ten thousand documents took ten minutes.
    --
    -- Backfilled from what is already there, in one pass, so an existing index
    -- keeps working without `ingest --rebuild`. The tokenizer marker is left
    -- alone: nothing about the stored terms changed.
    CREATE TABLE search_rows (
        rowid_ref   INTEGER PRIMARY KEY,
        document_id TEXT    NOT NULL
    );
    CREATE INDEX search_rows_by_document ON search_rows (document_id);
    INSERT INTO search_rows (rowid_ref, document_id)
        SELECT rowid, document_id FROM search;
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
    #
    # **But this file is not only the index.** The ledger lives in it too, and
    # a ledger is the record of what was sent and what was used -- evidence,
    # not something a re-run reconstructs. So `synchronous` stays at its
    # default here and is lowered only around the writes that *are*
    # rebuildable; see `rebuildable_writes`.
    connection.execute("PRAGMA journal_mode = WAL")
    requires_fts5(connection)
    _migrate(connection)
    return connection


@contextmanager
def opened(path: Path | str, *, create: bool = True) -> Iterator[sqlite3.Connection]:
    """`connect`, closed when the block ends. For a caller that is not a CLI.

    `connect` hands back a raw `sqlite3.Connection` and the caller owns it.
    That is right for the CLI, which has a registry that closes everything at
    exit, and it is a trap for a library caller: the walk-through script in
    `examples/` leaked one on its first version, and Windows then refused to
    delete the directory it lived in.

    **Additive, and deliberately not a change to `connect`.** The surface is
    promised now (ADR-0023), and `with connect(...)` already means something
    else in `sqlite3` -- it commits a transaction and leaves the connection
    open, which is exactly the mistake this exists to prevent. A caller who
    writes `with opened(...)` cannot get that by accident.

        with opened(Path.home() / ".tsumugi" / "index.db") as connection:
            store = SqliteDocumentStore(connection)
    """
    connection = connect(path, create=create)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def rebuildable_writes(connection: sqlite3.Connection) -> Iterator[None]:
    """Commit without waiting for the disk, for writes a re-run would redo.

        with rebuildable_writes(connection):
            ingest_paths(...)

    In WAL mode SQLite's default `synchronous = FULL` flushes the log to disk
    on **every commit**, and ingest commits twice a document -- once for the
    store, once for the index. Measured over 300 documents of 6,800
    characters, five interleaved rounds, best of each:

        FULL      1.73 s      5.78 ms a document
        NORMAL    1.21 s      4.04 ms a document      -30%
        OFF       1.12 s      3.74 ms a document      -35%

    `NORMAL` takes most of it, and the five points `OFF` adds are not the same
    kind of thing: `OFF` can leave the database **corrupt** after a power cut,
    while `NORMAL` can only lose whole commits from the end.

    **What is given up, precisely.** An application crash -- an exception, a
    `Ctrl-C`, the process killed -- loses nothing at all; the WAL still holds
    every committed row. Only a power cut or an OS crash can drop the most
    recent commits. So the property that an interrupted ingest keeps what it
    already did, which is the reason ingest commits per document rather than
    in batches, is exactly preserved.

    And what a power cut would cost here is a re-run: `ingest` is incremental,
    skips a document whose hash it already holds, and the corpus is the files
    on disk (ADR-0014). The index is derived; losing its tail is not losing
    anything.

    **Not for the ledger**, which shares this database and is not derived from
    anything. A package that was sent cannot be reconstructed by re-reading
    the corpus, which is why this is a block a caller enters deliberately
    rather than a setting on the connection.
    """
    previous = connection.execute("PRAGMA synchronous").fetchone()[0]
    connection.execute("PRAGMA synchronous = NORMAL")
    try:
        yield
    finally:
        connection.execute(f"PRAGMA synchronous = {previous}")


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


def empty(connection: sqlite3.Connection) -> None:
    """Discard everything the index holds, keeping the schema.

    Done inside the connection rather than by deleting the file, because the
    caller is usually holding that file open and because the path is often one
    the user named. Vacuumed and checkpointed afterwards: an index is a
    complete plaintext copy of a corpus, and "the rows are gone" is not the
    same as "the text is gone".
    """
    with connection:
        connection.execute("DELETE FROM documents")
        connection.execute("DELETE FROM search")
        connection.execute("DELETE FROM search_rows")
        connection.execute("DELETE FROM index_meta")
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("VACUUM")
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
