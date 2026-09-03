"""Would a real segmenter recover the misses bigrams cannot? Measured first.

    python tools/measure_segmenters.py

ADR-0007 chose character bigrams and named the condition for revisiting it:

> A proper analyzer (MeCab, Sudachi) would do better and is a dependency with a
> dictionary. **If the golden retrieval dataset ever shows this costing real
> recall, it comes back as an optional adapter** -- never as a core dependency.

The dataset now shows something. Evidence recall is 90.7% in English, 88.3% in
Japanese, and **83.3% in Korean and Chinese**, and every miss is a question that
shares no contiguous phrase with its document. So the condition is arguably met
-- but *arguably* is what this file exists to remove.

The question is narrow and answerable: **of the cases the lexical stage misses,
how many would a word-boundary-aware term list confirm?** Not "is segmentation
better in general", which is a literature question, but what it is worth here,
on the corpus whose numbers this project publishes.

`_content_terms` splits a question by script class and drops hiragana, which is
a boundary rule that needs no dictionary and works about as well as that
description suggests. A segmenter knows that 予算の単位は is 予算 / の / 単位 /
は, and that Chinese has boundaries at all.

Needs `janome` (Japanese, pure Python, bundled dictionary) and `jieba`
(Chinese). Nothing in the library imports either; this asks whether they would
earn their place.
"""

from __future__ import annotations

import sys
import unicodedata
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")
from tsumugi.application.build_context import build_context
from tsumugi.application.search import _content_terms, _counted, _longest_present
from tsumugi.evaluation.dataset import load_cases
from tsumugi.evaluation.runner import cost_model_for, prepared_case

COVERAGE_THRESHOLD = 1.0


def coverage(text: str, terms: Sequence[str]) -> float:
    """The quantity `_confirm_by_coverage` thresholds, computed here to compare."""
    total = sum(len(t) for t in terms)
    if not total:
        return 0.0
    folded = unicodedata.normalize("NFKC", text).casefold()
    found = 0
    for term in terms:
        at, piece = _longest_present(term, folded)
        if at != -1:
            found += _counted(term, piece)
    return found / total


@lru_cache(maxsize=1)
def _japanese_tokenizer() -> Any:
    from janome.tokenizer import Tokenizer

    return Tokenizer()


def _janome_terms(question: str) -> list[str]:
    """Content words, by morphology rather than by script boundary."""
    tokenizer = _japanese_tokenizer()
    keep = {"名詞", "動詞", "形容詞", "副詞"}
    return [
        token.surface
        for token in tokenizer.tokenize(question)
        if token.part_of_speech.split(",")[0] in keep and len(token.surface) > 1
    ]


def _jieba_terms(question: str) -> list[str]:
    """Chinese words. `jieba` has no part of speech in the default mode, so
    length is the only filter available -- single characters carry too little."""
    import jieba

    return [w for w in jieba.cut(question) if len(w) > 1]


SEGMENTERS: dict[str, Callable[[str], list[str]]] = {
    "bigram (today)": _content_terms,
    "janome": _janome_terms,
    "jieba": _jieba_terms,
}


def main() -> int:
    missed: list[tuple[str, str, str, dict[str, str]]] = []
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
            continue
        answer = case.fact_document[case.must_include[0]]
        missed.append((case.case_id, case.question, case.documents[answer], dict(case.documents)))

    assert missed, "nothing is missed; this is measuring nothing"
    print(f"{len(missed)} cases the lexical stage misses")
    print()
    print(f"{'terms from':16} {'confirms':>10} {'rival wins':>12}")

    for name, segment in SEGMENTERS.items():
        confirms = rivals = 0
        for _case_id, question, answer_text, documents in missed:
            terms = segment(question)
            if not terms:
                continue
            mine = coverage(answer_text, terms)
            best_rival = max(
                (coverage(t, terms) for t in documents.values() if t != answer_text), default=0.0
            )
            confirms += mine >= COVERAGE_THRESHOLD
            rivals += best_rival >= mine > 0
        print(f"{name:16} {confirms:9}/{len(missed)} {rivals:11}")

    print()
    print(
        "`confirms` is how many of the misses would clear the coverage threshold on the\n"
        "answer document. `rival wins` is how many would ALSO clear it, or clear it\n"
        "higher, somewhere else -- recovered recall bought with a trap, which is the\n"
        "trade `docs/measurements.md` says an embedding source could not make."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
