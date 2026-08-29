"""How text becomes index terms.

The port exists because the built-in answer is a compromise. Character bigrams
find Japanese where SQLite's default tokenizer finds nothing at all (ADR-0007),
and they do it with no dependency -- but they know nothing about morphology,
so ``開発する`` and ``開発`` are unrelated strings to them.

A proper analyser (MeCab, Sudachi) would do better and brings a dictionary. If
the retrieval dataset ever shows the difference costing real recall, it arrives
here as an adapter, never as a core dependency.

A tokenizer is a **candidate generator**. It is allowed to be sloppy in the
recall direction because every candidate is confirmed against the anchored text
before it can enter a package. That is why the built-in one can afford to
over-generate.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

__all__ = ["Tokenizer"]


@runtime_checkable
class Tokenizer(Protocol):
    """Turns text into the terms an index stores and a query searches."""

    @property
    def name(self) -> str:
        """Stable identifier. It goes into the index's metadata, because an
        index built by one tokenizer cannot be searched by another."""
        ...

    def index_terms(self, text: str) -> Sequence[str]:
        """Terms to store for a document."""
        ...

    def query_terms(self, query: str) -> Sequence[str]:
        """Terms to search for.

        Separate from :meth:`index_terms` because the two are not always the
        same operation: a query may want every term to match where a document
        wants each term recorded once.
        """
        ...
