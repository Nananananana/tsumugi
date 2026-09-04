"""Normalization, and the offset map back to the original.

The map is what lets the index normalize aggressively while every anchor keeps
pointing into text that exists on disk.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tsumugi.domain.span import Span
from tsumugi.domain.text import normalize
from tsumugi.infrastructure.index.tokenization import BigramTokenizer


class TestNormalize:
    def test_full_width_letters_fold_to_ascii(self) -> None:
        assert normalize("ｔｓｕｍｕｇｉ").text == "tsumugi"

    def test_half_width_kana_expand(self) -> None:
        assert normalize("ﾄｳｷｮｳ").text == "トウキョウ"

    def test_a_character_that_expands_keeps_one_origin_per_output(self) -> None:
        # ㍿ becomes four characters, all of which came from source index 0.
        result = normalize("㍿")
        assert result.text == "株式会社"
        assert result.origin == (0, 0, 0, 0)

    def test_plain_text_is_left_alone(self) -> None:
        result = normalize("東京の会議")
        assert result.text == "東京の会議"
        assert result.origin == (0, 1, 2, 3, 4)


class TestOffsetMap:
    def test_a_span_maps_back_to_the_characters_that_produced_it(self) -> None:
        result = normalize("aｂc")
        assert result.to_original(Span(1, 2)) == Span(1, 2)

    def test_an_expansion_maps_back_to_the_whole_source_character(self) -> None:
        # Asking for "式" -- one normalized character out of four -- has to
        # return the whole ㍿ it came from. There is no narrower true answer.
        result = normalize("前㍿後")
        assert result.text == "前株式会社後"
        assert result.to_original(Span(2, 3)) == Span(1, 2)

    def test_a_span_across_an_expansion_covers_both_sources(self) -> None:
        result = normalize("前㍿後")
        assert result.to_original(Span(0, 6)) == Span(0, 3)

    def test_an_empty_span_maps_to_an_empty_span(self) -> None:
        result = normalize("abc")
        assert result.to_original(Span(1, 1)) == Span(1, 1)


text = st.text(
    alphabet=st.sampled_from(list("あア亜aAｱＡ１㍿ﬁ 　\n。é")),
    min_size=1,
    max_size=120,
)


class TestProperties:
    @given(content=text)
    def test_the_map_has_one_entry_per_normalized_character(self, content: str) -> None:
        result = normalize(content)
        assert len(result.origin) == len(result.text)

    @given(content=text)
    def test_every_origin_points_inside_the_original(self, content: str) -> None:
        result = normalize(content)
        assert all(0 <= i < len(content) for i in result.origin)

    @given(content=text)
    def test_origins_never_go_backwards(self, content: str) -> None:
        # Normalization is per character, so the map is monotonic. Anything
        # else would make a span map to a discontiguous range.
        result = normalize(content)
        assert list(result.origin) == sorted(result.origin)

    @given(content=text, data=st.data())
    def test_a_mapped_span_always_contains_what_produced_the_normalized_text(
        self, content: str, data: st.DataObject
    ) -> None:
        result = normalize(content)
        if not result.text:
            return
        start = data.draw(st.integers(min_value=0, max_value=len(result.text) - 1))
        end = data.draw(st.integers(min_value=start + 1, max_value=len(result.text)))

        original_span = result.to_original(Span(start, end))

        # Re-normalizing the mapped original must produce a string containing
        # the normalized slice we asked about. Widening is allowed; losing the
        # text is not.
        assert result.text[start:end] in normalize(original_span.slice(content)).text


class TestKoreanReachesTheIndex:
    """Korean glues its particles to the noun, and the index used to miss it.

    `등록은` is *registration* plus the topic particle. A question written with
    the particle and a document written without one shared no term at all, so
    the index returned nothing and confirmation never ran -- which is not a
    near miss, it is silence. Four of the seven labelled cases where a package
    came back with no items *and* no candidates were Korean.

    The fix expands the **question** only. The three tests below are the three
    ways it could go wrong, and the third is the one that would be expensive.
    """

    def test_a_question_with_a_particle_reaches_a_document_without_one(self) -> None:
        tokenizer = BigramTokenizer()
        assert "등록" in tokenizer.query_terms("등록은 언제까지 가능하나요?"), (
            "the question cannot reach a document that says 등록"
        )

    def test_the_whole_word_is_still_offered_first(self) -> None:
        """Reducing must add a possibility, never replace one."""
        terms = list(BigramTokenizer().query_terms("등록은"))
        assert terms[0] == "등록은"
        assert "등록" in terms

    def test_indexing_is_untouched_so_existing_indexes_still_match(self) -> None:
        """**The expensive one.** `index_terms` must not expand.

        Expanding the index costs 5.8% more terms per character and buys
        exactly what the free side already bought. Worse, moving the expansion
        into `_terms` would change what is stored, and since `name` is
        unchanged nothing would force a rebuild -- every existing index would
        silently stop lining up, and the failure looks like an empty corpus.
        """
        tokenizer = BigramTokenizer()
        assert list(tokenizer.index_terms("등록은 마감일은")) == ["등록은", "마감일은"]
        assert tokenizer.name == "bigram/script-aware@2", (
            "the name pins the index format; changing it forces every user to rebuild"
        )

    def test_no_reduction_goes_down_to_a_single_syllable(self) -> None:
        """One syllable is not a morpheme and would match most of a corpus.

        Tested on a three-syllable word, because a one-syllable word has no
        reduction to make and would pass whatever the floor were set to --
        which is how the first version of this test missed `HANGUL_STEM = 1`.
        """
        terms = list(BigramTokenizer().query_terms("등록은"))
        assert terms == ["등록은", "등록"]
        assert all(len(term) >= 2 for term in terms), terms
        # A word that is already one syllable is passed through, not dropped.
        assert list(BigramTokenizer().query_terms("은")) == ["은"]

    @pytest.mark.parametrize("text", ["tokyo notes", "東京の会議", "テントは"])
    def test_no_other_script_is_affected(self, text: str) -> None:
        tokenizer = BigramTokenizer()
        assert list(tokenizer.query_terms(text)) == list(tokenizer.index_terms(text))
