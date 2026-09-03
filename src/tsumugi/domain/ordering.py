"""The order candidates are offered to the budget in, and how to choose it.

`fit_to_budget` fills a budget best-first, and until now *best* meant one thing:
descending score. That is a real algorithm with a real weakness, and the
weakness is measurable here — **113 of 240 packages built from the evaluation
corpus contain two items sharing a twelve-character window.** Duplicates are
marked (ADR-0008) and still spend budget, because marking is a report and
ordering is a decision, and the report is not allowed to make the decision.

So the decision becomes a parameter. Both orderings are in the standard
library's reach, both are deterministic, and neither needs a model:

``by_score``
    Descending score, ties broken by path and offset. What this project has
    always done, and what it measures its numbers against.

``maximal_marginal_relevance``
    Carbonell & Goldstein, SIGIR 1998. Pick the best; then repeatedly pick
    whatever maximises ``diversity * score - (1 - diversity) * likeness to
    what is already picked``. Spends a budget on distinct evidence rather than
    on the same sentence three times.

**MMR is not the better one.** It is a different trade, and `docs/measurements.md`
records what each costs on this corpus rather than asserting which wins — the
whole reason the choice is a parameter and not a rewrite. A near-duplicate is
sometimes the second witness that makes a fact checkable, and dropping it to
buy variety is a loss the reader cannot see.

The likeness measure is `redundancy.similarity`, already here, already used for
the marks: character shingles and set containment. Reusing it means MMR
introduces no new notion of *alike*, so a package's marks and its ordering
cannot disagree about which passages repeat each other.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Final

from .redundancy import similarity

if TYPE_CHECKING:  # pragma: no cover - a type, not a dependency
    from .assembly import Candidate

__all__ = ["DEFAULT_DIVERSITY", "ORDERINGS", "Ordering", "by_score", "maximal_marginal_relevance"]

#: An ordering takes what could be sent and says in what order to try it.
Ordering = Callable[[Sequence["Candidate"]], list["Candidate"]]

#: The λ in Carbonell & Goldstein, named for what it does rather than for its
#: letter. 1.0 is pure relevance and reduces exactly to ``by_score``; 0.0 is
#: pure novelty and ignores the question. 0.7 leans towards relevance, which is
#: the right default for evidence: a package is not a summary.
DEFAULT_DIVERSITY: Final = 0.7


def _tiebreak(candidate: Candidate) -> tuple[str, str, int]:
    """The keys that make an order reproducible (ADR-0003).

    Score alone leaves ties, and an unstable sort would make two runs of the
    same query produce two different packages.
    """
    return (candidate.source_path, candidate.anchor.document_id, candidate.anchor.span.start)


def by_score(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Descending score. The ordering every measured number here was taken on."""
    return sorted(candidates, key=lambda c: (-c.score, *_tiebreak(c)))


def maximal_marginal_relevance(
    candidates: Sequence[Candidate], *, diversity: float = DEFAULT_DIVERSITY
) -> list[Candidate]:
    """Relevance traded against novelty, greedily (Carbonell & Goldstein, 1998).

    Scores are rescaled to ``0..1`` against the best candidate before being
    weighed against a similarity that is already ``0..1``. Without that the
    trade would depend on the absolute size of bm25 scores, which vary with
    corpus and query length -- the parameter would mean something different for
    every question, which is the kind of number this project has spent a week
    removing.

    A candidate already disqualified (a stale anchor, a filter hit) keeps its
    place by score and is not diversified against: it is not going to be sent,
    and letting it push a usable passage down the list would be an ordering
    decision made by something that is only ever reported.
    """
    if not candidates:
        return []
    if not 0.0 <= diversity <= 1.0:
        raise ValueError(f"diversity is a share between 0 and 1, not {diversity}")

    ranked = by_score(candidates)
    best = ranked[0].score
    scale = best if best > 0 else 1.0

    remaining = list(ranked)
    chosen: list[Candidate] = [remaining.pop(0)]
    while remaining:

        def marginal(candidate: Candidate, picked: list[Candidate] = chosen) -> float:
            likeness = max(
                (similarity(candidate.text, taken.text).containment for taken in picked),
                default=0.0,
            )
            return diversity * (candidate.score / scale) - (1.0 - diversity) * likeness

        # `max` keeps the first of equals, and `remaining` is already in
        # deterministic order, so ties resolve the same way every run.
        pick = max(remaining, key=marginal)
        remaining.remove(pick)
        chosen.append(pick)
    return chosen


#: The orderings a caller may name, for configuration to look up. Adding one is
#: a line here plus the function; nothing else in the library learns its name.
ORDERINGS: Final[dict[str, Ordering]] = {
    "score": by_score,
    "mmr": maximal_marginal_relevance,
}
