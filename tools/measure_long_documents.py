"""Does retrieval survive documents the size of real ones? Measured.

    python tools/measure_long_documents.py

**The evaluation corpus has a median document of 143 characters.** Measured on
two sibling repositories, a real document is 6,811 — forty-eight times larger.
Every number in `docs/measurements.md` was taken on the small ones.

That matters because this library **does not chunk**. Nothing splits a document
before indexing: the whole file is one FTS5 row, bm25 scores whole documents,
and the passage a package carries is a window widened around wherever the match
landed. Every comparable library — LlamaIndex node parsers, LangChain text
splitters — splits first, and the recent literature (late chunking, contextual
retrieval) is entirely about how.

On 143-character documents the difference cannot appear. A document that *is* a
passage cannot be out-scored by the wrong part of itself.

So this pads each case's documents with prose from unrelated genres in the same
language, leaving the answer and the traps exactly where they were, and asks
what recall does as documents get longer. The padding is checked to contain
neither the required fact nor a forbidden one, because padding that carried
either would be measuring a different question.
"""

from __future__ import annotations

import re
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "src")
from tsumugi.application.build_context import build_context
from tsumugi.application.ingest import ingest_paths
from tsumugi.evaluation.dataset import Case, load_cases
from tsumugi.evaluation.runner import cost_model_for
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.database import connect
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

#: How much unrelated prose to wrap each document in. 0 is the corpus as it
#: ships; the others bracket a real document's 6,811 characters.
PADDINGS = (0, 1000, 4000, 12000)

#: Split on markdown headings -- the unit `Document` already carries as
#: sections, and the obvious candidate for a chunk. Used with `--sections` to
#: simulate section-level indexing without building it: each section becomes
#: its own file, so the index scores sections instead of documents.
#:
#: **A simulation, and it cheats in one way that matters.** Anchors then point
#: into section files rather than the parent, which is exactly what a real
#: implementation may not do (ADR-0010). It answers "would scoring sections
#: recover the recall" and nothing about whether it can be built.
HEADING = re.compile(r"(?m)^(?=#)")


def _filler(cases: list[Case]) -> dict[str, list[str]]:
    """Prose per language, to pad with. Real documents are about many things."""
    by_language: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        for text in case.documents.values():
            by_language[case.language].append(text)
    return by_language


def _pad(text: str, pool: list[str], size: int, forbidden: list[str]) -> str:
    """Wrap ``text`` in unrelated prose, half before and half after.

    Padding that carried the answer or a trap would change the question rather
    than the document length, so anything containing either is skipped.
    """
    if not size:
        return text
    safe = [p for p in pool if p != text and not any(bad and bad in p for bad in forbidden)]
    if not safe:
        return text
    before: list[str] = []
    after: list[str] = []
    index = 0
    while sum(map(len, before)) < size // 2:
        before.append(safe[index % len(safe)])
        index += 1
    while sum(map(len, after)) < size // 2:
        after.append(safe[index % len(safe)])
        index += 1
    join = chr(10) * 2
    return join.join([*before, text, *after])


def _sections(text: str) -> list[str]:
    parts = [part for part in HEADING.split(text) if part.strip()]
    return parts or [text]


def main() -> int:
    by_section = "--sections" in sys.argv
    cases = [c for c in load_cases(Path("tests/cases")) if c.must_include]
    assert cases, "no cases with a required fact; measuring nothing"
    pool = _filler(cases)

    print("scored by:", "sections" if by_section else "whole documents")
    print(f"{'padding':>8} {'median doc':>11} {'recall':>8} {'trap':>8}")
    for size in PADDINGS:
        found = trapped = trap_cases = 0
        lengths: list[int] = []
        for case in cases:
            fact = case.facts[case.must_include[0]]
            forbidden = [case.facts[k].text for k in case.must_not_include]
            padded = {
                name: _pad(text, pool[case.language], size, [fact.text, *forbidden])
                for name, text in case.documents.items()
            }
            lengths.extend(len(t) for t in padded.values())

            # Its own workspace rather than `prepared_case`, which materialises
            # the case's own documents. Changing the runner so a measurement can
            # pad them would put a measurement's needs inside the thing measured.
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace) / "corpus"
                root.mkdir()
                for name, text in padded.items():
                    if not by_section:
                        (root / name).write_text(text, encoding="utf-8")
                        continue
                    for number, section in enumerate(_sections(text)):
                        (root / f"{Path(name).stem}-{number:03d}.md").write_text(
                            section, encoding="utf-8"
                        )
                connection = connect(Path(workspace) / "index.db")
                try:
                    store = SqliteDocumentStore(connection)
                    fts = FtsIndex(connection)
                    ingest_paths(
                        walk(root).files,
                        root=root,
                        store=store,
                        index=fts,
                        parser_for=parser_for,
                    )
                    package = build_context(
                        case.question,
                        store=store,
                        index=fts,
                        cost_model=cost_model_for(case.budget.unit),
                        budget=case.budget,
                    )
                finally:
                    connection.close()
            texts = [item.text for item in package.items]
            found += any(fact.text in t for t in texts)
            if forbidden:
                trap_cases += 1
                trapped += any(bad in t for t in texts for bad in forbidden)

        trap = f"{trapped / trap_cases * 100:6.1f}%" if trap_cases else "     -"
        print(
            f"{size:>8} {statistics.median(lengths):>11.0f} {found / len(cases) * 100:7.1f}% {trap}"
        )

    print()
    print(
        "The budget is unchanged, so a longer document does not get a longer window --\n"
        "what changes is which document bm25 ranks first and where the window lands."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
