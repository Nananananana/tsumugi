"""Finding candidates.

An index answers "which documents might bear on this?" and nothing more. It
returns *candidates*, and a candidate is not a result: the confirmation stage
checks each one against the anchored text before anything reaches a package.

That division is why the built-in index can over-generate on purpose
(ADR-0007). It is also why an index is allowed to be replaced by something
smarter -- an embedding store, a real analyser -- without any of it touching a
guarantee. The retrieval stage proposes; the domain decides.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..domain.document import Document, DocumentId
from ..domain.hashing import ContentHash

__all__ = ["Index", "IndexHit"]


@dataclass(frozen=True, slots=True, order=True)
class IndexHit:
    """One candidate document, with the index's own opinion of it.

    ``score`` is comparable only against other hits from the same index and the
    same query. It is not a probability and is never presented as one.
    """

    score: float
    document_id: DocumentId
    version: ContentHash

    def __post_init__(self) -> None:
        if not self.document_id:
            raise ValueError("a hit with no document_id points at nothing")


@runtime_checkable
class Index(Protocol):
    """A searchable view over the corpus."""

    @property
    def name(self) -> str:
        """Stable identifier, recorded with the index so that an index built
        by one implementation is not searched by another."""
        ...

    def add(self, document: Document) -> None:
        """Index a revision, replacing any earlier one for the same document.

        Unlike the store, the index is derived and holds only what is current:
        searching turns up documents, and a document has one current revision.
        Old revisions stay findable through their anchors, not through search.
        """
        ...

    def remove(self, document_id: DocumentId) -> None: ...

    def search(self, query: str, limit: int = 50) -> Sequence[IndexHit]:
        """Candidates for ``query``, best first.

        Ties are broken deterministically -- by ``document_id`` -- because a
        package has to be reproducible (ADR-0003) and an unstable sort at the
        bottom of the stack makes that unachievable at the top.

        A ``limit`` that truncates is a cap, and every cap that bounds coverage
        has to reach ``omissions[]`` under ``truncated_by_cap`` (ADR-0005). The
        index reports the truncation by returning exactly ``limit`` hits; the
        caller is responsible for saying so.
        """
        ...

    def count(self) -> int: ...
