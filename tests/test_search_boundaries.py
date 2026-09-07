"""The edges of the four rules `search` applies to a candidate.

Each of these is a threshold the suite exercised only from well inside. That is
the same shape of gap `test_context_package.py` found in the assembler, and it
matters more here: these rules decide **what a question is taken to mean** and
**what evidence counts**, before anything downstream can disagree.

Two of the four are decisions with a defensible opposite, written down now
rather than merely true:

- a match exactly at the relative floor is evidence, not a near-miss;
- `relative_match_floor` may be 0.0 or 1.0, and `coverage_threshold` may be 1.0
  but not 0.0. That asymmetry is not an oversight — a coverage threshold of
  zero confirms every document in the corpus for every question.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tsumugi.application.ingest import ingest_paths
from tsumugi.application.search import (
    Confirmation,
    SearchResult,
    _apply_relative_floor,
    _trim_punctuation,
    search,
)
from tsumugi.domain.anchor import Anchor
from tsumugi.domain.span import Span
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

from .helpers import build_document

DOCUMENT = build_document("notes/a.md", "テントの重量は2.4kg、二人用。予備は持たない。")


def _result(matched: int, *, unconfirmed: bool = False) -> SearchResult:
    return SearchResult(
        anchor=Anchor.into(DOCUMENT, Span(0, 10)),
        text=DOCUMENT.content[:10],
        source_path=DOCUMENT.source_path,
        score=1.0,
        unconfirmed=unconfirmed,
        matched=matched,
    )


class TestTrimmingPunctuation:
    """A needle is what the question is *about*, and `?` is not.

    By Unicode category rather than a list, so `?`, `？`, `。`, `।` and `؟` are
    all covered without anyone having to think of them -- which is the whole
    argument for the rule, and was untested in every script.
    """

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("テントの重量は？", "テントの重量は"),
            ("。テントの重量。", "テントの重量"),
            ("(tent)", "tent"),
            ("«tent»", "tent"),
            ("؟tent؟", "tent"),
            ("।tent।", "tent"),
            ("+tent=", "tent"),
            ("¥100", "100"),
        ],
    )
    def test_both_ends_are_trimmed_in_any_script(self, text: str, expected: str) -> None:
        assert _trim_punctuation(text) == expected

    def test_nothing_is_removed_from_the_middle(self) -> None:
        """A needle is still a phrase. Trimming inside would turn `2.4kg` into
        two needles and `e-mail` into three, and confirmation would then accept
        a document containing the pieces in any arrangement."""
        assert _trim_punctuation("2.4kg、二人用") == "2.4kg、二人用"
        assert _trim_punctuation("e-mail") == "e-mail"

    def test_a_string_of_nothing_but_punctuation_becomes_empty(self) -> None:
        """`while end > start` is what stops the second loop walking past the
        first. Without it this is an index error, on a question a person can
        type."""
        assert _trim_punctuation("？！。") == ""
        assert _trim_punctuation("") == ""

    def test_one_character_of_content_survives_from_either_side(self) -> None:
        """The tightest case for both loops at once: each has to stop the
        moment the other's boundary is reached."""
        assert _trim_punctuation("。あ。") == "あ"
        assert _trim_punctuation("あ。") == "あ"
        assert _trim_punctuation("。あ") == "あ"


class TestTheRelativeFloor:
    """A match weak *beside the best this query found* is not evidence.

    Relative rather than absolute, because five words is a lot in one corpus
    and nothing in another (ADR-0019). What the floor does is demote, never
    drop: the result comes back marked `unconfirmed`, so the over-generation
    stays visible rather than becoming mysterious.
    """

    def test_a_match_exactly_at_the_floor_is_still_evidence(self) -> None:
        """`>=`, not `>`. The floor is a share of the strongest match, so with
        a floor of 0.8 and a best of 10 the boundary is 8.0 exactly -- a value
        integer `matched` counts hit squarely and often."""
        settings = Confirmation(relative_match_floor=0.8)
        best, at_the_floor = _result(10), _result(8)

        kept = _apply_relative_floor([best, at_the_floor], settings)

        assert [r.unconfirmed for r in kept] == [False, False]

    def test_a_match_below_the_floor_is_demoted(self) -> None:
        """The positive control. Without it the test above passes over a floor
        that never rejects anything."""
        settings = Confirmation(relative_match_floor=0.8)

        kept = _apply_relative_floor([_result(10), _result(7)], settings)

        assert [r.unconfirmed for r in kept] == [False, True]

    def test_a_coverage_match_is_not_demoted_for_having_no_run(self) -> None:
        """`or not result.matched`. Coverage confirms without a phrase, so
        `matched` is zero -- and zero is below every floor. Without this
        clause the floor would demote every coverage match in the corpus,
        which is the population the Korean and Chinese cases live in.
        """
        settings = Confirmation(relative_match_floor=0.8)

        kept = _apply_relative_floor([_result(10), _result(0)], settings)

        assert [r.unconfirmed for r in kept] == [False, False]

    def test_a_result_already_unconfirmed_is_left_alone(self) -> None:
        kept = _apply_relative_floor([_result(10), _result(1, unconfirmed=True)], Confirmation())
        assert [r.unconfirmed for r in kept] == [False, True]

    def test_nothing_confirmed_leaves_every_result_as_it_was(self) -> None:
        """`if not strongest: return results`. With no run anywhere, the floor
        is zero and every comparison against it is meaningless -- so the rule
        does not run rather than running vacuously."""
        only_coverage = [_result(0), _result(0)]
        assert _apply_relative_floor(only_coverage, Confirmation()) == only_coverage
        assert _apply_relative_floor([], Confirmation()) == []


