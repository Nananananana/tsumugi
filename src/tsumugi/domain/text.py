"""Normalization that remembers where every character came from.

Two places need normalized text and neither may lose the original offsets:

- the index tokenizes normalized text, because ｔｓｕｍｕｇｉ and tsumugi are the
  same word to anyone searching (ADR-0007);
- citation resolution compares a model's quotation against the text that was
  sent, and a full-width colon should not make a true citation unsupported
  (ADR-0004).

The alternative -- normalizing the document and anchoring into that -- would
make every anchor point into a string that does not exist on disk. So the
document keeps its original text forever, and normalization carries a map back.

This is `mamori`'s ADR-0004, which reached the same conclusion for replacing
text rather than for finding it.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from .span import Span

__all__ = ["NormalizedText", "normalize"]


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """Normalized text, plus the offset of each character in the original."""

    original: str
    text: str
    #: ``origin[i]`` is the index in ``original`` that produced ``text[i]``.
    #: One entry per normalized character, so a form that expanded (``㍿`` into
    #: ``株式会社``) points all four at the same source character.
    origin: tuple[int, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.origin) != len(self.text):
            raise ValueError(
                f"offset map has {len(self.origin)} entries for {len(self.text)} characters"
            )

    def to_original(self, span: Span) -> Span:
        """The span in ``original`` that produced ``span`` in ``text``.

        An empty span maps to an empty span at the same place. A non-empty one
        maps to the range covering every source character involved, which may
        be wider than the normalized span -- normalization that collapsed four
        characters into one cannot be undone into a narrower answer, and
        widening is the direction that keeps the evidence honest.
        """
        if span.end > len(self.text):
            raise ValueError(f"span {span} runs past normalized text of {len(self.text)}")
        if span.is_empty:
            at = self.origin[span.start] if span.start < len(self.origin) else len(self.original)
            return Span(at, at)

        first = self.origin[span.start]
        # A source character cannot be half-covered: if it produced several
        # normalized characters and the span reaches any of them, the whole
        # source character is in.
        last = self.origin[span.end - 1]
        return Span(first, last + 1)


def normalize(text: str, form: Literal["NFC", "NFD", "NFKC", "NFKD"] = "NFKC") -> NormalizedText:
    """Normalize ``text``, keeping a map back to the original offsets.

    Normalization is applied per character rather than to the whole string.
    That is what makes the map possible, and it costs one real thing: a
    combining sequence split across two source characters (``か`` followed by
    ``゛``) does not compose into ``が``. Whole-string NFKC would compose it and
    would then have no way to say which source character the result came from.

    The trade is deliberate. An index that misses one composed form is a
    recall problem, findable and fixable. An anchor that cannot be mapped back
    to the document is an evidence problem, and this library is the evidence.
    """
    pieces: list[str] = []
    origin: list[int] = []
    for index, character in enumerate(text):
        normalized = unicodedata.normalize(form, character)
        pieces.append(normalized)
        origin.extend([index] * len(normalized))
    return NormalizedText(original=text, text="".join(pieces), origin=tuple(origin))
