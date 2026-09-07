"""The window a citation shows, and where it is allowed to cut.

`_widen` grows a bare match outwards so an item carries the sentence around it.
It is the code that decides **what text a reader actually sees under a
citation**, and proposal 0003 measured that at realistic document length *68%
of the recall loss was the window rather than the ranking* — the right document
found and confirmed, and the wrong part of it returned.

`python tools/mutate.py` found seventeen surviving mutants across `_widen`,
`_sentence_start` and `_sentence_end`: seventeen changes to that decision that
no test objected to. The suite exercised widening only through corpora where
every fact sits on its own line, which is the one shape where the sentence
rules never fire.

The rules being pinned here:

- a **hard stop** (`。．！？!?`) ends a sentence anywhere;
- a **soft stop** (`.`) ends one only when what follows is space or the text
  ends, so `2.4kg` does not cut an item in half -- and `e.g.` **does**, which
  the source claimed otherwise until this file tested the claim;
- a **line break** binds before either;
- `context` is a ceiling in both directions, and the match itself is never cut;
- a window does not open on the space after the previous full stop.
"""

from __future__ import annotations

import pytest

from tsumugi.application.search import _sentence_end, _sentence_start, _widen
from tsumugi.domain.span import Span

NL = chr(10)


def _window(content: str, needle: str, context: int = 400) -> str:
    """The text `_widen` would return for a match on ``needle``."""
    start = content.index(needle)
    return _widen(content, Span(start, start + len(needle)), context).slice(content)


class TestTheSoftStopDoesNotCutANumber:
    def test_a_decimal_does_not_end_a_sentence(self) -> None:
        """`2.4kg` is the shape this rule exists for.

        A full stop with a digit after it is not a sentence end, and treating
        it as one returns `4kg.` as the evidence for how heavy the tent is.
        """
        content = "The tent weighs 2.4kg in its bag. Next thing."
        assert _window(content, "2.4kg") == "The tent weighs 2.4kg in its bag."

    def test_an_abbreviation_does_end_a_sentence_and_that_is_known(self) -> None:
        """`e.g.` is **not** protected, and the comment used to say it was.

        A space follows that full stop, so the soft-stop rule fires and the
        window starts after it: the citation reads " a head torch, and spare
        cells." rather than including the clause that introduced it.

        Protecting it needs a list of abbreviations, which is a per-language
        resource this project has refused three times (ADR-0007, ADR-0018,
        ADR-0019). So the behaviour is pinned as it is, and the source no
        longer claims otherwise.
        """
        content = "Bring a light, e.g. a head torch, and spare cells. Next."
        window = _window(content, "head torch")
        assert window == "a head torch, and spare cells."
        assert "e.g." not in window

    def test_a_full_stop_followed_by_space_does_end_one(self) -> None:
        content = "First sentence here. The tent weighs 2.4kg. Third one."
        assert _window(content, "tent weighs") == "The tent weighs 2.4kg."

    def test_a_full_stop_at_the_very_end_ends_one(self) -> None:
        """`index + 1 >= len(content)` — the end of the text is a boundary."""
        content = "Only one sentence about the tent."
        assert _window(content, "tent") == content


class TestHardStopsEndASentenceAnywhere:
    @pytest.mark.parametrize("stop", ["。", "．", "！", "？", "!", "?"])
    def test_each_terminator_bounds_the_window(self, stop: str) -> None:
        """No space required after a hard stop: Japanese does not write one."""
        content = f"前の文{stop}テントは2.4kg{stop}次の文{stop}"
        window = _window(content, "テントは")
        assert window.startswith("テント"), window
        assert window.endswith(stop), window
        assert "前の文" not in window, window
        assert "次の文" not in window, window


class TestALineBreakBindsFirst:
    def test_the_window_never_crosses_a_newline(self) -> None:
        content = "A previous line." + NL + "The tent weighs 2.4kg" + NL + "A following line."
        window = _window(content, "tent weighs")
        assert NL not in window, window
        assert window == "The tent weighs 2.4kg"

    def test_a_line_break_wins_over_a_later_sentence_end(self) -> None:
        content = "The tent weighs 2.4kg" + NL + "and more text. Then a sentence."
        assert _window(content, "tent") == "The tent weighs 2.4kg"