class TestTheSettingsRefuseWhatTheyCannotMean:
    """Each of the three is on a *cliff*, which is why they are settings at
    all. A caller who reaches for one and passes a value outside its range has
    misunderstood what it is a share of, and a silent clamp would hide that
    behind numbers they would then trust.
    """

    @pytest.mark.parametrize("floor", [0.0, 1.0])
    def test_the_relative_floor_may_sit_on_either_end(self, floor: float) -> None:
        """`0.0 <= x <= 1.0`, both inclusive, and both ends mean something.

        Zero turns the rule off, which is how `tools/measure_floor_scope.py`
        measured what it is worth. One demands the strongest evidence found --
        rejected as a *default* because every case in this corpus has exactly
        one answer, so the corpus cannot show its cost. Refusing it outright
        would be a different claim: that nobody's corpus is like that.
        """
        assert Confirmation(relative_match_floor=floor).relative_match_floor == floor

    @pytest.mark.parametrize("floor", [-0.01, 1.01])
    def test_a_floor_outside_its_range_is_refused(self, floor: float) -> None:
        with pytest.raises(ValueError, match="share of the best match"):
            Confirmation(relative_match_floor=floor)

    def test_coverage_may_be_one_and_may_not_be_zero(self) -> None:
        """`0.0 < x <= 1.0`, and the asymmetry with the floor above is the
        point. One means the whole question must be present, which is the
        default. Zero means **every document confirms every question** -- the
        rule deleted while still appearing to be configured.
        """
        assert Confirmation(coverage_threshold=1.0).coverage_threshold == 1.0
        with pytest.raises(ValueError, match="above 0"):
            Confirmation(coverage_threshold=0.0)

    def test_an_inflection_tail_of_zero_is_allowed_and_negative_is_not(self) -> None:
        """Zero is "no suffix may hang off a term", which is what a language
        that does not glue grammar to its nouns wants. A negative count is not
        a stricter version of that; it is a misunderstanding of the unit."""
        assert Confirmation(inflection_tail=0).inflection_tail == 0
        with pytest.raises(ValueError, match="cannot be negative"):
            Confirmation(inflection_tail=-1)


NL = chr(10)

#: Three headings, and the near-miss text is under the last one.
MANUAL = (
    "# はじめに"
    + NL * 2
    + "この記録は装備の覚書である。"
    + NL * 2
    + "# 食料"
    + NL * 2
    + "米と味噌を持つ。"
    + NL * 2
    + "# 設営"
    + NL * 2
    + "テントを畳む手順。ペグは八本。"
    + NL
)


class TestWhichSectionAHitIsReportedIn:
    """`section` is published: it is in every `search` hit over MCP and in
    every anchor a package carries. A reader follows it to a heading.

    The confirmed path names the section the match sits in. The unconfirmed
    path named `_section_name(document, 0)` -- the heading at the head of the
    document, whatever the hit was about. In a single-section note the two are
    the same value, which is every document the suite had.
    """

    def test_an_unconfirmed_hit_names_the_section_it_came_from(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "manual.md").write_text(MANUAL, encoding="utf-8", newline="")
        ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)

        results, _ = search("テントの重量は", store=store, index=index)

        assert results, "the index has to propose something, or this proves nothing"
        by_section = {result.section: result for result in results}
        assert "設営" in by_section, [r.section for r in results]

        found = by_section["設営"]
        assert found.unconfirmed, "the near miss is the case this is about"
        assert "テントを畳む" in found.text

    def test_a_hit_in_the_first_section_still_names_the_first_section(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        """The positive control, and the reason the defect survived: when the
        hit really is at the head, `0` and `region.start` agree."""
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "manual.md").write_text(MANUAL, encoding="utf-8", newline="")
        ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)

        results, _ = search("装備の覚書", store=store, index=index)

        assert results
        assert results[0].section == "はじめに", results[0].section
