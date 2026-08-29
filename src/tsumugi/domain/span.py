"""A half-open range of characters in a document.

Offsets are in Python string indices -- code points, not bytes and not
grapheme clusters. That choice is load-bearing: ``content[span.start:span.end]``
has to be the exact text the anchor recorded, and any other unit would need a
conversion at every comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Span"]


@dataclass(frozen=True, slots=True, order=True)
class Span:
    """``[start, end)`` in a document's ``content``."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError(f"span starts before the document: {self.start}")
        if self.end < self.start:
            raise ValueError(f"span ends before it starts: [{self.start}, {self.end})")

    def __len__(self) -> int:
        return self.end - self.start

    @property
    def is_empty(self) -> bool:
        return self.end == self.start

    def slice(self, text: str) -> str:
        """The text this span covers.

        Raises rather than returning a short string when the span runs past the
        end. Python slicing clamps silently, and a clamped anchor is an anchor
        that resolves to the wrong text without saying so.
        """
        if self.end > len(text):
            raise ValueError(f"span [{self.start}, {self.end}) runs past a text of {len(text)}")
        return text[self.start : self.end]

    def overlaps(self, other: Span) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: Span) -> bool:
        return self.start <= other.start and other.end <= self.end

    def shift(self, by: int) -> Span:
        return Span(self.start + by, self.end + by)
