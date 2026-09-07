"""Can the 0.7 trap points that section indexing cost be recovered?

    python tools/measure_floor_scope.py

Section-level indexing bought **20.5 points of recall** at realistic document
length and cost **0.7 points of trap rate**, 3.3% to 4.0%
([proposals/0003](../docs/proposals/0003-what-running-it-taught.md), item 0).
Where the 0.7 comes from is known: one document used to be one candidate and is
now several, so a near-miss section gets more chances to confirm with less
surrounding text to be relative to.

The open question that item left is this file's whole subject. ADR-0019's floor
demotes a match that is weak beside the best one; **beside which best one?**

    query      the strongest match anywhere in this query's results
    document   the strongest match in the same document

The second asks *is this the right part of this document*, which is the
question sections created and the query-wide floor cannot ask. It is not
obviously better: a weak section of a weak document clears its own document's
low bar easily, so it could confirm more rather than less.

So it is measured, both ways, on the same cases the trade was made on. What
this cannot show is which is better on somebody else's corpus, and the trap
rate is the number this corpus is least trustworthy about -- the same generator
moved it 6.0% to 25.8%.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, "src")
from tsumugi.application import search as search_module
from tsumugi.application.build_context import build_context
from tsumugi.application.search import Confirmation, SearchResult
from tsumugi.evaluation.dataset import load_cases
from tsumugi.evaluation.runner import cost_model_for, prepared_case
from tsumugi.evaluation.scoring import score_case, summarise
from tsumugi.infrastructure.freshness import FilesystemFreshness


#: The variant lives here rather than in the library. Its only measurement says
#: it is five times worse, and a setting like that is one somebody switches on.
def _document_scoped(results: list[SearchResult], settings: Confirmation) -> list[SearchResult]:
    """The floor, compared within each document rather than across the query."""
    strongest: dict[str, int] = {}
    for result in results:
        key = result.anchor.document_id
        strongest[key] = max(strongest.get(key, 0), result.matched)
    floors = {key: best * settings.relative_match_floor for key, best in strongest.items()}
    return [
        result
        if result.unconfirmed
        or not result.matched
        or result.matched >= floors[result.anchor.document_id]
        else replace(result, unconfirmed=True)
        for result in results
    ]


@contextmanager
def _scope(name: str) -> Iterator[None]:
    """Swap the floor rule for one run and put it back, checked afterwards."""
    original = search_module._apply_relative_floor
    if name == "document":
        search_module._apply_relative_floor = _document_scoped
    try:
        yield
    finally:
        search_module._apply_relative_floor = original
        assert search_module._apply_relative_floor is original


def main() -> int:
    cases = [case for case in load_cases(Path("tests/cases")) if case.must_include]
    assert cases, "no cases with a required fact; measuring nothing"
    print(f"{len(cases)} cases, one case is {100 / len(cases):.1f} points\n")
    print(f"{'scope':>10} {'recall':>8} {'precision':>10} {'trap':>7} {'omissions':>10}")

    for scope in ("document", "query"):
        confirmation = Confirmation()
        scores = []
        for case in cases:
            with prepared_case(case) as (store, index, root), _scope(scope):
                package = build_context(
                    case.question,
                    store=store,
                    index=index,
                    cost_model=cost_model_for(case.budget.unit),
                    budget=case.budget,
                    version="eval",
                    freshness=FilesystemFreshness(root),
                    confirmation=confirmation,
                )
            scores.append(score_case(case, package))
        summary = summarise(scores)
        print(
            f"{scope:>10} "
            f"{(summary.evidence_recall or 0.0) * 100:7.1f}% "
            f"{(summary.evidence_precision or 0.0) * 100:9.1f}% "
            f"{(summary.trap_rate or 0.0) * 100:6.1f}% "
            f"{(summary.omission_correctness or 0.0) * 100:9.1f}%"
        )

    print(
        "\n`document` recovers the 0.7 only if the trap rate falls with no recall cost.\n"
        "Anything else is a different trade, and this corpus is least trustworthy\n"
        "about exactly this number: the same generator moved it 6.0% to 25.8%."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
