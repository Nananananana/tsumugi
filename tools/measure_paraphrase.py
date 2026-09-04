"""How far can a question drift from the document before the answer is lost?

    python tools/measure_paraphrase.py

Every question in the evaluation corpus was written by somebody looking at the
document it answers, and it shows: the Chinese cases score 80% evidence recall
while being, almost without exception, contiguous substrings of the text they
ask about. **The corpus cannot fail the way a user will**, so the number it
reports for Chinese is not a number about Chinese.

This asks the same fact several ways, from verbatim to how a person actually
speaks, against one small document per language. It is not scored and there is
no floor: it is a *shape*, printed so that a change to confirmation shows where
it moved the boundary.

English and Japanese fail only on a true synonym, which is ADR-0007 working as
intended -- a lexical library confirms by the words that are there. Chinese
fails two rows earlier, and **not by returning nothing**:

    菜园大小   just the keywords          items=1, and the answer is not in it
    菜园       the document's own word    items=1, and the answer is not in it

A package, confidently, of the wrong passage. `_content_terms` splits a
question into runs of one script, which gives English one term per word and
Japanese a break at every kana boundary; Chinese has neither spaces nor script
alternation, so **the whole question is always one term**. `_confirm_by_coverage`
locates evidence where the terms crowd together, and its own docstring records
why: taking each term's first occurrence pointed the item at the heading, because
a heading is a document's first mention of what it is about. With one term there
is nothing to crowd, the fallback is the first occurrence, and the item is the
heading again. The fix that landed for two terms never reached the language that
only ever has one.

That is a corpus problem before it is a code problem -- 30 Chinese cases score
80% and none of them can see this. Fixing it means either segmenting Han runs
(ADR-0007 refused a segmenter, and `tools/measure_segmenters.py` measured jieba
at 2 of 23) or locating single-term evidence some other way (a density heuristic
was tried and reverted: +0.5 recall, +0.7 trap). Neither is decidable on a corpus
that cannot fail. Roadmap: "the corpus cannot see paraphrase".
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "src")
from tsumugi.application.build_context import build_context
from tsumugi.application.ingest import ingest_paths
from tsumugi.domain.budget import Budget, Unit
from tsumugi.domain.package import ContextPackage
from tsumugi.evaluation.runner import cost_model_for
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.database import connect
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore


@dataclass(frozen=True)
class Probe:
    """One document, one fact in it, and the same question asked five ways."""

    language: str
    document: str
    #: The string that must appear in a package for the question to be answered.
    answer: str
    #: Ordered from verbatim to furthest, so the row where it stops is the edge.
    questions: tuple[tuple[str, str], ...]


PROBES = (
    Probe(
        language="en",
        document=(
            "# Community garden" + chr(10) * 2 + "The plot size in the garden allocation "
            "is 10 square metres."
            + chr(10)
            + "The rota is handed over at each changeover."
            + chr(10)
        ),
        answer="10 square metres",
        questions=(
            ("the plot size in the garden allocation", "verbatim"),
            ("what is the plot size in the garden allocation", "verbatim, with a question stem"),
            ("how big is the plot", "how a person actually asks"),
            ("plot size", "just the keywords"),
            ("what is the area of each plot", "a synonym for the keyword"),
        ),
    ),
    Probe(
        language="zh",
        document=(
            "# 社区菜园分配"
            + chr(10) * 2
            + "菜园分配的菜园的大小是10平方米。"
            + chr(10)
            + "负责人轮换，交接时需要登记。"
            + chr(10)
        ),
        answer="10平方米",
        questions=(
            ("菜园分配的菜园的大小是多少", "verbatim -- how the corpus writes them"),
            ("菜园分配的菜园的大小", "verbatim, minus the tail"),
            ("菜园的大小是多少", "the same words, shorter"),
            ("菜园大小", "just the keywords -- English's 'plot size' works"),
            ("菜园", "one keyword, the document's own word"),
            ("菜园有多大", "how a person actually asks"),
            ("每个菜园的面积是多少", "a synonym for the keyword"),
        ),
    ),
    Probe(
        language="ja",
        document=(
            "# 会議室の予約"
            + chr(10) * 2
            + "会議室の予約は総務課で受け付けています。"
            + chr(10)
            + "集合場所は本社三階です。"
            + chr(10)
        ),
        answer="本社三階",
        questions=(
            ("集合場所は本社", "verbatim"),
            ("集合場所はどこですか", "verbatim, with a question stem"),
            ("集合場所", "just the keyword"),
            ("どこに集まればいいですか", "how a person actually asks"),
            ("待ち合わせ場所を教えて", "a synonym for the keyword"),
        ),
    ),
)


def _ask(document: str, question: str) -> ContextPackage:
    """One question against one throwaway corpus of one file."""
    with tempfile.TemporaryDirectory() as workspace:
        root = Path(workspace) / "corpus"
        root.mkdir()
        (root / "a.md").write_text(document, encoding="utf-8")
        connection = connect(Path(workspace) / "index.db")
        try:
            store, index = SqliteDocumentStore(connection), FtsIndex(connection)
            ingest_paths(
                walk(root).files, root=root, store=store, index=index, parser_for=parser_for
            )
            return build_context(
                question,
                store=store,
                index=index,
                cost_model=cost_model_for(Unit.CHARACTERS),
                budget=Budget.parse("characters:2000"),
            )
        finally:
            connection.close()


def main() -> int:
    for probe in PROBES:
        print(f"{chr(10)}{probe.language}  -- the answer is {probe.answer!r} every time")
        answered = 0
        for question, why in probe.questions:
            package = _ask(probe.document, question)
            got = probe.answer in "".join(item.text for item in package.items)
            answered += got
            mark = "yes" if got else "NO "
            print(f"    {mark}  items={len(package.items)}  {question}")
            print(f"         {why}")
        print(f"    {answered}/{len(probe.questions)} answered")

    print(
        chr(10)
        + "A `no` on the last row of each block is expected and is not a defect: this"
        + chr(10)
        + "library confirms by the words that are there, and a synonym is not (ADR-0007)."
        + chr(10)
        + "A `no` further up is the boundary moving, and is worth an explanation."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
