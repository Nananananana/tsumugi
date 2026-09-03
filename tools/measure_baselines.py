"""How much of the headline does a baseline that ignores the question get?

    python tools/measure_baselines.py

`iriguchi` was asked how much of its 81.0% was just getting the majority class
right, and the answer changed the conclusion: on the cases a router actually
exists for, both apparent wins were ties with the rules, and their whole margin
came from the class a trivial baseline takes for free.

The same question has never been asked here. `docs/measurements.md` reports
87.2% evidence recall and 3.3% trap rate as though the number were the
retrieval's. Some of it is: a corpus of five documents with a budget that fits
two is not free. **But nobody has measured how much.**

Two baselines, both of which would be embarrassing to lose to:

`first_fit`     take documents in corpus order until the budget is spent.
                **The question is never read.** Whatever this scores is what
                the corpus gives away by its shape.
`no_confirm`    the index's candidates, in index order, no confirmation stage.
                What ADR-0007's "over-generate, then confirm" is worth is the
                gap between this and the real number.

Neither is a strawman: `first_fit` is what a retriever does when it has no
retriever, and `no_confirm` is what most of them actually ship.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "src")
from tsumugi.application.build_context import build_context
from tsumugi.domain.ordering import maximal_marginal_relevance
from tsumugi.evaluation.dataset import load_cases
from tsumugi.evaluation.runner import cost_model_for, prepared_case


def _overlapping(texts: list[str]) -> bool:
    """Two selected passages sharing a twelve-character window."""
    for a in range(len(texts)):
        grams = {texts[a][k : k + 12] for k in range(max(0, len(texts[a]) - 12))}
        for b in range(a + 1, len(texts)):
            if any(texts[b][k : k + 12] in grams for k in range(max(0, len(texts[b]) - 12))):
                return True
    return False


def main() -> int:
    cases = [c for c in load_cases(Path("tests/cases")) if c.must_include]
    assert cases, "no cases with a required fact; measuring nothing"

    found = {"tsumugi": 0, "first_fit": 0, "no_confirm": 0, "mmr": 0}
    repeats = {"tsumugi": 0, "mmr": 0}
    items = {"tsumugi": 0, "mmr": 0}
    trapped = {"tsumugi": 0, "first_fit": 0, "no_confirm": 0, "mmr": 0}
    trap_cases = 0

    for case in cases:
        fact = case.facts[case.must_include[0]]
        forbidden = [case.facts[k].text for k in case.must_not_include]
        cost = cost_model_for(case.budget.unit)

        with prepared_case(case) as (store, index, _root):
            package = build_context(
                case.question, store=store, index=index, cost_model=cost, budget=case.budget
            )
            real = [item.text for item in package.items]
            diverse = build_context(
                case.question,
                store=store,
                index=index,
                cost_model=cost,
                budget=case.budget,
                ordering=maximal_marginal_relevance,
            )
            mmr = [item.text for item in diverse.items]

            # Everything the index proposed, before confirmation had a say.
            hits = index.search(case.question, limit=50)
            documents = [
                store.get(h.document_id, h.version) or store.get(h.document_id) for h in hits
            ]
            candidates = [d.content for d in documents if d is not None]

        # The budget, spent the dumbest way each baseline can spend it.
        limit = case.budget.limit
        first_fit = _under_budget(list(case.documents.values()), limit)
        no_confirm = _under_budget(candidates, limit)

        for name, texts in (("tsumugi", real), ("mmr", mmr)):
            repeats[name] += _overlapping(texts)
            items[name] += len(texts)

        for name, texts in (
            ("tsumugi", real),
            ("mmr", mmr),
            ("first_fit", first_fit),
            ("no_confirm", no_confirm),
        ):
            found[name] += any(fact.text in t for t in texts)
            if forbidden:
                trapped[name] += any(bad in t for t in texts for bad in forbidden)
        trap_cases += bool(forbidden)

    total = len(cases)
    print(f"{total} cases with a required fact, {trap_cases} of them with a forbidden one")
    print()
    print(f"{'':12} {'recall':>8} {'trap':>8}")
    for name in ("first_fit", "no_confirm", "tsumugi", "mmr"):
        trap = f"{trapped[name] / trap_cases * 100:7.1f}%" if trap_cases else "      -"
        print(f"{name:12} {found[name] / total * 100:7.1f}% {trap}")
    print()
    print(f"{'ordering':12} {'items':>8} {'repeat':>8}")
    for name in ("tsumugi", "mmr"):
        print(f"{name:12} {items[name]:8} {repeats[name] / total * 100:7.1f}%")
    print()
    print(
        "The gap between `first_fit` and `tsumugi` is what reading the question is worth\n"
        "on this corpus. The gap between `no_confirm` and `tsumugi` is what confirmation\n"
        "is worth. Neither gap is the headline, and the headline never said so."
    )
    return 0


def _under_budget(texts: list[str], limit: int) -> list[str]:
    """As many whole texts as fit, in the order given.

    Characters, not the case's own unit: the point is a baseline nobody would
    defend, and making it budget-aware in the same units would be doing it a
    favour it has not earned.
    """
    kept: list[str] = []
    spent = 0
    for text in texts:
        if spent + len(text) > limit:
            continue
        kept.append(text)
        spent += len(text)
    return kept


if __name__ == "__main__":
    raise SystemExit(main())
