"""A cross-encoder ordering. The one place a model is allowed near selection.

`tsumugi[research]` puts `fastembed` on the path — ONNX, no torch — and this
turns it into an `Ordering`: candidates that have **already been confirmed**,
put in the order a model thinks answers the question.

**Where it is used is the whole safety argument, and it was measured.** A
cross-encoder is very good at the thing rivals use it for and not good enough
to be a gate here:

| `BAAI/bge-reranker-base` on this corpus | |
|---|---|
| of the 23 cases the lexical stage misses, answer ranked first | **17** |
| of the 120 trap cases, **the forbidden document ranked first** | **10 (8.3%)** |
| of the 120 trap cases, the answer ranked first | 53 (44.2%) |

The middle row is the one that decides. This library's trap rate is **4.2%**,
and a pipeline that let the reranker choose what to send would roughly double
it while ranking the answer first in fewer than half the cases. ADR-0022
refused to carry an item nothing lexical confirms; a better model does not
change that answer, it just makes the refusal cost more — seventeen recovered
paraphrases, declined.

So the reranker never decides *whether* something is sent. Confirmation still
does that, unchanged, and this only decides the order things are offered to the
budget in — where being wrong costs an ordering rather than a false citation.

**It is slow, and that is a real cost.** A model runs per query, so this is for
someone who has decided the ordering matters more than the latency. Everything
else here is deterministic and local; this is neither, and `--ordering rerank`
is the only way to reach it.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final

from ..domain.ordering import by_score
from ..errors import ConfigurationError

if TYPE_CHECKING:  # pragma: no cover - a type, not a dependency
    from ..domain.assembly import Candidate

__all__ = ["DEFAULT_RERANKER", "rerank"]

#: Multilingual on purpose. This project's weakest languages are Chinese and
#: Korean, and an English-only reranker would improve the language that needs
#: it least.
DEFAULT_RERANKER: Final = "BAAI/bge-reranker-base"


@lru_cache(maxsize=2)
def _encoder(model: str) -> Any:
    """The model, loaded once. Downloads on first use.

    Raises `ConfigurationError` rather than falling back to the score ordering:
    an ordering that silently becomes a different ordering would report numbers
    for something nobody chose, which is the failure this repository has spent a
    week removing from its own claims.
    """
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
    except ImportError as error:  # pragma: no cover - depends on what is installed
        raise ConfigurationError(
            "the `rerank` ordering needs `fastembed`. Install `tsumugi[research]`, "
            "or choose `--ordering score`"
        ) from error
    return TextCrossEncoder(model_name=model)


def rerank(
    candidates: Sequence[Candidate], query: str = "", *, model: str = DEFAULT_RERANKER
) -> list[Candidate]:
    """Order confirmed candidates by what a cross-encoder makes of the question.

    Falls back to the score ordering when there is nothing to reorder or no
    question to reorder against -- not silently, because in both cases the
    score ordering *is* the answer rather than a substitute for one.
    """
    if len(candidates) < 2 or not query.strip():
        return by_score(candidates, query)

    ordered = by_score(candidates, query)
    scores = list(_encoder(model).rerank(query, [c.text for c in ordered]))
    # Ties keep the score order, which is deterministic (ADR-0003); the model's
    # own output is not guaranteed to break them the same way twice.
    return [
        candidate
        for _score, _index, candidate in sorted(
            ((-s, i, c) for i, (s, c) in enumerate(zip(scores, ordered, strict=True))),
        )
    ]
