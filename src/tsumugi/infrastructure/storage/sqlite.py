"""The document store, on SQLite.

Versions are append-only (ADR-0010). Ingesting an edited file adds a revision
and moves the ``is_current`` flag; it never overwrites, because an anchor into
the old revision has to keep resolving as history.

The store therefore holds a complete plaintext copy of the corpus. That is the
first sentence of ``docs/threat-model.md`` and the reason ``forget`` vacuums.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

from ...domain.document import Block, Document, DocumentId, Section
from ...domain.hashing import ContentHash
from ...domain.span import Span
from ...errors import StorageError

__all__ = ["SqliteDocumentStore"]


class SqliteDocumentStore:
    """Satisfies :class:`~tsumugi.ports.store.DocumentStore`."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def put(self, document: Document) -> bool:
        document.verify()
        version = str(document.version)

        held = self._connection.execute(
            "SELECT 1 FROM documents WHERE document_id = ? AND version = ?",
            (document.document_id, version),
        ).fetchone()
        if held is not None:
            return False

        with self._connection:
            self._connection.execute(
                "UPDATE documents SET is_current = 0 WHERE document_id = ?",
                (document.document_id,),
            )
            self._connection.execute(
                "INSERT INTO documents (document_id, version, source_path, media_type, "
                "content, structure, metadata, ingested_at, is_current) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    document.document_id,
                    version,
                    document.source_path,
                    document.media_type,
                    document.content,
                    json.dumps(_structure(document), ensure_ascii=False),
                    json.dumps(dict(document.metadata), ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                ),
            )
        return True

    def get(self, document_id: DocumentId, version: ContentHash | None = None) -> Document | None:
        if version is None:
            row = self._connection.execute(
                "SELECT * FROM documents WHERE document_id = ? AND is_current = 1",
                (document_id,),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT * FROM documents WHERE document_id = ? AND version = ?",
                (document_id, str(version)),
            ).fetchone()
        return _document(row) if row is not None else None

    def current_version(self, document_id: DocumentId) -> ContentHash | None:
        row = self._connection.execute(
            "SELECT version FROM documents WHERE document_id = ? AND is_current = 1",
            (document_id,),
        ).fetchone()
        return ContentHash.parse(row["version"]) if row is not None else None

    def versions(self, document_id: DocumentId) -> Sequence[ContentHash]:
        rows = self._connection.execute(
            "SELECT version FROM documents WHERE document_id = ? ORDER BY ingested_at, version",
            (document_id,),
        ).fetchall()
        return [ContentHash.parse(row["version"]) for row in rows]

    def by_path(self, source_path: str) -> Document | None:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE source_path = ? AND is_current = 1",
            (source_path,),
        ).fetchone()
        return _document(row) if row is not None else None

    def all_current(self) -> Iterator[Document]:
        # Ordered by id rather than by insertion: a build has to be
        # reproducible (ADR-0003), and an unstable order at the bottom makes
        # that unachievable at the top.
        rows = self._connection.execute(
            "SELECT * FROM documents WHERE is_current = 1 ORDER BY document_id"
        ).fetchall()
        for row in rows:
            yield _document(row)

    def forget(self, document_id: DocumentId) -> int:
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM documents WHERE document_id = ?", (document_id,)
            )
            removed = cursor.rowcount
        # Deleting rows leaves the text in free pages. For a file that holds a
        # person's notes, "removed from the table" is not removed.
        self._connection.execute("VACUUM")
        return removed

    def count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(DISTINCT document_id) AS n FROM documents WHERE is_current = 1"
        ).fetchone()
        return int(row["n"])


def _structure(document: Document) -> dict[str, list[dict[str, object]]]:
    return {
        "sections": [
            {
                "heading": s.heading,
                "level": s.level,
                "start": s.span.start,
                "end": s.span.end,
                "heading_start": s.heading_span.start if s.heading_span else None,
                "heading_end": s.heading_span.end if s.heading_span else None,
            }
            for s in document.sections
        ],
        "blocks": [
            {"kind": b.kind, "start": b.span.start, "end": b.span.end, "level": b.level}
            for b in document.blocks
        ],
    }


def _document(row: sqlite3.Row) -> Document:
    try:
        structure = json.loads(row["structure"])
        metadata = json.loads(row["metadata"])
    except json.JSONDecodeError as error:
        raise StorageError(f"corrupt structure for {row['document_id']}: {error}") from error

    sections = tuple(
        Section(
            heading=s["heading"],
            level=s["level"],
            span=Span(s["start"], s["end"]),
            heading_span=(
                Span(s["heading_start"], s["heading_end"])
                if s.get("heading_start") is not None
                else None
            ),
        )
        for s in structure.get("sections", [])
    )
    blocks = tuple(
        Block(kind=b["kind"], span=Span(b["start"], b["end"]), level=b.get("level"))
        for b in structure.get("blocks", [])
    )
    return Document(
        document_id=row["document_id"],
        version=ContentHash.parse(row["version"]),
        source_path=row["source_path"],
        media_type=row["media_type"],
        content=row["content"],
        sections=sections,
        blocks=blocks,
        metadata=metadata,
    )
