"""What was considered and not sent, and which rule removed it.

Every retrieval system discards more than it returns, and the interesting
question is almost never about what came back. A user asks something, gets a
confident answer built from three documents, and never learns that a fourth --
the one that contradicted the other three -- ranked ninth and did not fit.

Worse here than in a search engine, because a search engine's user can see the
result list is truncated. A package goes to a model, and the model answers with
the confidence of complete information over a selection whose edges it cannot
see.

So an omission is a first-class value with a rule and a reason, and
``omissions`` is required rather than optional (ADR-0005).

An omission carries an anchor, a score and prose. It does **not** carry the
omitted text: copying what was deliberately not sent into the thing being sent
would defeat the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .document import DocumentId
from .span import Span

__all__ = ["Omission", "OmissionRule"]


class OmissionRule(Enum):
    """Why a candidate did not make it.

    A closed set on purpose. A future filter that discards candidates has to
    add a rule here, which is a visible change to the contract rather than a
    quiet one -- that obligation is the point.
    """

    #: Ranked well enough, would not fit the budget.
    BUDGET_EXHAUSTED = "budget_exhausted"
    #: Scored under the relevance floor.
    BELOW_THRESHOLD = "below_threshold"
    #: Near-duplicate of something included. Marked, never silently deleted
    #: (ADR-0008).
    REDUNDANT_CANDIDATE = "redundant_candidate"
    #: The source document changed since it was indexed (ADR-0010).
    STALE_ANCHOR = "stale_anchor"
    #: Removed by an explicit user filter or ignore rule.
    EXCLUDED_BY_FILTER = "excluded_by_filter"
    #: Cut by a top-N or a sampling limit. **The important one**: any
    #: implementation limit that bounds coverage has to appear here, or a
    #: partial search is indistinguishable from a complete one.
    TRUNCATED_BY_CAP = "truncated_by_cap"

    @classmethod
    def parse(cls, value: str) -> OmissionRule:
        try:
            return cls(value)
        except ValueError:
            known = ", ".join(rule.value for rule in cls)
            raise ValueError(f"unknown omission rule {value!r}; expected one of {known}") from None


@dataclass(frozen=True, slots=True)
class Omission:
    """One candidate that was considered and left out."""

    rule: OmissionRule
    #: In prose, for a person. Naming the rule is not the same as explaining
    #: the decision: "ranked 7th; 2,210 estimated tokens would exceed the
    #: 8,000 limit" is what a reader can act on.
    reason: str
    document_id: DocumentId
    span: Span
    source_path: str = ""
    #: The ranker's score, where there was one. ``None`` for candidates that
    #: never reached ranking -- a file excluded by a filter has no score, and
    #: reporting 0.0 would imply it was judged irrelevant.
    score: float | None = None
    #: What it would have cost, in the package's budget unit, where that was
    #: computed.
    cost: int | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(
                f"an omission under {self.rule.value} with no reason explains nothing; "
                f"naming the rule is not the same as saying why"
            )
        if not self.document_id:
            raise ValueError("an omission with no document_id points at nothing")
        if self.cost is not None and self.cost < 0:
            raise ValueError(f"a negative cost of {self.cost}")

    def describe(self) -> str:
        where = self.source_path or self.document_id
        return f"{where}[{self.span.start}:{self.span.end}] {self.rule.value}: {self.reason}"
