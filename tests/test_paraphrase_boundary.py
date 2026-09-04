"""Where confirmation stops working, pinned so it cannot move unnoticed.

The evaluation corpus cannot see any of this. Every question in it was written
by somebody reading the document it answers, so the corpus measures
near-verbatim questions and reports 80% for Chinese while the natural way to ask
returns the wrong passage.

These tests are **not** a wish list. Two of them assert a defect — that a
single-term Chinese question lands on the heading rather than the answer —
because the alternative is worse: a known wrong behaviour with no test drifts
between releases without anyone noticing which release changed it, and the
measurement that found it lives in a tool nobody runs. When the fix lands
(roadmap item 4), these two fail loudly and get inverted, which is the point.

`tools/measure_paraphrase.py` is the same probe with more rows and no
assertions.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

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

Ask = Callable[[str, str], ContextPackage]

ENGLISH = (
    "# Community garden\n\n"
    "The plot size in the garden allocation is 10 square metres.\n"
    "The rota is handed over at each changeover.\n"
)
ENGLISH_ANSWER = "10 square metres"

CHINESE = "# 社区菜园分配\n\n菜园分配的菜园的大小是10平方米。\n负责人轮换，交接时需要登记。\n"
CHINESE_ANSWER = "10平方米"


@pytest.fixture
def ask(tmp_path: Path) -> Callable[[str, str], ContextPackage]:
    """Ingest one document and answer one question against it."""

    def _ask(document: str, question: str) -> ContextPackage:
        root = tmp_path / "corpus"
        root.mkdir(exist_ok=True)
        (root / "a.md").write_text(document, encoding="utf-8")
        connection = connect(tmp_path / "index.db")
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

    return _ask


def answered(package: ContextPackage, answer: str) -> bool:
    return answer in "".join(item.text for item in package.items)


class TestEnglishSurvivesRephrasing:
    """The behaviour the corpus does measure, held in place."""

    @pytest.mark.parametrize(
        "question",
        [
            "the plot size in the garden allocation",
            "what is the plot size in the garden allocation",
            "how big is the plot",
            "plot size",
        ],
    )
    def test_the_answer_survives(self, ask: Ask, question: str) -> None:
        assert answered(ask(ENGLISH, question), ENGLISH_ANSWER)

    def test_a_synonym_is_a_miss_and_that_is_the_design(self, ask: Ask) -> None:
        """ADR-0007: confirmation is lexical, so `area` for `size` is a miss.

        Asserted rather than assumed, because a change that made this pass
        would mean something started guessing at meaning.
        """
        assert not answered(ask(ENGLISH, "what is the area of each plot"), ENGLISH_ANSWER)


class TestChineseStopsEarlier:
    """Three of these pass today. Two assert a live defect."""

    @pytest.mark.parametrize(
        "question",
        [
            "菜园分配的菜园的大小是多少",
            "菜园分配的菜园的大小",
            "菜园的大小是多少",
        ],
    )
    def test_near_verbatim_works(self, ask: Ask, question: str) -> None:
        """Every Chinese case in the evaluation corpus is one of these."""
        assert answered(ask(CHINESE, question), CHINESE_ANSWER)

    @pytest.mark.parametrize("question", ["菜园大小", "菜园"])
    def test_a_keyword_returns_the_wrong_passage(self, ask: Ask, question: str) -> None:
        """**A defect, pinned.** English's `plot size` works; this does not.

        Chinese has no spaces and no script alternation, so the question is one
        term. `_confirm_by_coverage` places evidence where terms crowd together
        and falls back to the first occurrence when there is nothing to crowd --
        which is the heading, the exact failure that function's docstring says
        was fixed for two terms or more.

        Non-empty and wrong, which is worse than empty. When roadmap item 4
        lands, this test fails and becomes the opposite assertion.
        """
        package = ask(CHINESE, question)
        assert package.items, "expected the wrong passage, not an empty package"
        assert not answered(package, CHINESE_ANSWER), (
            "this now finds the answer -- the single-term location defect is fixed. "
            "Invert this test and close roadmap item 4."
        )
