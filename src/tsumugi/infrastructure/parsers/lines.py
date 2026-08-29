"""Lines, with the offsets they occupy in the document.

``str.splitlines()`` throws away exactly the information every parser here
needs. Everything is a span into the original string, so this is where the
line/offset bookkeeping happens once rather than in each parser.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ...domain.span import Span

__all__ = ["Line", "iter_lines"]


@dataclass(frozen=True, slots=True)
class Line:
    """One line, without its terminator, and where it sits."""

    number: int
    text: str
    #: Covers the text only. The newline is not part of the line, so that a
    #: block built from a run of lines does not end with a stray separator.
    span: Span


def iter_lines(content: str) -> Iterator[Line]:
    """Every line of ``content``, with offsets into it.

    Handles ``\\n``, ``\\r\\n`` and a lone ``\\r``, because a corpus of personal
    notes has files from every editor anyone ever used.
    """
    number = 1
    start = 0
    index = 0
    length = len(content)

    while index < length:
        character = content[index]
        if character == "\r":
            yield Line(number, content[start:index], Span(start, index))
            index += 2 if content[index + 1 : index + 2] == "\n" else 1
            start = index
            number += 1
        elif character == "\n":
            yield Line(number, content[start:index], Span(start, index))
            index += 1
            start = index
            number += 1
        else:
            index += 1

    if start < length or number == 1:
        yield Line(number, content[start:length], Span(start, length))
