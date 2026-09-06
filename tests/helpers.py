"""Small builders shared across the suite.

Not a fixture module: these are plain functions so a test can call one twice
with different arguments in the same line and stay readable.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from tsumugi.domain.document import Block, Document, Section
from tsumugi.domain.hashing import ContentHash


def build_document(
    source_path: str,
    content: str,
    *,
    media_type: str = "text/markdown",
    sections: tuple[Section, ...] = (),
    blocks: tuple[Block, ...] = (),
    metadata: Mapping[str, str] | None = None,
) -> Document:
    """A document whose version really is the hash of its content."""
    return Document(
        document_id=Document.identity_for(source_path),
        version=ContentHash.of(content),
        source_path=source_path,
        media_type=media_type,
        content=content,
        sections=sections,
        blocks=blocks,
        metadata=dict(metadata or {}),
    )


#: How to undo each migration, newest first, so a test can hand `connect()` a
#: database that is genuinely at an older schema. **One entry per migration
#: above 1**, and a test in `test_index_scaling.py` fails the moment a
#: migration is added without its undo -- the alternative was every test that
#: builds an old database knowing privately which tables and columns to drop,
#: and each new migration breaking all of them at once.
#:
#: Migration 3 has no undo of its own: it drops and recreates `search`, so
#: re-running it on either shape is what a real upgrade does anyway.
_UNDO: dict[int, tuple[str, ...]] = {
    4: ("DROP TABLE IF EXISTS search_rows",),
    3: (),
    2: ("ALTER TABLE documents DROP COLUMN corpus_root",),
}


def rewind_to_schema(connection: sqlite3.Connection, version: int) -> None:
    """Undo every migration above ``version`` and stamp the database with it.

    Used to build a database an older tsumugi would have written, with its
    documents in place, so that opening it again exercises the real upgrade
    path rather than a fresh install.
    """
    from tsumugi.infrastructure.storage.database import SCHEMA_VERSION

    assert 1 <= version < SCHEMA_VERSION, version
    with connection:
        for step in range(SCHEMA_VERSION, version, -1):
            for statement in _UNDO[step]:
                connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {version}")


def undone_migrations() -> set[int]:
    """Which migrations `rewind_to_schema` knows how to undo."""
    return set(_UNDO)
