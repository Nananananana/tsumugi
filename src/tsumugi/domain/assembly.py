"""Fitting candidates to a budget, and accounting for everything that did not.

This is where [ADR 0005](../../docs/adr/0005-selection-is-a-report.md) becomes
code. The rule the whole module exists to enforce:

    every candidate that comes in leaves as either an item or an omission

Not "most". Not "the interesting ones". A candidate that vanished between the
two lists is a silent truncation, and a silent truncation reads to the reader
as "everything was considered".

The function is deliberately dull. It ranks, it fills, it records. There is no
model in it and no wall-clock, so the same candidates in the same order produce
the same package every time (ADR-0003).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

from .anchor import Anchor
from .budget import Budget
from .omission import Omission, OmissionRule
from .redundancy import DEFAULT_THRESHOLD, mark_duplicates
from .selection import ContextItem, ItemProvenance, SelectionTrace
from .span import Span

__all__ = ["CORPUS_WIDE", "REDUNDANT_SIGNAL", "Candidate", "Fitted", "fit_to_budget"]

#: Carried on an included item that duplicates one already chosen. A duplicate
#: that fits is still sent -- redundancy lowers priority and never vetoes
#: (ADR-0008) -- but the reader is told, because two copies of one idea read as
#: two sources.
REDUNDANT_SIGNAL: Final = "redundant_with"

#: Stands in for "the corpus itself" on an omission that points at no document.
#: Only ``truncated_by_cap`` uses it: a cap is a statement about what was never
#: looked at, so there is nothing to anchor.
CORPUS_WIDE: Final = "(corpus)"
_NOWHERE: Final = Span(0, 0)


@dataclass(frozen=True, slots=True)
class Candidate:
    """Something that could be sent, before anything has decided whether it is."""

    text: str
    anchor: Anchor
    score: float
    source_path: str = ""
    section: str = ""
    signals: tuple[str, ...] = ()
    provenance: ItemProvenance = field(default_factory=ItemProvenance)
    #: Set when the candidate is already known to be unusable -- a stale
    #: anchor, a filter hit. Carried rather than dropped at the call site, so
    #: that the reason reaches ``omissions``.
    disqualified: tuple[OmissionRule, str] | None = None


@dataclass(frozen=True, slots=True)
class Fitted:
    """What fitting produced: the items, and the account of everything else."""

    items: tuple[ContextItem, ...]
    omissions: tuple[Omission, ...]
    spent: int

    def accounts_for(self, candidates: int) -> bool:
        """Whether every candidate is in exactly one of the two lists.

        The ``truncated_by_cap`` entry is excluded: it describes candidates
        that were never retrieved, so it corresponds to no input.
        """
        seen = sum(1 for o in self.omissions if o.rule is not OmissionRule.TRUNCATED_BY_CAP)
        return len(self.items) + seen == candidates


def fit_to_budget(
    candidates: Sequence[Candidate],
    *,
    budget: Budget,
    cost_of: Callable[[str], int],
    minimum_score: float = 0.0,
    truncated_at: int | None = None,
    redundancy_threshold: float = DEFAULT_THRESHOLD,
) -> Fitted:
    """Choose what fits, and say what did not.

    ``truncated_at`` is the cap that bounded the candidate list *before* this
    function saw it -- an index limit, a top-N. It is recorded as a
    ``truncated_by_cap`` omission, because a cap the package does not mention
    is indistinguishable from having considered everything.

    Candidates are taken best-first. A candidate that does not fit does **not**
    stop the fill: a later, smaller one may still fit, and stopping at the first
    overflow would silently prefer long passages over short relevant ones.

    Near-duplicates are **marked, never vetoed** (ADR-0008). A copy of something
    already chosen goes to the back of the queue; if the budget still admits it
    the copy is sent, carrying a ``redundant_with:...`` signal so the reader
    knows two items are one idea. Only when the budget refuses it does it become
    an omission -- and then under ``redundant_candidate``, because "this repeats
    itm_001" is a better answer to *why* than "there was no room".
    """
    ordered = sorted(
        candidates,
        # Deterministic to the last key. An unstable sort here would make every
        # package downstream unreproducible.
        key=lambda c: (-c.score, c.source_path, c.anchor.document_id, c.anchor.span.start),
    )

    # Ranked order decides which member of a duplicate cluster survives.
    # Redundancy says two passages are alike; it has no way to know which is
    # right, and does not guess (ADR-0015).
    duplicates = mark_duplicates([c.text for c in ordered], threshold=redundancy_threshold)

    items: list[ContextItem] = []
    omissions: list[Omission] = []
    item_id_at: dict[int, str] = {}
    spent = 0
    rank = 0

    def place(position: int, candidate: Candidate, cost: int, extra: tuple[str, ...] = ()) -> None:
        nonlocal spent, rank
        rank += 1
        spent += cost
        item_id = f"itm_{rank:03d}"
        item_id_at[position] = item_id
        items.append(
            ContextItem(
                item_id=item_id,
                text=candidate.text,
                anchor=candidate.anchor,
                source_path=candidate.source_path,
                section=candidate.section,
                provenance=candidate.provenance,
                selection=SelectionTrace(
                    rank=rank, score=candidate.score, signals=(*candidate.signals, *extra)
                ),
                cost=cost,
            )
        )

    def consider(position: int, candidate: Candidate, extra: tuple[str, ...] = ()) -> None:
        cost = cost_of(candidate.text)

        if candidate.disqualified is not None:
            rule, reason = candidate.disqualified
            omissions.append(_omission(candidate, rule, reason, cost))
            return

        if candidate.score < minimum_score:
            omissions.append(
                _omission(
                    candidate,
                    OmissionRule.BELOW_THRESHOLD,
                    f"scored {candidate.score:.3f}, below the floor of {minimum_score:.3f}",
                    cost,
                )
            )
            return

        if spent + cost > budget.limit:
            duplicate = duplicates.get(position)
            if duplicate is not None:
                head, found = duplicate
                omissions.append(
                    _omission(
                        candidate,
                        OmissionRule.REDUNDANT_CANDIDATE,
                        f"{found.describe()} with {item_id_at.get(head, 'an earlier candidate')}, "
                        f"and the remaining {budget.remaining(spent)} "
                        f"{budget.unit.value} would not hold it",
                        cost,
                    )
                )
                return
            omissions.append(
                _omission(
                    candidate,
                    OmissionRule.BUDGET_EXHAUSTED,
                    f"ranked {len(items) + len(omissions) + 1}; {cost} "
                    f"{budget.unit.value} would exceed the limit of {budget.limit} "
                    f"with {budget.remaining(spent)} left",
                    cost,
                )
            )
            return

        place(position, candidate, cost, extra)

    # Originals first, copies afterwards: a duplicate loses priority, which is
    # the whole of "lowers priority, does not veto".
    for position, candidate in enumerate(ordered):
        if position not in duplicates:
            consider(position, candidate)

    for position in sorted(duplicates):
        head, found = duplicates[position]
        marker = f"{REDUNDANT_SIGNAL}:{item_id_at.get(head, 'a candidate that was not sent')}"
        consider(position, ordered[position], extra=(marker, found.describe()))

    if truncated_at is not None and truncated_at > 0:
        # No anchor, because there is nothing to point at -- that is the whole
        # nature of this omission. It uses the sentinel id so that a reader can
        # tell it apart from a candidate that was seen and rejected.
        omissions.append(
            Omission(
                rule=OmissionRule.TRUNCATED_BY_CAP,
                reason=(
                    f"retrieval returned its cap of {truncated_at} candidates, so this "
                    f"package was assembled from a bounded view of the corpus rather "
                    f"than all of it"
                ),
                document_id=CORPUS_WIDE,
                span=_NOWHERE,
                source_path="",
            )
        )

    return Fitted(items=tuple(items), omissions=tuple(omissions), spent=spent)


def _omission(candidate: Candidate, rule: OmissionRule, reason: str, cost: int) -> Omission:
    return Omission(
        rule=rule,
        reason=reason,
        document_id=candidate.anchor.document_id,
        span=candidate.anchor.span,
        source_path=candidate.source_path,
        score=candidate.score,
        cost=cost,
    )
