"""Where a folded character came from in the text a reader will see.

Confirmation searches folded text — NFKC, casefolded — so that `ｔｅｎｔ` and
`tent` are the same question. Anchors are offsets into the **original**. The
bridge is `origins`: one entry per folded character, holding the index of the
source character it came from.

`tools/mutate.py`'s own docstring names the bug that motivated it, and it was
this bridge: *"`_confirm` computed anchor offsets in folded space and applied
them to the original; the tests were green because 0 of 780 corpus documents
change length under NFKC."* Every corpus of English or of Japanese kana passes
folding one character for one character, and every test written on such a
corpus proves nothing about the map.

Five mutants survived here. They are all the same failure with different
arithmetic: an anchor that points at text it does not quote, in a library whose
one claim is that it does.

The cases that actually move a boundary, none of which a normal corpus contains
in quantity:

| source | folded | what the map has to do |
|---|---|---|
| `ｶﾞ` (2 chars) | `ガ` (1) | one folded character, two consumed |
| `ﬁ` (1) | `fi` (2) | two folded characters, one origin, repeated |
| `㍿` (1) | `株式会社` (4) | four folded characters, one origin |
| `２` (1) | `2` (1) | the ordinary case, and the reason the rest hid |
"""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tsumugi.application.search import _fold, _folded, _origin, _source_span

#: `か` followed by a combining voiced mark: two code points that NFKC composes
#: into one. The shape a decomposing editor or a macOS filename produces.
DECOMPOSED = "がす"

#: Halfwidth katakana `ｶ` plus a halfwidth voiced mark, then `ｽ`.
HALFWIDTH_VOICED = "ｶﾞｽ"

#: The `fi` ligature, in the middle of a word so a wrong origin is visible.
LIGATURE = "officeﬁle"

#: One character that folds to four.
SQUARE = "㍿の記録"


class TestTheMapFromFoldedBackToOriginal:
    @pytest.mark.parametrize(
        ("source", "folded", "origins"),
        [
            # Two characters in, one out: the second origin skips to 2.
            (DECOMPOSED, "がす", (0, 2)),
            (HALFWIDTH_VOICED, "ガス", (0, 2)),
            # One character in, two out: the origin repeats.
            (LIGATURE, "officefile", (0, 1, 2, 3, 4, 5, 6, 6, 7, 8)),
            # One in, four out.
            (SQUARE, "株式会社の記録", (0, 0, 0, 0, 1, 2, 3)),
            # One for one, which is every document the corpus contains and the
            # reason none of the rows above were ever exercised.
            ("重量２．４ｋｇ", "重量2.4kg", (0, 1, 2, 3, 4, 5, 6)),
            ("①番", "1番", (0, 1)),
        ],
    )
    def test_each_folded_character_names_the_source_character_it_came_from(
        self, source: str, folded: str, origins: tuple[int, ...]
    ) -> None:
        assert _fold(source) == (folded, origins)

    def test_the_first_character_is_not_skipped(self) -> None:
        """`index = 0`. Starting the walk at 1 drops the first source
        character, and every anchor in the document then points one character
        early -- a citation that begins mid-word, on every passage."""
        folded, origins = _fold("重量２")
        assert folded.startswith("重")
        assert origins[0] == 0

    def test_the_scan_takes_one_character_at_a_time_unless_they_compose(self) -> None:
        """`size = 1`, and `size += 1` when a piece composes with the next.

        Start at two and every pair of unrelated characters is folded as a
        unit, so the second of each pair reports the first one's offset. Add
        two instead of one and a composing sequence swallows the character
        after it. Both are silent: the folded *text* is identical, and only
        the map is wrong.
        """
        _, pairs = _fold("重量２．４")
        assert pairs == (0, 1, 2, 3, 4), "unrelated characters are separate pieces"

        _, composing = _fold(DECOMPOSED)
        assert composing == (0, 2), "the composed pair consumes exactly two"


class TestPastTheEnd:
    def test_the_index_after_the_last_folded_character_is_the_length(self) -> None:
        """`at < len(origins)`, and the boundary is reached on every span that
        ends on the last character.

        A span's end is exclusive, so confirming a phrase that runs to the end
        of a document asks for the origin of `len(origins)`. Mutated to `<=`
        that is an `IndexError` in the middle of confirming a match -- and
        the passage it happens on is the one whose evidence runs to the end.
        """
        source = "重量２．４"
        _, origins = _fold(source)
        assert _origin(origins, len(origins), len(source)) == len(source)
        assert _origin(origins, len(origins) + 5, len(source)) == len(source)

    def test_an_index_inside_the_map_reads_the_map(self) -> None:
        """The positive control: the fallback is a fallback."""
        _, origins = _fold(SQUARE)
        assert _origin(origins, 4, len(SQUARE)) == 1
        assert _origin(origins, 0, len(SQUARE)) == 0


