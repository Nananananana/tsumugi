"""Turning bytes on disk into structure, without touching the text.

The rule every parser obeys, and the reason a hand-written Markdown reader is
good enough to ship: **a parser reports structure over the original string and
never rewrites it.** It returns spans, not text. A parser that misreads a
nested list produces worse *sections*; it cannot produce a wrong *anchor*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain.document import Block, Section

__all__ = ["ParsedDocument", "Parser"]


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """What a parser found. Spans only, indexed into the content it was given."""

    sections: tuple[Section, ...] = ()
    blocks: tuple[Block, ...] = ()
    #: Anything the format states about itself -- a title, front matter, a
    #: language. Values are strings so that no parser can smuggle a live object
    #: into a document.
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class Parser(Protocol):
    """Reads one family of formats."""

    @property
    def name(self) -> str:
        """Stable identifier, recorded in provenance. Not a display name."""
        ...

    @property
    def suffixes(self) -> Sequence[str]:
        """File suffixes this parser claims, lowercase and dotted."""
        ...

    @property
    def media_type(self) -> str:
        """What documents from this parser are labelled as."""
        ...

    def parse(self, content: str) -> ParsedDocument:
        """Read ``content`` and report its structure.

        **Raises on failure.** Returning an empty ``ParsedDocument`` would be
        indistinguishable from a file that genuinely has no structure, and a
        silently unparsed document is one that quietly stops being findable.
        """
        ...
