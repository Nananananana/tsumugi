"""Near-duplicates: what the detector sees, what it cannot, and that it marks.

The second half matters as much as the first. A detector whose limits are not
written down gets trusted past them, and this one has a limit that measurement
found rather than intuition: it cannot tell a corrected value from a different
subject, because the difference between those is meaning (ADR-0015).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tsumugi.domain.anchor import Anchor
from tsumugi.domain.assembly import REDUNDANT_SIGNAL, Candidate, fit_to_budget
from tsumugi.domain.budget import Budget
from tsumugi.domain.omission import OmissionRule
from tsumugi.domain.redundancy import (
    DEFAULT_THRESHOLD,
    mark_duplicates,
    shingles,
    similarity,
)
from tsumugi.domain.span import Span

from .helpers import build_document

PASSAGE = (
    "設計方針として、予算の単位は呼び出し側で明示する。トークンは推定であり、"
    "推定器は自らの誤差を申告する。文字数とバイト数は正確に数える。"
)
ELSEWHERE = "球根の植え付け時期は10月下旬から11月上旬。土は乾かし気味に管理する。"


class TestWhatItSees:
    def test_a_verbatim_copy(self) -> None:
        assert similarity(PASSAGE, PASSAGE).score == 1.0

    def test_a_copy_reflowed_to_a_different_width(self) -> None:
        # The commonest way a duplicate stops looking like one.
        reflowed = PASSAGE.replace("。", "。\n    ")
        assert similarity(PASSAGE, reflowed).is_near_duplicate()

    def test_a_copy_embedded_in_something_longer(self) -> None:
        longer = f"前置き。\n\n{PASSAGE}\n\n{ELSEWHERE * 3}"
        assert similarity(PASSAGE, longer).is_near_duplicate()

    def test_a_copy_with_one_clause_edited(self) -> None:
        edited = PASSAGE.replace("トークンは推定であり", "トークン数は概算であり")
        assert similarity(PASSAGE, edited).is_near_duplicate()

    def test_case_and_width_do_not_hide_a_copy(self) -> None:
        assert similarity("Budget Notes", "ｂｕｄｇｅｔ　ｎｏｔｅｓ").is_near_duplicate()


class TestWhatItCannotSee:
    """Measured limits, not guesses. Written down so they are not trusted past."""

    def test_the_same_thing_said_in_different_words(self) -> None:
        rewritten = "予算はトークン・文字・バイトから単位を選ぶ。推定の場合は誤差を示す。"
        assert not similarity(PASSAGE, rewritten).is_near_duplicate()

    def test_a_superseded_version_is_not_detectable_as_a_duplicate(self) -> None:
        # 0.417 containment, against 0.167 for an unrelated statement of the
        # same shape. Not separable by character overlap, because the
        # difference between them is meaning (ADR-0015).
        correction = similarity("テントの重量は2.4kg、二人用", "テントの重量は3.1kg、二人用")
        different = similarity(
            "テントの重量は2.4kg、二人用", "キャンプ用タープの重量は3.1kg、二人用"
        )
        assert not correction.is_near_duplicate()
        assert correction.score < 0.5
        # The two are close enough that no threshold separates them.
        assert abs(correction.score - different.score) < 0.5

    def test_unrelated_text_scores_nothing(self) -> None:
        assert similarity(PASSAGE, ELSEWHERE).score == 0.0


class TestTheThreshold:
    def test_it_sits_in_the_measured_gap(self) -> None:
        # Above every non-copy and below every copy. If this fails, the
        # threshold moved without the measurement moving with it.
        copies = [
            similarity(PASSAGE, PASSAGE).score,
            similarity(PASSAGE, PASSAGE.replace("。", "。\n  ")).score,
            similarity(PASSAGE, f"前置き\n\n{PASSAGE}\n\n{ELSEWHERE}").score,
        ]
        not_copies = [
            similarity("テントの重量は2.4kg", "テントの重量は3.1kg").score,
            similarity(PASSAGE, ELSEWHERE).score,
        ]
        assert min(copies) > DEFAULT_THRESHOLD > max(not_copies)


class TestShingles:
    def test_whitespace_is_collapsed(self) -> None:
        assert shingles("a b  c") == shingles("a b\n\tc")

    def test_short_text_still_yields_something(self) -> None:
        assert shingles("ab")

    def test_empty_text_yields_nothing(self) -> None:
        assert shingles("") == frozenset()
        assert similarity("", PASSAGE).score == 0.0


class TestMarking:
    def test_the_first_of_a_pair_survives(self) -> None:
        marks = mark_duplicates([PASSAGE, PASSAGE])
        assert marks == {1: marks[1]}
        assert marks[1][0] == 0

    def test_a_chain_of_copies_collapses_to_one_survivor(self) -> None:
        # Otherwise a document copied three times produces a chain of pointers
        # and two of them look like independent sources.
        marks = mark_duplicates([PASSAGE, PASSAGE, PASSAGE, PASSAGE])
        assert set(marks) == {1, 2, 3}
        assert all(head == 0 for head, _ in marks.values())

    def test_unrelated_passages_are_not_marked(self) -> None:
        assert mark_duplicates([PASSAGE, ELSEWHERE]) == {}

    @given(texts=st.lists(st.sampled_from([PASSAGE, ELSEWHERE, ""]), max_size=6))
    def test_marking_never_marks_the_first_passage(self, texts: list[str]) -> None:
        # It has nothing earlier to duplicate.
        assert 0 not in mark_duplicates(texts)


class TestItMarksAndNeverRemoves:
    """ADR-0008's promise, as behaviour."""

    @staticmethod
    def _candidates(*texts: str) -> list[Candidate]:
        document = build_document("notes/a.md", "".join(texts))
        made: list[Candidate] = []
        at = 0
        for n, text in enumerate(texts):
            span = Span(at, at + len(text))
            made.append(
                Candidate(
                    text=text,
                    anchor=Anchor.into(document, span),
                    score=1.0 - n * 0.01,
                    source_path=document.source_path,
                )
            )
            at += len(text)
        return made

    def test_a_duplicate_that_fits_is_still_sent(self) -> None:
        # Redundancy lowers priority; it does not veto. If the budget admits
        # both, both go.
        fitted = fit_to_budget(
            self._candidates(PASSAGE, PASSAGE),
            budget=Budget.characters(10_000),
            cost_of=len,
        )
        assert len(fitted.items) == 2
        assert fitted.omissions == ()

    def test_and_it_says_which_item_it_repeats(self) -> None:
        fitted = fit_to_budget(
            self._candidates(PASSAGE, PASSAGE),
            budget=Budget.characters(10_000),
            cost_of=len,
        )
        second = fitted.items[1]
        assert second.selection is not None
        assert any(s.startswith(REDUNDANT_SIGNAL) for s in second.selection.signals)
        assert any("overlap" in s for s in second.selection.signals)

    def test_a_duplicate_loses_priority_to_something_new(self) -> None:
        # The copy scores higher than the fresh passage, and still loses: that
        # is what "lowers priority" buys.
        candidates = self._candidates(PASSAGE, PASSAGE, ELSEWHERE)
        candidates[2] = Candidate(
            text=candidates[2].text,
            anchor=candidates[2].anchor,
            score=0.5,
            source_path=candidates[2].source_path,
        )
        fitted = fit_to_budget(
            candidates,
            budget=Budget.characters(len(PASSAGE) + len(ELSEWHERE) + 2),
            cost_of=len,
        )
        sent = {item.text for item in fitted.items}
        assert PASSAGE in sent
        assert ELSEWHERE in sent

    def test_a_duplicate_that_does_not_fit_says_it_repeats_rather_than_that_it_is_late(
        self,
    ) -> None:
        # "this repeats itm_001" is a better answer to *why* than "there was no
        # room" -- and only one of the two tells you the budget was not the
        # real problem.
        fitted = fit_to_budget(
            self._candidates(PASSAGE, PASSAGE),
            budget=Budget.characters(len(PASSAGE) + 5),
            cost_of=len,
        )
        assert len(fitted.items) == 1
        assert len(fitted.omissions) == 1
        omission = fitted.omissions[0]
        assert omission.rule is OmissionRule.REDUNDANT_CANDIDATE
        assert "itm_001" in omission.reason
        assert "overlap" in omission.reason

    def test_nothing_is_lost_between_the_two_lists(self) -> None:
        fitted = fit_to_budget(
            self._candidates(PASSAGE, PASSAGE, ELSEWHERE, PASSAGE),
            budget=Budget.characters(len(PASSAGE) + 5),
            cost_of=len,
        )
        assert fitted.accounts_for(4)

    def test_marking_does_not_change_the_result_when_it_finds_nothing(self) -> None:
        candidates = self._candidates(PASSAGE, ELSEWHERE)
        with_marking = fit_to_budget(candidates, budget=Budget.characters(10_000), cost_of=len)
        without = fit_to_budget(
            candidates, budget=Budget.characters(10_000), cost_of=len, redundancy_threshold=1.1
        )
        assert [i.text for i in with_marking.items] == [i.text for i in without.items]


class TestDeterminism:
    def test_the_same_candidates_produce_the_same_marks(self) -> None:
        texts = [PASSAGE, ELSEWHERE, PASSAGE, PASSAGE]
        assert mark_duplicates(texts) == mark_duplicates(texts)

    @pytest.mark.parametrize("threshold", [0.0, 0.5, DEFAULT_THRESHOLD, 1.0])
    def test_any_threshold_is_deterministic(self, threshold: float) -> None:
        texts = [PASSAGE, PASSAGE, ELSEWHERE]
        assert mark_duplicates(texts, threshold=threshold) == mark_duplicates(
            texts, threshold=threshold
        )
