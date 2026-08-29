"""Where documents and their versions live.

The store keeps the text it anchored (ADR-0010), which is what lets evidence
survive an edit. It therefore holds a complete plaintext copy of whatever
corpus it was built from, and ``docs/threat-model.md`` says so before it says
anything else.

Versions are append-only. ``put`` on an edited document adds a revision; it
never replaces one, because an anchor into the old revision has to keep
resolving.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Protocol, runtime_checkable

from ..domain.document import Document, DocumentId
from ..domain.hashing import ContentHash

__all__ = ["DocumentStore"]


@runtime_checkable
class DocumentStore(Protocol):
    """Documents, addressed by identity and by revision."""

    def put(self, document: Document) -> bool:
        """Store a revision.

        Returns ``True`` when this revision is new, ``False`` when it was
        already held -- which is how an ingest run reports what changed without
        a second pass.
        """
        ...

    def get(self, document_id: DocumentId, version: ContentHash | None = None) -> Document | None:
        """One revision, or the current one when ``version`` is ``None``."""
        ...

    def current_version(self, document_id: DocumentId) -> ContentHash | None: ...

    def versions(self, document_id: DocumentId) -> Sequence[ContentHash]:
        """Every stored revision, oldest first."""
        ...

    def by_path(self, source_path: str) -> Document | None:
        """The current revision of the document at a path, if it is held."""
        ...

    def all_current(self) -> Iterator[Document]:
        """Every document at its current revision, in a stable order.

        Stable because a build has to be reproducible (ADR-0003), and a store
        that iterates in insertion or hash order makes that impossible from the
        bottom up.
        """
        ...

    def forget(self, document_id: DocumentId) -> int:
        """Remove a document and every revision of it. Returns how many went.

        Must leave nothing recoverable -- see the test that vacuums and then
        greps the database file.
        """
        ...

    def count(self) -> int:
        """How many documents are held, counting each one once."""
        ...
