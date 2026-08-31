"""Would embeddings close the residual? Measured before anything is built.

    python tools/measure_embeddings.py bge-m3
    python tools/measure_embeddings.py nomic-embed-text

For every case the lexical stage misses, embed the question and each document
and ask: does the answer document rank first by cosine? That is the whole
question `proposals/0003` leads with, and it is answerable in fifty lines
without an `Embedder` port, a vector table or a fusion rule.

**Committed because the answer has a shape, not just a number.** Embeddings
recover every English and Japanese paraphrase case and lose to the *near-miss*
in Chinese and Korean, where a document about a different subject with the same
attribute scores as more similar than the answer. A tool that only printed the
total would have hidden the half that matters.

Three numbers, and the second one retired a roadmap item:

    15/23   embeddings rank the answer document first
     0/23   ...would then survive confirmation unchanged
     6/23   have the answer covering more of the question than every rival

`proposals/0003` led with *"an embedding candidate source, fused, **with
confirmation unchanged**"*, and the middle number says that recovers exactly
nothing. The clause written as the safety guarantee was a description of doing
nothing: confirmation is lexical, these cases are lexical misses, so every
document similarity recovers is dropped again on arrival.

**How much of that is tautology, honestly.** Most of it. Retrieval and
confirmation are different rules -- bm25 over bigrams against coverage of
content terms -- so a document *could* fail retrieval and still confirm, and
the measurement was worth running rather than reasoning about. But the result
was always going to be low, and the honest claim is that it pinned a number
that reasoning would have put "near zero" while leaving room to be wrong.

The third number is the one that was not predictable. **6/23 is the ceiling for
any relaxation of the coverage threshold**, because in the other 17 the rival
covers as much of the question as the answer or more -- exactly, in several:

    zh-sports-club-logistics    answer 0.42   best rival 0.42
    zh-warranty-terms           answer 0.56   best rival 0.56
    zh-medical-appointment      answer 0.00   best rival 0.18   <- and embeddings got this one right

Lowering `COVERAGE_THRESHOLD` cannot separate a tie, and in the last row it
would admit the adversary while still excluding the answer. So the design
question is not which threshold: it is whether a package may carry an item that
nothing lexical confirms, and say so.

Needs ollama and an embedding model. Nothing in the library calls this, and
nothing in CI runs it.
"""

import json
import math
import sys
import unicodedata
import urllib.request
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, "src")
from tsumugi.application.build_context import build_context
from tsumugi.application.search import (
    _confirm,
    _confirm_by_coverage,
    _content_terms,
    _counted,
    _longest_present,
    _needles,
)
from tsumugi.evaluation.dataset import load_cases
from tsumugi.evaluation.runner import cost_model_for, prepared_case

MODEL = sys.argv[1] if len(sys.argv) > 1 else "bge-m3"


def embed(texts: list[str]) -> list[list[float]]:
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/embed", data=body, headers={"Content-Type": "application/json"}
    )
    # S310: a literal loopback URL, three lines above. Nothing here takes a
    # host from anywhere.
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310
        vectors: list[list[float]] = json.loads(r.read())["embeddings"]
        return vectors


def coverage(content: str, terms: Sequence[str]) -> float:
    """How much of the question's content the document carries, 0.0 to 1.0.

    The same quantity `_confirm_by_coverage` thresholds at 1.0. Computed here
    for the answer *and* for the adversary, because the question a lower
    threshold has to answer is not "does the answer clear it" but "does the
    answer clear it while the near-miss does not".
    """
    total = sum(len(term) for term in terms)
    if not total:
        return 0.0
    folded = unicodedata.normalize("NFKC", content).casefold()
    matched = 0
    for term in terms:
        at, piece = _longest_present(term, folded)
        if at != -1:
            matched += _counted(term, piece)
    return matched / total


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


cases = load_cases(Path("tests/cases"))
checked = hit = reachable = 0
separable = []
for case in cases:
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
    answer_doc = case.fact_document[case.must_include[0]]
    fact = case.facts[case.must_include[0]]
    found = any(fact.text in item.text for item in package.items)
    if found:
        continue  # lexical already got it
    checked += 1
    names = list(case.documents)
    vectors = embed([case.question] + [case.documents[n] for n in names])
    query, docs = vectors[0], vectors[1:]
    ranked = sorted(zip(names, docs, strict=True), key=lambda p: -cosine(query, p[1]))
    top = ranked[0][0]
    ranked_right = top == answer_doc
    hit += ranked_right

    # The second question, and the one that decides whether any of this is
    # reachable: if an embedding stage hands this document to the pipeline,
    # does confirmation keep it? Unconfirmed results never enter a package --
    # `build_context` drops them with a declared omission -- so a candidate
    # recovered by similarity and then dropped is recovered into the bin.
    spans, _ = _confirm(case.documents[top], _needles(case.question))
    if not spans:
        spans = _confirm_by_coverage(case.documents[top], _content_terms(case.question))
    survives = bool(spans)
    reachable += ranked_right and survives

    terms = _content_terms(case.question)
    cov_answer = coverage(case.documents[answer_doc], terms)
    cov_rival = max(
        (coverage(text, terms) for name, text in case.documents.items() if name != answer_doc),
        default=0.0,
    )
    separable.append(cov_answer > cov_rival)

    print(
        f"{case.case_id:38} lexical MISS -> embedding top={top:14} "
        f"{'HIT ' if ranked_right else 'miss'} "
        f"confirmation={'keeps' if survives else 'DROPS'}  "
        f"coverage answer={cov_answer:.2f} best rival={cov_rival:.2f}"
    )
print(f"\n{hit}/{checked} of the cases lexical retrieval misses, embeddings rank first")
print(f"{reachable}/{checked} of those would also survive confirmation unchanged")
print(
    f"{sum(separable)}/{len(separable)} have the answer covering more of the question "
    "than every rival -- the ceiling for any lower coverage threshold"
)
