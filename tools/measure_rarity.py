"""Would term rarity break the ties that coverage cannot? Measured first.

    python tools/measure_rarity.py

`proposals/0003` item 5 says bm25 knows `warranty` is the rare word and `the
coverage period of` is not, and the confirmation stage does not. The case for
adding it is a specific one: when a near-miss and the answer cover **the same
amount** of the question, no threshold can separate them — measured earlier,
`answer 0.42 / rival 0.42`, `0.56 / 0.56`. Rarity is the only signal to hand
that could, because the words they share are the common ones.

This asks whether it actually would, before anything is built. Same shape as
`measure_embeddings.py`, which retired item 1 by measuring the clause its title
rested on.

Nothing in the library calls this, nothing in CI runs it, and no model or
network is involved: inverse document frequency is computed from the case's own
documents, which is the whole attraction — it needs no word list, and
ADR-0007, ADR-0018 and ADR-0019 each refused one.
"""

import math
import sys
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, "src")
from tsumugi.application.build_context import build_context
from tsumugi.application.search import _content_terms, _counted, _longest_present
from tsumugi.evaluation.dataset import load_cases
from tsumugi.evaluation.runner import cost_model_for, prepared_case


def present(term: str, folded: str) -> int:
    """Characters of ``term`` found in ``folded``, by the confirmation rule."""
    at, piece = _longest_present(term, folded)
    return 0 if at == -1 else _counted(term, piece)


def coverage(text: str, terms: Sequence[str], weights: dict[str, float] | None = None) -> float:
    """Share of the question the document carries, optionally rarity-weighted."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    weight = weights or {}
    total = sum(len(t) * weight.get(t, 1.0) for t in terms)
    if not total:
        return 0.0
    found = sum(present(t, folded) * weight.get(t, 1.0) for t in terms)
    return float(found / total)


def rarity(terms: Sequence[str], documents: Mapping[str, str]) -> dict[str, float]:
    """Inverse document frequency over the documents of one case.

    No corpus statistics, no list, no language resource: a term that appears in
    every document of a case tells you nothing about which document is meant,
    and that is computable from the case alone.
    """
    n = len(documents)
    weights = {}
    for term in terms:
        appearing = sum(
            1
            for text in documents.values()
            if present(term, unicodedata.normalize("NFKC", text).casefold())
        )
        weights[term] = math.log((n + 1) / (appearing + 1)) + 1.0
    return weights


ties = separated = reversed_wrongly = 0
nonties: list[tuple[str, float, float]] = []
verdicts: Counter[str] = Counter()

for case in load_cases(Path("tests/cases")):
    if not case.must_include:
        continue
    with prepared_case(case) as (store, index, _root):
        package = build_context(
            case.question,
            store=store,
            index=index,
            cost_model=cost_model_for(case.budget.unit),
            budget=case.budget,
        )
    fact = case.facts[case.must_include[0]]
    if any(fact.text in item.text for item in package.items):
        continue  # the lexical stage already has it

    answer = case.fact_document[case.must_include[0]]
    terms = _content_terms(case.question)
    if not terms:
        continue

    plain = {name: coverage(text, terms) for name, text in case.documents.items()}
    best_rival = max((v for k, v in plain.items() if k != answer), default=0.0)
    weights = rarity(terms, case.documents)
    weighted = {name: coverage(text, terms, weights) for name, text in case.documents.items()}
    w_rival = max((v for k, v in weighted.items() if k != answer), default=0.0)

    if plain[answer] != best_rival:
        # Not a tie. Rarity has room to work here in principle: the two
        # documents matched different terms, so weighting them differently can
        # move the gap. Reported separately -- a signal that only helps where a
        # threshold already could is not the argument item 5 makes.
        before = plain[answer] - best_rival
        after = weighted[answer] - w_rival
        nonties.append((case.case_id, before, after))
        continue

    ties += 1
    rival = w_rival

    if weighted[answer] > rival:
        separated += 1
        verdicts["separated"] += 1
    elif weighted[answer] < rival:
        reversed_wrongly += 1
        verdicts["rival now wins"] += 1
    else:
        verdicts["still tied"] += 1

    print(
        f"{case.case_id:38} plain {plain[answer]:.2f}={best_rival:.2f}  "
        f"weighted answer {weighted[answer]:.2f} rival {rival:.2f}  "
        f"{'SEPARATED' if weighted[answer] > rival else 'no'}"
    )

print(f"\n{ties} cases where the answer and a rival cover exactly the same amount")
for verdict, count in verdicts.most_common():
    print(f"  {verdict:16} {count}")
improved = [c for c, b, a in nonties if a > b]
worsened = [c for c, b, a in nonties if a < b]
print(f"{chr(10)}{len(nonties)} cases where they cover different amounts")
print(f"  answer margin improved  {len(improved)}")
print(f"  answer margin worsened  {len(worsened)}")
for case_id, b, a in nonties:
    print(f"    {case_id:38} {b:+.2f} -> {a:+.2f}")

print(
    "\nRarity is worth adding only if it separates ties without reversing any; "
    "a rival that wins on rarity is a trap bought with a fix."
)
