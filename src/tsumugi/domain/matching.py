"""Locating a quotation in the text that was sent.

The model returns the text it relied on; tsumugi finds it (ADR-0004). Models
cannot count characters, so asking for offsets produces coordinates that are
plausible, self-consistent, in the right document, and wrong by a few
characters -- which resolves to real text saying something slightly different.
A verifier that trusted that would mark a claim supported and point at the
wrong sentence, which is worse than no verification because it looks like
proof.

So resolution happens here, deterministically, with one stated tolerance:

    NFKC, case-folded, and runs of whitespace collapsed to one space.

Nothing else. No fuzzy matching, no edit distance, no "close enough". The
tolerance covers the ways the *same* text can be written differently -- a
full-width colon, a line wrapped in a different place -- and stops there,
because every step beyond it trades a false negative for a false positive, and
only one of those two is safe here.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from .span import Span

__all__ = ["SearchForm", "find_all", "search_form"]


@dataclass(frozen=True, slots=True)
class SearchForm:
    """Text reduced to its comparable form, with a map back to the original."""

    original: str
    text: str
    #: ``origin[i]`` is the index in ``original`` that produced ``text[i]``.
    origin: tuple[int, ...] = field(repr=False)

    def to_original(self, span: Span) -> Span:
        """The span of ``original`` that produced ``span`` of ``text``."""
        if span.is_empty or not self.origin:
            return Span(0, 0)
        first = self.origin[span.start]
        last = self.origin[min(span.end, len(self.origin)) - 1]
        return Span(first, last + 1)


def search_form(text: str) -> SearchForm:
    """Reduce ``text`` for comparison, keeping the offsets.

    Applied identically to the quotation and to the text that was sent, so the
    two are compared on equal terms. Leading and trailing whitespace is dropped
    -- a model that quotes with a trailing newline has not made a mistake worth
    reporting.
    """
    pieces: list[str] = []
    origin: list[int] = []
    in_space = True  # True at the start, so leading whitespace is dropped.

    for index, character in enumerate(text):
        if character.isspace():
            if not in_space:
                pieces.append(" ")
                origin.append(index)
                in_space = True
            continue
        in_space = False
        # Per character, so one source position can be recovered for each
        # output position even where normalization expands (ﬁ -> fi).
        folded = unicodedata.normalize("NFKC", character).casefold()
        pieces.append(folded)
        origin.extend([index] * len(folded))

    while pieces and pieces[-1] == " ":
        pieces.pop()
        origin.pop()

    return SearchForm(original=text, text="".join(pieces), origin=tuple(origin))


def find_all(quotation: str, haystack: str, *, limit: int = 32) -> list[Span]:
    """Every place ``quotation`` occurs in ``haystack``, as spans of ``haystack``.

    Returns an empty list when it does not occur. That is a real answer and the
    caller reports it as ``unsupported``: a quotation that is not there is not
    "nearly there".

    More than one match is not an error. A short quotation genuinely occurs in
    several places, and reporting all of them is more honest than picking one
    and implying precision that is not there.
    """
    needle = search_form(quotation)
    if not needle.text:
        return []

    hay = search_form(haystack)
    found: list[Span] = []
    at = hay.text.find(needle.text)
    while at != -1 and len(found) < limit:
        found.append(hay.to_original(Span(at, at + len(needle.text))))
        at = hay.text.find(needle.text, at + 1)
    return found