class TestTheMapIsUsable:
    @given(
        st.text(
            alphabet=st.sampled_from("aZ0９ｋガｶﾞ㍿ﬁ重量。 ①が"),
            min_size=0,
            max_size=40,
        )
    )
    def test_every_origin_points_somewhere_real_and_never_backwards(self, text: str) -> None:
        """Three properties that together make the map a map.

        In range, so no anchor can be built past the end of the document.
        Non-decreasing, because folding does not reorder -- a map that went
        backwards would produce a span whose end preceded its start, and
        `Span` refuses those, so the failure would surface as an exception
        somewhere unrelated.
        """
        folded, origins = _fold(text)
        assert len(origins) == len(folded)
        assert all(0 <= origin < len(text) for origin in origins)
        assert list(origins) == sorted(origins)

    @given(
        st.text(
            alphabet=st.sampled_from("aZ0９ｋガｶﾞ㍿ﬁ重量。 ①が"),
            min_size=1,
            max_size=40,
        )
    )
    def test_a_matched_run_maps_to_source_text_that_contains_it(self, text: str) -> None:
        """The property the whole file is about, stated once.

        Take any run of folded characters, ask `_source_span` where it came
        from, cut the original there and fold that: the run has to be in it.
        That is exactly what `_confirm` does to turn a match into an anchor.

        The first version of this said `_origin(start)` to `_origin(end)`, and
        hypothesis refused it in four examples: `ﬁ`. A run that begins and ends
        inside **one** source character maps both ends to the same origin, so
        the cut was empty and the run was in nothing. That is what
        `_source_span` exists to answer, and this is the property it answers
        to.
        """
        folded, origins = _fold(text)
        if not folded:
            return
        for start in range(len(folded)):
            for end in range(start + 1, len(folded) + 1):
                span = _source_span(origins, text, start, end)
                cut = unicodedata.normalize("NFKC", span.slice(text)).casefold()
                assert folded[start:end] in cut, (start, end, span)

    @given(
        st.text(
            alphabet=st.sampled_from("aZ0９ｋガｶﾞ㍿ﬁ重量。 ①が"),
            min_size=1,
            max_size=40,
        )
    )
    def test_no_match_ever_produces_an_empty_span(self, text: str) -> None:
        """An anchor is a promise that a quotation is *there*.

        An empty span is an anchor whose text is `""` and whose `text_hash` is
        the hash of nothing, offered as the evidence for a claim. It resolves
        RESOLVED, because the empty string really is at that offset.
        """
        folded, origins = _fold(text)
        for start in range(len(folded)):
            for end in range(start + 1, len(folded) + 1):
                span = _source_span(origins, text, start, end)
                assert span.end > span.start, (start, end, span)
                assert span.end <= len(text)


class TestTheCache:
    def test_two_callers_cannot_be_handed_the_same_mutable_map(self) -> None:
        """`origins` is a tuple, and the reason is the cache.

        A document is folded twice per confirmation -- once for the phrase
        rule, once for coverage -- so the cached value is handed out
        repeatedly. A list would let one caller's edit reach every later one,
        and the symptom would be anchors that are correct until some unrelated
        code touches them.
        """
        first = _folded(SQUARE)
        second = _folded(SQUARE)
        assert first is second
        assert isinstance(first[1], tuple)

    def test_the_cache_returns_what_the_uncached_path_would(self) -> None:
        """The cache is an optimisation and is checked as one: above
        `_CACHEABLE` the same work is simply done again, so the two paths have
        to agree or a large document confirms differently from a small one."""
        assert _folded(LIGATURE) == _fold(LIGATURE)


class TestAMatchThatEndsInsideOneCharacter:
    """The two rows in `_source_span`'s table, as cases rather than prose.

    Both were live before the property test above: the first quoted the wrong
    text, the second quoted nothing. Neither reached a reader, because every
    result is widened to its sentence first -- but `context` is a public
    parameter with a default, and an anchor that is only correct because of
    what a later function does to it is not a correct anchor.
    """

    def test_a_run_ending_mid_expansion_keeps_the_character_that_produced_it(self) -> None:
        """`0株` matches across two source characters, the second of which
        folds to four. Mapping the end alone gives `[0, 1)` -- the text `0`,
        offered as the evidence for `0株`."""
        text = "0㍿"
        folded, origins = _fold(text)
        assert folded == "0株式会社"

        span = _source_span(origins, text, 0, len("0株"))
        assert span.slice(text) == "0㍿"

    def test_a_run_wholly_inside_one_character_is_that_character(self) -> None:
        """`株式` is inside `㍿`. There is no smaller true answer: the span
        cannot be `[0, 0)`, because an empty quotation is not evidence, and it
        resolves RESOLVED -- the empty string really is at that offset."""
        text = "㍿の記録"
        _, origins = _fold(text)

        span = _source_span(origins, text, 0, 2)
        assert span.slice(text) == "㍿"
        assert span.end > span.start

    def test_an_ordinary_one_for_one_match_is_not_widened(self) -> None:
        """The positive control. A rule that always reached one character
        further would quote a character past every match in the corpus, and
        every document in the corpus folds one for one."""
        text = "テントの重量は2.4kg"
        _, origins = _fold(text)
        span = _source_span(origins, text, 0, len("テント"))
        assert span.slice(text) == "テント"
