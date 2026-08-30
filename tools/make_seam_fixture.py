"""Regenerate the seam fixture: one corpus, one question, one real package.

    python tools/make_seam_fixture.py

Writes ``fixtures/seam/context-package.json`` from the corpus and question
beside it, using the real pipeline. Consumers of the ContextPackage contract
vendor the result and test against it without importing tsumugi.

**Determinism.** `created_at` is pinned to ``PINNED_AT``. It is the one field
deliberately excluded from ``package_id`` (ADR-0003), which is what makes
pinning it safe rather than a lie: the fixture carries the id these inputs
really produce, and running this tool twice writes identical bytes.

The two alternatives were considered and rejected. *Stripping* the field would
publish a document tsumugi does not emit, and the point of a seam fixture is
that it is real output. *Excluding it from comparison* pushes the same decision
onto every consumer, and one of them will forget.

Re-run after any change to selection, rendering or the contract, and commit the
diff: a fixture that drifts from the producer is worse than none, because it
looks like agreement.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsumugi.application.build_context import build_context
from tsumugi.application.ingest import ingest_paths
from tsumugi.domain.budget import Budget
from tsumugi.infrastructure.cost.heuristic import CharacterCost
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.database import connect
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "seam"
CORPUS = FIXTURES / "corpus"
QUESTION = FIXTURES / "question.txt"
PACKAGE = FIXTURES / "context-package.json"

#: Invented, and in the past. A timestamp that moved would make every
#: regeneration a diff.
PINNED_AT = "2026-08-30T00:00:00+00:00"

#: Characters rather than tokens: a token estimate carries its measured error,
#: and that error is a property of the estimator's fit rather than of this
#: corpus. Pinning it would give a fixture that fails whenever the estimator is
#: re-measured, for a reason that has nothing to do with the seam.
#:
#: Tight on purpose. A loose budget fits everything and publishes an empty
#: ``omissions``, and omissions are the half of this contract a consumer is
#: most likely to get wrong -- a fixture that never shows one is a fixture that
#: never tests it.
BUDGET = Budget.characters(40)

#: Named so a consumer can assert on it without parsing the corpus.
VERSION = "fixture"


def build() -> str:
    question = QUESTION.read_text(encoding="utf-8").strip()
    with tempfile.TemporaryDirectory() as workspace:
        connection = connect(Path(workspace) / "index.db")
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        ingest_paths(
            sorted(CORPUS.glob("*.md")),
            root=CORPUS,
            store=store,
            index=index,
            parser_for=parser_for,
        )
        package = build_context(
            question,
            store=store,
            index=index,
            cost_model=CharacterCost(),
            budget=BUDGET,
            version=VERSION,
            created_at=PINNED_AT,
        )
        connection.close()
    return package.to_json()


def main() -> int:
    written = build()
    # Byte-identical on a second run, or the fixture is not a fixture.
    if written != build():
        print("the producer is not deterministic; refusing to write", file=sys.stderr)
        return 1

    previous = PACKAGE.read_text(encoding="utf-8") if PACKAGE.exists() else ""
    with PACKAGE.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(written + "\n")

    package_id = json.loads(written)["package_id"]
    print(f"{PACKAGE.relative_to(PACKAGE.parents[2])}  {package_id}")
    print("unchanged" if previous == written + "\n" else "CHANGED -- review the diff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
