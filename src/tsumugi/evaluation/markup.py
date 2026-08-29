"""Facts planted in a document, and where they turned out to be.

A case's corpus files carry their labels inline::

    ## 装備

    {{F:tent-weight}}テントは 2.4kg、二人用{{/F}}。予備は持たない。

The loader strips the markup and computes the spans. Hand-written offsets are
wrong often enough that a corpus annotated that way ends up measuring the
annotator rather than the system, and a contributor adding a case should not
have to count characters. This is `mamori`'s dataset convention, taken for the
same reason (ADR-0013).

Keeping the label *in* the text also means a reviewer reads one file rather
than two, and a fact that moves takes its label with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.span import Span

__all__ = ["MARKUP", "PlantedFact", "strip_markup"]

#: ``{{F:some-id}} ... {{/F}}``. The id is constrained so that a typo is a
#: parse failure rather than a fact nobody notices is missing.
MARKUP = re.compile(r"\{\{F:([a-z0-9][a-z0-9-]*)\}\}(.*?)\{\{/F\}\}", re.DOTALL)

_OPENING = re.compile(r"\{\{F:")
_CLOSING = re.compile(r"\{\{/F\}\}")


@dataclass(frozen=True, slots=True)
class PlantedFact:
    """One labelled passage, located in the stripped text."""

    fact_id: str
    span: Span
    text: str

    def __post_init__(self) -> None:
        if len(self.text) != len(self.span):
            raise ValueError(
                f"{self.fact_id}: text is {len(self.text)} characters and its span "
                f"covers {len(self.span)}"
            )


def strip_markup(marked: str) -> tuple[str, dict[str, PlantedFact]]:
    """Remove the markup and report where each fact landed.

    Raises on unbalanced or duplicated markup rather than doing something
    reasonable with it. A case whose labels are wrong produces a case that
    fails a *correct* implementation, and that failure is expensive precisely
    because the instinct is to go looking in the code.
    """
    plain: list[str] = []
    facts: dict[str, PlantedFact] = {}
    cursor = 0
    length = 0

    for match in MARKUP.finditer(marked):
        before = marked[cursor : match.start()]
        _refuse_stray_markup(before, match.start())
        plain.append(before)
        length += len(before)

        fact_id, text = match.group(1), match.group(2)
        if fact_id in facts:
            raise ValueError(f"fact {fact_id!r} is planted twice; ids identify one passage")
        if not text:
            raise ValueError(f"fact {fact_id!r} is empty")

        facts[fact_id] = PlantedFact(fact_id, Span(length, length + len(text)), text)
        plain.append(text)
        length += len(text)
        cursor = match.end()

    tail = marked[cursor:]
    _refuse_stray_markup(tail, cursor)
    plain.append(tail)

    return "".join(plain), facts


def _refuse_stray_markup(text: str, at: int) -> None:
    for pattern, what in ((_OPENING, "opening"), (_CLOSING, "closing")):
        found = pattern.search(text)
        if found is not None:
            raise ValueError(
                f"unbalanced {what} markup at offset {at + found.start()}: "
                f"{text[found.start() : found.start() + 40]!r}"
            )
