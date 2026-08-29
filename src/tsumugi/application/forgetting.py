"""Removing a document, and meaning it.

The index holds the text it anchored ([ADR 0010](../../docs/adr/0010-the-index-stores-the-text.md)),
so deleting a file from the corpus does not delete the evidence. That is
deliberate — it is what lets an anchor survive an edit — and it is exactly why
this has to exist: without it, "I deleted that note" and "tsumugi no longer has
that note" are different statements, and only one of them is true.

Removing rows is not removing text. SQLite leaves deleted pages in the file
until it is vacuumed, so a `forget` that only issued DELETE would leave the
passage recoverable with a hex editor, in a file that is a complete plaintext
copy of somebody's notes. The store vacuums, and a test greps the database
afterwards.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.document import DocumentId
from ..ports.index import Index
from ..ports.store import DocumentStore

__all__ = ["Forgotten", "forget_documents", "forget_path"]


@dataclass(frozen=True, slots=True)
class Forgotten:
    """What was removed."""

    document_id: DocumentId
    source_path: str
    versions: int


def forget_path(source_path: str, *, store: DocumentStore, index: Index) -> Forgotten | None:
    """Remove the document at ``source_path``, every revision of it.

    ``None`` when nothing was held there. Not an error: asking to forget
    something already gone is a reasonable thing to do twice.
    """
    document = store.by_path(source_path)
    if document is None:
        return None

    versions = len(store.versions(document.document_id))
    index.remove(document.document_id)
    store.forget(document.document_id)
    return Forgotten(document.document_id, document.source_path, versions)


def forget_documents(
    source_paths: Sequence[str], *, store: DocumentStore, index: Index
) -> tuple[list[Forgotten], list[str]]:
    """Forget several. Returns what went, and what was not there to go."""
    removed: list[Forgotten] = []
    missing: list[str] = []
    for path in source_paths:
        gone = forget_path(path, store=store, index=index)
        if gone is None:
            missing.append(path)
        else:
            removed.append(gone)
    return removed, missing
