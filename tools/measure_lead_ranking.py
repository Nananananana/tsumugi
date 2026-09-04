"""Does a cross-encoder pick a better lead than bm25 does?

    python tools/measure_lead_ranking.py          # needs tsumugi[research]

**A narrow question, deliberately.** When confirmation supports nothing, a
package is empty and `leads_from` offers the best-ranked unconfirmed passage
([ADR 0026](../docs/adr/0026-a-lead-is-offered-only-when-there-is-nothing-to-confuse-it-with.md)).
Best-ranked means bm25, and that is worth 43.5% useful against 21.7%
misleading over the 23 labelled cases whose package comes back empty. The
ranking is doing the whole job in the first position, so a better ranking is
worth exactly as much as the gap between those two numbers.

A cross-encoder reads the question and the passage together, which is the one
thing bm25 cannot do, and it is the reason the residual exists: these are
paraphrase cases, where the answer is stated in words the question does not
use.

**This is not the reranker-as-gate that was refused.** That was measured over
*all* cases and moved the trap rate from 4.2% to 8.3%, because it let
unconfirmed passages into packages that already had good evidence. Here the
package is empty by construction: there is no evidence to dilute, nothing is
promoted to an item, and the guarantee that every item is confirmed is
untouched. Only the *order* of the leads changes.

Three rankings, same 23 cases, same top-1 rule:

    bm25       what ships today
    cross      the cross-encoder's order
    oracle     the best any reordering could do -- the ceiling

The oracle row is the one that says whether this is worth building at all. If
bm25 is already near it, a better ranker has nothing to win.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")
from tsumugi.application.build_context import build_context
from tsumugi.evaluation.dataset import load_cases
from tsumugi.evaluation.runner import cost_model_for, prepared_case
from tsumugi.infrastructure.reranking import DEFAULT_RERANKER, _encoder


def main() -> int:
    cases = [c for c in load_cases(Path("tests/cases")) if c.must_include]
    assert cases, "no cases with a required fact; measuring nothing"

    encoder = _encoder(DEFAULT_RERANKER)
    tallies: dict[str, list[int]] = {k: [0, 0] for k in ("bm25", "cross", "oracle")}
    empty = 0
    with_traps = 0

    for case in cases:
        fact = case.facts[case.must_include[0]].text
        forbidden = [case.facts[k].text for k in case.must_not_include]

        with prepared_case(case) as (store, index, _root):
            package = build_context(
                case.question,
                store=store,
                index=index,
                cost_model=cost_model_for(case.budget.unit),
                budget=case.budget,
            )
            if package.items:
                continue
            offered = _passages(package, store)

        if not offered:
            continue
        empty += 1
        if forbidden:
            with_traps += 1

        # bm25: the order the package already carries.
        _tally(tallies["bm25"], [offered[0][1]], fact, forbidden)

        # cross: the same passages, read against the question.
        scores = list(encoder.rerank(case.question, [text for _, text in offered]))
        best = max(zip(scores, range(len(offered)), strict=True))[1]
        _tally(tallies["cross"], [offered[best][1]], fact, forbidden)

        # oracle: is the answer anywhere in what could have been offered?
        helpful = [text for _, text in offered if fact in text]
        _tally(tallies["oracle"], helpful[:1] or [offered[0][1]], fact, forbidden)

    print(f"{empty} cases come back empty and have something to offer.\n")
    print(f"{'ranking':>8} {'answer found':>14} {'misleading':>12}")
    for name in ("bm25", "cross", "oracle"):
        found, trapped = tallies[name]
        print(
            f"{name:>8} {found / empty * 100:13.1f}% {trapped / with_traps * 100:11.1f}%"
            if with_traps
            else f"{name:>8} {found / empty * 100:13.1f}%"
        )
    print(
        "\nThe oracle row is the ceiling: the best any reordering of the same passages\n"
        "could reach. If bm25 is already close to it, a better ranker wins nothing and\n"
        "the residual is somewhere else entirely."
    )
    return 0


def _passages(package: Any, store: Any) -> list[tuple[float, str]]:
    """The offerable omissions, resolved to text, best bm25 score first."""
    from tsumugi.application.leads import OFFERABLE

    found: list[tuple[float, str]] = []
    for omission in sorted(
        (o for o in package.omissions if o.rule in OFFERABLE and o.score is not None),
        key=lambda o: (-(o.score or 0.0), o.document_id, o.span.start),
    ):
        document = store.get(omission.document_id)
        if document is not None:
            text = omission.span.slice(document.content)
            if text.strip():
                found.append((omission.score or 0.0, text))
    return found


def _tally(into: list[int], texts: list[str], fact: str, forbidden: list[str]) -> None:
    into[0] += any(fact in text for text in texts)
    into[1] += any(bad in text for text in texts for bad in forbidden)


if __name__ == "__main__":
    raise SystemExit(main())
