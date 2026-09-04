"""Korean loses whole questions at the index, and this measures the fix.

    python tools/measure_hangul.py

**Found by asking why seven empty packages had no omissions at all.** An
omission means a candidate was considered and rejected; none means the index
returned *nothing*, so confirmation never ran. Four of those seven are Korean
and two are Chinese — the two weakest languages, at 83.3% recall each.

The mechanism is not subtle once seen:

    question  등록은 언제까지 가능하나요?      terms: 등록은, 언제까지, ...
    document  학교 행정의 등록 마감일은 ...    terms: 등록, 마감일은, ...
    overlap   NONE

`등록은` is *registration* plus the topic particle 은. Korean glues its
particles to the noun with no space, and
[the tokenizer](../src/tsumugi/infrastructure/index/tokenization.py) passes
Hangul runs through whole on a stated assumption:

> **Hangul is spaced.** Korean writes its word boundaries, so a run is already
> a word.

That is true of *word* boundaries and false of *morphology*. `등록` and `등록은`
are one word in two forms and two terms in the index.

`INFLECTION_TAIL` exists for exactly this and cannot help: it lets a stem count
as a whole term during **confirmation**, and confirmation never runs on a
document the index did not return.

## What is measured

A Hangul run also emits its prefixes, down to `MINIMUM`. `등록은` becomes
`등록은` and `등록`, so a question written with a particle still reaches a
document written without one. Applied to both sides, because the reverse
happens just as often.

The cost is index size, and this project has refused a feature for that before
— bigramming Hangul was dropped because it cost a fifth of the index and
bought nothing measurable. So terms-per-character is reported beside recall,
and **recall is reported per language**: a change that helps Korean and quietly
costs English is not an improvement, and the aggregate would hide it.
"""

from __future__ import annotations

import statistics
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "src")
from tsumugi.application.build_context import build_context
from tsumugi.application.leads import OFFERABLE
from tsumugi.evaluation.dataset import load_cases
from tsumugi.evaluation.runner import cost_model_for, prepared_case
from tsumugi.infrastructure.index import fts
from tsumugi.infrastructure.index.tokenization import (
    BIGRAM_SCRIPTS,
    BigramTokenizer,
    script_runs,
)

#: Shortest prefix worth emitting. A one-character Hangul prefix is a syllable
#: rather than a morpheme and would match almost anything.
MINIMUM = 2


class HangulPrefixTokenizer(BigramTokenizer):
    """`BigramTokenizer`, plus prefixes of every Hangul run."""

    name = "bigram/script-aware+hangul-prefix@1"

    def _terms(self, text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        terms: list[str] = []
        for script, run in script_runs(normalized):
            if script == "hangul":
                terms.append(run)
                # Longest first is irrelevant to FTS and helps a reader of the
                # index dump: the whole word, then what it might reduce to.
                terms.extend(run[:size] for size in range(len(run) - 1, MINIMUM - 1, -1))
                continue
            if script not in BIGRAM_SCRIPTS or len(run) == 1:
                terms.append(run)
                continue
            terms.extend(run[i : i + 2] for i in range(len(run) - 1))
        return terms


class QueryOnlyPrefixTokenizer(BigramTokenizer):
    """Prefixes on the **question** only, so the index does not grow.

    The two directions cost differently and only one of them is expensive:

        query longer than the document  등록은 asked of 등록   -> query side
        document longer than the query  등록 asked of 등록은   -> index side

    Index-side expansion is what inflates terms-per-character; query-side is
    free, because a question is a dozen characters and is tokenized once. If
    the two score the same, the free one is the answer.
    """

    name = "bigram/script-aware+hangul-query-prefix@1"

    def query_terms(self, query: str) -> list[str]:
        terms: list[str] = []
        for term in self._terms(query):
            terms.append(term)
            if _is_hangul(term):
                terms.extend(term[:size] for size in range(len(term) - 1, MINIMUM - 1, -1))
        return terms


def _is_hangul(term: str) -> bool:
    return any(0xAC00 <= ord(character) <= 0xD7AF for character in term)


def _score(
    tokenizer: BigramTokenizer,
) -> tuple[dict[str, list[int]], int, int, float, int, int]:
    """Recall per language, trap counts, and terms per character."""
    # Patched through `vars()` so mypy is not asked to believe the module
    # re-exports a name it only imports. This is a measurement swapping a
    # dependency for one run, not something the library does.
    original = vars(fts)["BigramTokenizer"]
    vars(fts)["BigramTokenizer"] = lambda: tokenizer
    by_language: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    trapped = trap_cases = 0
    density: list[float] = []
    # The number this change is actually about. A package with no items *and*
    # no omissions is one the index returned nothing for: there is no lead to
    # offer and nothing to say beyond "no".
    empty = silent = 0
    try:
        for case in load_cases(Path("tests/cases")):
            if not case.must_include:
                continue
            fact = case.facts[case.must_include[0]].text
            forbidden = [case.facts[k].text for k in case.must_not_include]
            for text in case.documents.values():
                if text:
                    density.append(len(tokenizer.index_terms(text)) / len(text))
            with prepared_case(case) as (store, index, _root):
                package = build_context(
                    case.question,
                    store=store,
                    index=index,
                    cost_model=cost_model_for(case.budget.unit),
                    budget=case.budget,
                )
            if not package.items:
                empty += 1
                silent += not any(
                    o.rule in OFFERABLE and o.score is not None for o in package.omissions
                )
            texts = [item.text for item in package.items]
            by_language[case.language][1] += 1
            by_language[case.language][0] += any(fact in t for t in texts)
            if forbidden:
                trap_cases += 1
                trapped += any(bad in t for t in texts for bad in forbidden)
    finally:
        vars(fts)["BigramTokenizer"] = original
    return by_language, trapped, trap_cases, statistics.mean(density), empty, silent


def main() -> int:
    results = {}
    for label, tokenizer in (
        ("shipped", BigramTokenizer()),
        ("both-sides", HangulPrefixTokenizer()),
        ("query-only", QueryOnlyPrefixTokenizer()),
    ):
        results[label] = _score(tokenizer)

    languages = sorted(results["shipped"][0])
    print(f"{'language':>10} " + " ".join(f"{label:>16}" for label in results))
    for language in languages:
        row = f"{language:>10} "
        for label in results:
            found, total = results[label][0][language]
            row += f"{found / total * 100:15.1f}% "
        print(row)

    print()
    for label in results:
        _by_language, trapped, trap_cases, density, empty, silent = results[label]
        total_found = sum(v[0] for v in results[label][0].values())
        total = sum(v[1] for v in results[label][0].values())
        print(
            f"{label:>16}  recall {total_found / total * 100:5.1f}%  "
            f"trap {trapped / trap_cases * 100:5.1f}%  "
            f"terms/char {density:.3f}"
        )
        print(f"{'':>16}  {empty} empty packages, {silent} of them with nothing at all to offer")

    print(
        "\nPer language, because a change that helps Korean and quietly costs English\n"
        "is not an improvement and the aggregate would hide it. terms/char is the\n"
        "index cost -- bigramming Hangul was refused once for exactly that."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
