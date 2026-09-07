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


class TestOnlyPassagesThatShareSomethingAreCompared:
    """`mark_duplicates` used to ask every earlier head, and the answer for
    almost all of them was *nothing in common*.

    Containment is `shared / min(len(a), len(b))`, so a passage sharing no
    shingle with another cannot reach any threshold above zero. Asking anyway
    is the whole of the quadratic cost — and the case where every question gets
    that answer is a corpus of **unrelated** documents, which is the normal
    state of a well-kept notes folder and the opposite of what a
    duplicate-heavy fixture measures:

        candidates    no duplicates    all near-copies
        50              9.56 ms ->  1.42 ms      0.56 ms
        100            33.20 ms ->  3.37 ms      1.03 ms
        200           132.28 ms ->  8.72 ms      2.44 ms

    Duplicates were always cheap: a marked passage stops being a head and drops
    out of the comparison set. It is the *absence* of duplication that cost.
    """

    def _unrelated(self, count: int) -> list[str]:
        return [f"第{index}項について、独立した記述がここにある。" for index in range(count)]

    def test_passages_with_nothing_in_common_are_not_marked(self) -> None:
        assert mark_duplicates(["テントの重量は2.4kg", "the ledger holds no text"]) == {}

    def test_a_passage_sharing_no_shingle_is_not_a_duplicate_at_any_threshold(self) -> None:
        """Including a threshold of zero, which is reachable from a config file.

        `is_near_duplicate(0.0)` is `score >= 0.0`, so the old comparison
        marked **every** candidate as a duplicate of the first one, with a
        reason reading `0% overlap with itm_001`. That sentence is in a
        published package, and it is not true: two passages sharing no
        five-character run are not near-duplicates of each other, whatever
        number a caller put in a file.
        """
        pair = ["テントの重量は2.4kg", "the ledger holds no text"]
        assert mark_duplicates(pair, threshold=0.0) == {}

    def test_a_passage_that_shares_everything_is_still_marked_at_zero(self) -> None:
        """The positive control: a threshold of zero is permissive, not inert."""
        copy = "テントの重量は2.4kg、二人用である。"
        marks = mark_duplicates([copy, copy], threshold=0.0)
        assert marks[1][0] == 0
        assert marks[1][1].score == pytest.approx(1.0)

    def test_the_earliest_head_wins_a_tie(self) -> None:
        """Two heads equally alike, and the lower index has to win.

        The comparison set is now built by walking an inverted index, and
        `frozenset` iteration order over strings **moves between runs**. Two
        runs of the same query produce the same package (ADR-0003), and a
        tie-break that kept whichever candidate arrived first would have ended
        that guarantee -- silently, and only sometimes.
        """
        passage = "テントの重量は2.4kg、二人用である。予備は持たない。"
        marks = mark_duplicates(["前置き。" + passage, "別の前置き。" + passage, passage])
        assert marks[2][0] == 0, marks

    def test_a_chain_of_copies_collapses_to_one_survivor(self) -> None:
        """Only heads are indexed, which is the rule the old `earlier in marks`
        skip enforced. A chain of near-copies points at one passage rather than
        at each other."""
        passage = "集合場所は駅前の広場、七時とする。"
        marks = mark_duplicates([passage, passage + "（写し）", passage + "（写しの写し）"])
        assert [marks[index][0] for index in (1, 2)] == [0, 0]

    @given(
        texts=st.lists(
            st.sampled_from(
                [
                    "テントの重量は2.4kg、二人用である。",
                    "テントの重量は2.4kg、二人用である。（追記）",
                    "予備の電池は持たない方針にした。",
                    "the ledger holds no text of its own",
                    "the ledger holds no text of its own, and that is deliberate",
                    "",
                    "短い",
                ]
            ),
            max_size=12,
        ),
        threshold=st.floats(0.01, 1.0),
    )
    def test_it_agrees_with_comparing_every_pair(self, texts: list[str], threshold: float) -> None:
        """The property the rewrite has to satisfy: nothing that could pass is
        skipped, and the numbers are the same ones.

        Stated against a direct all-pairs comparison written here, so it cannot
        pass by the two implementations sharing a mistake. Thresholds start at
        0.01 rather than 0: at exactly zero the two deliberately differ, which
        is the case above.
        """
        prepared = [shingles(text) for text in texts]
        expected: dict[int, tuple[int, float]] = {}
        for index in range(1, len(texts)):
            best: tuple[int, float] | None = None
            for earlier in range(index):
                if earlier in expected:
                    continue
                found = similarity(texts[earlier], texts[index])
                if found.is_near_duplicate(threshold) and (best is None or found.score > best[1]):
                    best = (earlier, found.score)
            if best is not None:
                expected[index] = best

        found_marks = mark_duplicates(texts, threshold=threshold)
        assert found_marks.keys() == expected.keys(), (texts, threshold)
        for index, (head, score) in expected.items():
            assert found_marks[index][0] == head
            assert found_marks[index][1].score == pytest.approx(score)
        assert len(prepared) == len(texts)
