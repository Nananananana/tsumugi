"""When a package comes back empty, was the answer in the omissions?

    python tools/measure_empty_packages.py

**The complaint this answers is that tsumugi says nothing too often.** A
package carries only what confirmation supported ([ADR
0022](../docs/adr/0022-an-unconfirmed-candidate-is-an-omission-not-an-item.md)),
and when confirmation supports nothing the evidence list is empty. A reader
gets "no confirmed evidence", which is true, and which is also useless if it
is what they get for every question they ask.

ADR-0022 refused to *mark* unconfirmed candidates and put them in the evidence
list, for a reason that has a number behind it: exclusion moved the trap rate
from 96.7% to 36.7%, and a mark only works if the reader honours it.

But that argument was measured over **all** cases, where an unconfirmed
passage sits beside confirmed ones and dilutes them. It says nothing about the
case that produces the complaint, where the evidence list is *empty* and the
alternative to an unconfirmed passage is not weaker evidence -- it is silence.
Those are different trades and only one of them has been measured.

So this counts, over the labelled corpus:

- how often a package comes back with no items at all;
- of those, how often an omission's span covers the fact the case requires --
  the recall that is sitting in the package already, unreachable;
- of those, how often an omission covers a fact the case forbids -- what
  offering them would cost.

The third number is the one that decides it. `docs/measurements.md` records
whatever comes out, including the outcome where this is a bad idea.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "src")
from tsumugi.application.build_context import build_context
from tsumugi.evaluation.dataset import load_cases
from tsumugi.evaluation.runner import cost_model_for, prepared_case

#: How many of the best-scoring omissions to offer. 0 means all of them, which
#: is what a naive "just show the near misses" would do.
DEPTHS = (0, 3, 2, 1)


def main() -> int:
    cases = [c for c in load_cases(Path("tests/cases")) if c.must_include]
    assert cases, "no cases with a required fact; measuring nothing"

    empty = 0
    empty_recoverable = 0
    empty_traps = 0
    empty_with_traps = 0
    nonempty_missed = 0
    nonempty_recoverable = 0
    at_depth: dict[int, list[int]] = {d: [0, 0] for d in DEPTHS}

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
            # What a reader would get if the omissions were resolved back into
            # passages -- the thing that is currently unreachable.
            ranked = sorted(
                (o for o in package.omissions if o.score is not None),
                key=lambda o: (-(o.score or 0.0), o.document_id, o.span.start),
            )
            offered_by_depth: dict[int, list[str]] = {}
            for depth in DEPTHS:
                texts: list[str] = []
                for omission in ranked[: depth or len(ranked)]:
                    document = store.get(omission.document_id)
                    if document is not None:
                        texts.append(omission.span.slice(document.content))
                offered_by_depth[depth] = texts
            offered = offered_by_depth[0]

        items = [item.text for item in package.items]
        found = any(fact in text for text in items)
        in_omissions = any(fact in text for text in offered)
        trap_in_omissions = any(bad in text for text in offered for bad in forbidden)

        if not items:
            empty += 1
            empty_recoverable += in_omissions
            if forbidden:
                empty_with_traps += 1
                empty_traps += trap_in_omissions
            for depth, texts in offered_by_depth.items():
                at_depth[depth][0] += any(fact in text for text in texts)
                at_depth[depth][1] += any(bad in text for text in texts for bad in forbidden)
        elif not found:
            nonempty_missed += 1
            nonempty_recoverable += in_omissions

    total = len(cases)
    print(f"{total} cases with a required fact.\n")
    print(f"packages with no items at all: {empty} ({empty / total * 100:.1f}%)")
    if empty:
        print(
            f"  of those, the required fact is inside an omission: "
            f"{empty_recoverable} ({empty_recoverable / empty * 100:.1f}%)"
        )
        if empty_with_traps:
            print(
                f"  of those with a trap, a forbidden fact is inside an omission: "
                f"{empty_traps} ({empty_traps / empty_with_traps * 100:.1f}%)"
            )
    print()
    print(f"{chr(0)}".join([]) or f"{'offered':>10} {'answer found':>14} {'trap offered':>14}")
    for depth in DEPTHS:
        recovered, trapped = at_depth[depth]
        label = "all" if depth == 0 else f"best {depth}"
        print(
            f"{label:>10} {recovered / empty * 100:13.1f}% "
            f"{trapped / empty_with_traps * 100:13.1f}%"
        )

    print(f"\npackages that had items but missed the fact: {nonempty_missed}")
    if nonempty_missed:
        print(
            f"  of those, the fact is inside an omission: {nonempty_recoverable} "
            f"({nonempty_recoverable / nonempty_missed * 100:.1f}%)"
        )
    print(
        "\nThe empty-package rows decide whether offering resolved omissions is worth\n"
        "building. The recoverable share is what a reader would gain; the forbidden\n"
        "share is what it would cost them, and it is the number that matters."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
