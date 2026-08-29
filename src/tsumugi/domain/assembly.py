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
from .selection import ContextItem, ItemProvenance, SelectionTrace
from .span import Span

__all__ = ["CORPUS_WIDE", "Candidate", "Fitted", "fit_to_budget"]

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
) -> Fitted:
    """Choose what fits, and say what did not.

    ``truncated_at`` is the cap that bounded the candidate list *before* this
    function saw it -- an index limit, a top-N. It is recorded as a
    ``truncated_by_cap`` omission, because a cap the package does not mention
    is indistinguishable from having considered everything.

    Candidates are taken best-first. A candidate that does not fit does **not**
    stop the fill: a later, smaller one may still fit, and stopping at the first
    overflow would silently prefer long passages over short relevant ones.
    """
    ordered = sorted(
        candidates,
        # Deterministic to the last key. An unstable sort here would make every
        # package downstream unreproducible.
        key=lambda c: (-c.score, c.source_path, c.anchor.document_id, c.anchor.span.start),
    )

    items: list[ContextItem] = []
    omissions: list[Omission] = []
    spent = 0
    rank = 0

    for candidate in ordered:
        cost = cost_of(candidate.text)

        if candidate.disqualified is not None:
            rule, reason = candidate.disqualified
            omissions.append(_omission(candidate, rule, reason, cost))
            continue

        if candidate.score < minimum_score:
            omissions.append(
                _omission(
                    candidate,
                    OmissionRule.BELOW_THRESHOLD,
                    f"scored {candidate.score:.3f}, below the floor of {minimum_score:.3f}",
                    cost,
                )
            )
            continue

        if spent + cost > budget.limit:
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
            continue

        rank += 1
        spent += cost
        items.append(
            ContextItem(
                item_id=f"itm_{rank:03d}",
                text=candidate.text,
                anchor=candidate.anchor,
                source_path=candidate.source_path,
                section=candidate.section,
                provenance=candidate.provenance,
                selection=SelectionTrace(
                    rank=rank, score=candidate.score, signals=candidate.signals
                ),
                cost=cost,
            )
        )

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
