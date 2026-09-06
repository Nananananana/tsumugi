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

from collections.abc import Iterator, Mapping, Sequence
from typing import Protocol, runtime_checkable

from ..domain.document import Document, DocumentId
from ..domain.hashing import ContentHash

__all__ = ["DocumentStore"]


@runtime_checkable
class DocumentStore(Protocol):
    """Documents, addressed by identity and by revision."""

    def put(self, document: Document, *, corpus_root: str | None = None) -> bool:
        """Store a revision, and where it was read from.

        Returns ``True`` when this revision is new, ``False`` when it was
        already held -- which is how an ingest run reports what changed without
        a second pass.

        ``corpus_root`` is remembered so that staleness can be checked later
        without the caller having to supply the folder again. ``None`` means
        the store cannot say, which is different from "unchanged".
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

    def current_versions(self) -> Sequence[ContentHash]:
        """Every current document's version, and nothing else.

        `corpus_state` hashes these, once per build, to say which corpus a
        package was built against. Doing it through `all_current` rehydrated
        every document in the store -- parsing each one's structure and
        metadata JSON -- to read one column: **68% of a `context` call at
        10,000 documents**, and it grew with the corpus rather than with the
        answer.

        Order does not matter here, because `corpus_state` sorts before
        hashing; a store is free to return whatever the cheapest query gives.
        """
        ...

    def current_roots(self) -> Mapping[DocumentId, str]:
        """Where each current document was read from, for the ones that know.

        Same shape of waste as `current_versions`, plus a query per document:
        `remembered_roots` walked every document and then asked the store for
        each one's root by id. One query, two columns.

        Documents ingested before schema 2 have no recorded root and are absent
        from the mapping -- which reads as "cannot check" rather than
        "unchanged", and the freshness check is told which it is.
        """
        ...

    def forget(self, document_id: DocumentId) -> int:
        """Remove a document and every revision of it. Returns how many went.

        Must leave nothing recoverable -- see the test that vacuums and then
        greps the database file.
        """
        ...

    def corpus_root_of(self, document_id: DocumentId) -> str | None:
        """Where a document was read from, or ``None`` if unrecorded."""
        ...

    def count(self) -> int:
        """How many documents are held, counting each one once."""
        ...