class TestTheContextLimitIsACeiling:
    def test_a_tight_context_bounds_both_ends(self) -> None:
        content = "x" * 200 + "NEEDLE" + "y" * 200
        window = _widen(content, Span(200, 206), 10)
        assert window.start == 190
        assert window.end == 216
        assert len(window.slice(content)) == 26

    def test_the_match_itself_is_never_cut(self) -> None:
        """`max(end, span.end)`. A context of zero must still return the match,
        not an empty span that anchors to nothing."""
        content = "x" * 50 + "NEEDLE" + "y" * 50
        window = _widen(content, Span(50, 56), 0)
        assert window.slice(content) == "NEEDLE"

    def test_it_never_reaches_outside_the_text(self) -> None:
        content = "The tent weighs 2.4kg"
        window = _widen(content, Span(4, 8), 10_000)
        assert window.start == 0
        assert window.end == len(content)


class TestTheHelpersOnTheirOwn:
    def test_sentence_start_stops_at_the_floor(self) -> None:
        """No terminator anywhere: the floor is the answer, not -1."""
        content = "no terminators at all in this text"
        assert _sentence_start(content, 5, 20) == 5

    def test_sentence_end_stops_at_the_ceiling(self) -> None:
        content = "no terminators at all in this text"
        assert _sentence_end(content, 5, 20) == 20

    def test_sentence_start_returns_the_character_after_the_stop(self) -> None:
        """Off by one here puts the previous sentence's full stop inside the
        window, which reads as a fragment of the wrong sentence."""
        content = "First. Second."
        assert _sentence_start(content, 0, 10) == len("First. ") - 1
        assert content[_sentence_start(content, 0, 10) :].startswith(" Second")

    def test_sentence_end_includes_the_stop(self) -> None:
        content = "First. Second."
        assert _sentence_end(content, 0, len(content)) == len("First.")

    def test_a_floor_equal_to_the_position_scans_nothing(self) -> None:
        content = "First. Second."
        assert _sentence_start(content, 7, 7) == 7


class TestTheEdgesTheFirstPassMissed:
    """A second mutation sweep, after the cases above were written.

    Everything here is one character wide. That is not a coincidence: a window
    is defined entirely by where it stops, so every remaining question about it
    is a question about a single index.
    """

    def test_a_terminator_directly_before_the_match_is_seen(self) -> None:
        """`range(at - 1, floor - 1, -1)` starts at `at - 1`, and the character
        directly before a match is exactly where a stop most often is -- a
        match on the first word of a sentence. Start the scan one earlier and
        that stop is invisible, so the window swallows the sentence before it.
        """
        content = "前の文。テントの重量"
        assert _sentence_start(content, 0, 4) == 4
        assert _window(content, "テント") == "テントの重量"

    def test_a_full_stop_two_from_the_end_is_not_a_sentence_end(self) -> None:
        """`index + 1 >= len(content)`, and the `+ 1` is the whole rule.

        A soft stop ends a sentence when the text ends *immediately after it*.
        At one character from the end it does not: `tent.x` is not two
        sentences, and treating it as one cuts the last character off the
        evidence.
        """
        content = "A tent.x"
        assert _sentence_end(content, 0, len(content)) == len(content)

    def test_a_full_stop_at_the_very_end_still_is_one(self) -> None:
        """The positive control for the test above."""
        content = "A tent."
        assert _sentence_end(content, 0, len(content)) == len(content)

    def test_a_window_after_a_newline_keeps_the_first_character_of_the_line(self) -> None:
        """`line_start + 1` -- the character *after* the newline, not two after
        it. Off by one here drops the first letter of the line from every
        citation that begins one, and a citation reading `he tent weighs 2.4kg`
        is a quotation of something nobody wrote."""
        content = "first line" + NL + "The tent weighs 2.4kg"
        assert _window(content, "tent") == "The tent weighs 2.4kg"

    def test_a_window_always_contains_the_match_it_grew_from(self) -> None:
        """The trim that drops the space after a full stop is bounded by
        `start < span.start`, and the bound is the point.

        Widen a span that itself begins on a space and an unbounded trim walks
        past the match's own start, returning a window that begins *inside*
        what it was widened around. An anchor built from it quotes less than
        was matched, and the `text_hash` is of text that does not contain the
        evidence.
        """
        content = "abc.  def ghi"
        match = Span(5, 9)  # begins on the second space
        assert content[match.start].isspace(), "the case only exists on a leading space"

        window = _widen(content, match, 400)

        assert window.start <= match.start, window
        assert window.end >= match.end, window
        assert content[match.start : match.end] in window.slice(content)
