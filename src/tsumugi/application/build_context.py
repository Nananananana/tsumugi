"""Turning a question into a ContextPackage.

The one use case the whole library is for. It is short, and it is short on
purpose: retrieval belongs to the index, fitting belongs to the domain, and
this only carries candidates from one to the other without losing any.

The rule it exists to keep is the one from ADR-0005. Every candidate the search
stage produced reaches the package as an item or as an omission -- including
the ones this layer would find it convenient to drop, and including the cap
that bounded the search in the first place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from ..domain.anchor import ResolutionStatus, resolve
from ..domain.assembly import Candidate, fit_to_budget
from ..domain.budget import Budget, Unit
from ..domain.omission import OmissionRule
from ..domain.package import (
    BudgetReport,
    ContextPackage,
    PackageProvenance,
    corpus_state,
)
from ..domain.selection import ItemProvenance
from ..ports.cost import CostModel
from ..ports.freshness import FreshnessCheck
from ..ports.index import Index
from ..ports.store import DocumentStore
from .search import search

__all__ = ["build_context"]

_INSTRUCTIONS = {
    "role": "Answer the question using only the context provided below.",
    "rules": [
        # ADR-0004: the model quotes, tsumugi resolves the offsets. Asking for
        # positions produces coordinates that are plausible and wrong.
        "Quote the exact text you rely on. Do not report character offsets.",
        "If the context does not answer the question, say so plainly.",
        "Context marked as an interpretation is a reading, not a fact.",
    ],
}


#: Named so the ranker can explain itself inside the package. A score with no
#: account of where it came from is the part of a retrieval system users are
#: right not to trust.
SIGNAL_LEXICAL: Final = "lexical_match"
SIGNAL_SECTION: Final = "heading_match"
SIGNAL_CONFIRMED: Final = "confirmed_in_text"


def build_context(
    query: str,
    *,
    store: DocumentStore,
    index: Index,
    cost_model: CostModel,
    budget: Budget,
    candidate_limit: int = 50,
    minimum_score: float = 0.0,
    context_characters: int = 400,
    version: str = "",
    freshness: FreshnessCheck | None = None,
) -> ContextPackage:
    """Select what bears on ``query``, fit it to ``budget``, and account for the rest."""
    results, truncation = search(
        query,
        store=store,
        index=index,
        limit=candidate_limit,
        candidate_limit=candidate_limit,
        context=context_characters,
    )

    candidates: list[Candidate] = []
    for result in results:
        signals = [SIGNAL_LEXICAL]
        if result.section:
            signals.append(SIGNAL_SECTION)
        if not result.unconfirmed:
            signals.append(SIGNAL_CONFIRMED)

        # The index over-generates on purpose and confirmation is what turns a
        # candidate into a result (ADR-0007). A candidate the index proposed and
        # confirmation could not support has no established relevance, so it
        # does not go into a package -- it is reported.
        #
        # Found by the evaluation corpus: before this, an unconfirmed candidate
        # became an item covering the head of its document, which dragged whole
        # unrelated documents into packages. The lexical-near-miss trap sprang
        # on 29 of 30 cases. `search` still shows these, marked, because
        # searching is exploratory and a package is not.
        disqualified: tuple[OmissionRule, str] | None = None
        if result.unconfirmed:
            disqualified = (
                OmissionRule.BELOW_THRESHOLD,
                "the index proposed this document, and no exact occurrence of the "
                "query was confirmed in it; retrieval over-generates by design and "
                "an unconfirmed candidate has no established relevance",
            )
        # A stale anchor is not dropped either: it is carried with its reason,
        # so the package can say the evidence was true in the version it was
        # indexed from (ADR-0010).
        #
        # Two different checks, and only one of them used to exist. Resolving
        # against the *store* can never report staleness, because the store
        # holds the text it anchored -- that check catches a corrupt index and
        # nothing else. Whether the *file* has moved on needs the disk, which
        # is what `freshness` is for. Without it, a package silently offers a
        # passage from a file rewritten last week as though it were current;
        # the evaluation corpus found that, and it had been true since v0.2.
        document = store.get(result.anchor.document_id)
        if disqualified is None and document is not None:
            status = resolve(result.anchor, document).status
            if status is ResolutionStatus.UNRESOLVABLE:
                disqualified = (
                    OmissionRule.STALE_ANCHOR,
                    "the text is no longer at the offsets recorded for it",
                )
            elif freshness is not None and not freshness.is_current(document):
                disqualified = (
                    OmissionRule.STALE_ANCHOR,
                    "the file has changed since it was indexed; this passage was true "
                    "in the version that was read, and is not offered as current",
                )
            elif status is ResolutionStatus.STALE:
                disqualified = (
                    OmissionRule.STALE_ANCHOR,
                    "the document has changed since it was indexed; this passage was "
                    "true in the version that was read, and is not offered as current",
                )

        candidates.append(
            Candidate(
                text=result.text,
                anchor=result.anchor,
                score=result.score,
                source_path=result.source_path,
                section=result.section,
                signals=tuple(signals),
                provenance=ItemProvenance(),
                disqualified=disqualified,
            )
        )

    fitted = fit_to_budget(
        candidates,
        budget=budget,
        cost_of=cost_model.cost,
        minimum_score=minimum_score,
        truncated_at=truncation.limit if truncation is not None else None,
    )

    # An invariant, not a nicety. If this ever fails, a candidate vanished
    # between search and the package, which is the exact failure ADR-0005 is
    # about -- and it should stop the build rather than ship quietly.
    if not fitted.accounts_for(len(candidates)):
        raise AssertionError(
            f"{len(candidates)} candidates produced {len(fitted.items)} items and "
            f"{len(fitted.omissions)} omissions; something was dropped without a reason"
        )

    error = cost_model.measured_error
    report = BudgetReport(
        budget=budget,
        estimate=fitted.spent,
        estimator=cost_model.name,
        measured_error=(
            {
                "p50": error.p50,
                "p95": error.p95,
                "against": error.against,
                "dataset": error.dataset,
            }
            if error is not None
            else None
        ),
    )

    return ContextPackage(
        query=query,
        items=fitted.items,
        omissions=fitted.omissions,
        budget=report,
        provenance=PackageProvenance(
            tsumugi_version=version,
            corpus_state=corpus_state([d.version for d in store.all_current()]),
            providers=(
                "filesystem",
                freshness.name if freshness is not None else "freshness/unchecked",
            ),
        ),
        instructions=_INSTRUCTIONS,
        created_at=datetime.now(UTC).isoformat(),
    )


def cost_model_for(unit: Unit) -> str:
    """The name of the model a unit needs. Wiring lives in the interfaces layer."""
    return {
        Unit.TOKENS: "heuristic",
        Unit.CHARACTERS: "characters",
        Unit.BYTES: "bytes",
    }[unit]
