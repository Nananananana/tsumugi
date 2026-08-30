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

Needs ollama and an embedding model. Nothing in the library calls this, and
nothing in CI runs it.
"""

import json
import math
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, "src")
from tsumugi.application.build_context import build_context
from tsumugi.evaluation.dataset import load_cases
from tsumugi.evaluation.runner import cost_model_for, prepared_case

MODEL = sys.argv[1] if len(sys.argv) > 1 else "bge-m3"


def embed(texts):
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/embed", data=body, headers={"Content-Type": "application/json"}
    )
    # S310: a literal loopback URL, three lines above. Nothing here takes a
    # host from anywhere.
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310
        return json.loads(r.read())["embeddings"]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


cases = load_cases(Path("tests/cases"))
checked = hit = 0
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
    hit += top == answer_doc
    print(
        f"{case.case_id:38} lexical MISS -> embedding top={top:14} "
        f"{'HIT' if top == answer_doc else 'miss (want ' + answer_doc + ')'}"
    )
print(f"\n{hit}/{checked} of the cases lexical retrieval misses, embeddings rank first")
