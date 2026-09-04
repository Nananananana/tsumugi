"""What to offer when there is nothing to offer.

A package carries only what confirmation supported, and when confirmation
supports nothing the evidence list is empty. That is
[ADR 0022](../../docs/adr/0022-an-unconfirmed-candidate-is-an-omission-not-an-item.md)
working as designed, and it is also the commonest complaint about this
library: a reader who asks a question and is told *no confirmed evidence* has
learned something true and cannot do anything with it. Told that for every
question, they stop asking.

**The information was never missing.** An omission carries a `document_id` and
a `span`, so the passage the index proposed and confirmation could not support
is already identified in the package -- ADR-0022 says in as many words that a
reader who wants it can fetch it deliberately. Nothing offered them a way to.
This is that way.

A **lead** is not an item and never becomes one. It is a passage that ranked
well and was not confirmed, handed over under its own name, with the anchor
that says exactly where it came from. The evidence list stays empty; a caller
who takes a lead for evidence has to do that on purpose.

## Why only the best one, by default

Measured on the 23 labelled cases whose package comes back empty
(`tools/measure_empty_packages.py`):

    offered      answer found     misleading passage offered
    all                 65.2%                          65.2%
    best 3              65.2%                          65.2%
    best 2              65.2%                          56.5%
    best 1              43.5%                          21.7%

Handing over every omission is a coin flip -- as likely to offer the passage
the case forbids as the one it requires, which is the 96.7% trap rate ADR-0022
measured, arriving by a different door. **The ranking is doing real work in the
first position and almost none after it**: the second lead adds 21.7 points of
risk and no recall at all.

So the default is one. It turns 43.5% of dead ends into something a reader can
act on, and is wrong about a fifth of the time -- numbers a caller can see and
weigh, rather than a silence they cannot.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.omission import Omission, OmissionRule
from ..domain.package import ContextPackage
from ..domain.span import Span
from ..ports.store import DocumentStore

__all__ = ["DEFAULT_LIMIT", "Lead", "leads_from"]

#: One. The measurement above: the second lead costs 21.7 points of risk and
#: recovers nothing.
DEFAULT_LIMIT = 1

#: Rules worth offering. A candidate that was merely too big for the budget, or
#: a near-duplicate of something included, is not a lead -- the reader either
#: has it already or can raise the budget. These are the two that mean *we
#: found something and could not stand behind it*.
OFFERABLE = frozenset({OmissionRule.BELOW_THRESHOLD, OmissionRule.STALE_ANCHOR})


@dataclass(frozen=True, slots=True)
class Lead:
    """A passage that ranked well, was not confirmed, and is offered anyway.

    Deliberately not a `ContextItem`. It has no `text_hash` and cannot be
    verified against, because the thing that would make it verifiable -- a
    confirmed relationship to the question -- is exactly what it lacks.
    """

    text: str
    source_path: str
    document_id: str
    span: Span
    score: float
    #: Why it is not evidence, in the words the omission used.
    unconfirmed_because: str

    def describe(self) -> str:
        where = self.source_path or self.document_id
        return f"{where}[{self.span.start}:{self.span.end}] (unconfirmed): {self.text}"


def leads_from(
    package: ContextPackage,
    store: DocumentStore,
    *,
    limit: int = DEFAULT_LIMIT,
    only_when_empty: bool = True,
) -> list[Lead]:
    """Resolve a package's best omissions back into passages.

    ``only_when_empty`` is the default because a package that *has* evidence
    does not need leads, and offering them beside real items is the shape
    ADR-0022 refused -- there, a reader who ignores the label is upgrading an
    unsupported passage into evidence. With an empty evidence list there is
    nothing to be confused with.

    Returns ``[]`` rather than raising when a document has gone: a lead is a
    convenience, and failing an answer because a hint could not be fetched
    would be worse than not having the hint.
    """
    if limit <= 0:
        return []
    if only_when_empty and package.items:
        return []

    ranked = sorted(
        (o for o in package.omissions if o.rule in OFFERABLE and o.score is not None),
        key=_ranking,
    )

    found: list[Lead] = []
    for omission in ranked:
        if len(found) >= limit:
            break
        document = store.get(omission.document_id)
        if document is None:
            continue
        text = omission.span.slice(document.content)
        if not text.strip():
            continue
        found.append(
            Lead(
                text=text,
                # The omission's own path when it has one, and the
                # document's otherwise: a lead whose whole job is to say
                # *where to look* must not come back saying nowhere.
                source_path=omission.source_path or document.source_path,
                document_id=omission.document_id,
                span=omission.span,
                score=omission.score or 0.0,
                unconfirmed_because=omission.reason,
            )
        )
    return found


def _ranking(omission: Omission) -> tuple[float, str, int]:
    """Best score first, and ties broken by something that does not move.

    Two runs of the same query produce the same package (ADR-0003), and leads
    drawn from it have to be the same leads in the same order or the guarantee
    stops at the package boundary.
    """
    return (-(omission.score or 0.0), omission.document_id, omission.span.start)
