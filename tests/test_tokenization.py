"""The tokenizer ADR-0007 measured into existence.

The failure being guarded against is specific: SQLite's default tokenizer turns
a whole Japanese sentence into one token, so search returns nothing forever and
raises nothing. These tests are the reason that cannot come back.
"""

from __future__ import annotations

import pytest

from tsumugi.infrastructure.index.tokenization import (
    BigramTokenizer,
    is_bigrammed,
    script_class,
    script_runs,
)


@pytest.fixture
def tokenizer() -> BigramTokenizer:
    return BigramTokenizer()


class TestScriptDetection:
    @pytest.mark.parametrize(
        ("character", "expected"),
        [
            ("東", "ideograph"),
            ("京", "ideograph"),
            ("あ", "hiragana"),
            ("ア", "katakana"),
            ("한", "hangul"),
            ("a", "other"),
            ("1", "other"),
            ("。", "other"),
        ],
    )
    def test_each_script_is_named(self, character: str, expected: str) -> None:
        assert script_class(character) == expected

    @pytest.mark.parametrize("character", ["東", "ア", "京"])
    def test_ideographs_and_katakana_are_bigrammed(self, character: str) -> None:
        assert is_bigrammed(character)

    @pytest.mark.parametrize("character", ["a", "Z", "1", "-", "。", "あ", "한"])
    def test_nothing_else_is(self, character: str) -> None:
        assert not is_bigrammed(character)

    def test_a_mixed_line_splits_into_runs(self) -> None:
        assert list(script_runs("東京tokyo会議")) == [
            ("ideograph", "東京"),
            ("other", "tokyo"),
            ("ideograph", "会議"),
        ]

    def test_a_run_breaks_at_every_script_change(self) -> None:
        # Not only at the boundary of what gets bigrammed. When hiragana
        # stopped being bigrammed it started merging into the Latin run beside
        # it, and `tsumugiは予算` indexed `tsumugiは` as one term -- a token no
        # query could ever produce.
        assert list(script_runs("tsumugiは予算")) == [
            ("other", "tsumugi"),
            ("hiragana", "は"),
            ("ideograph", "予算"),
        ]

    def test_spaces_end_a_run(self) -> None:
        # Otherwise "東京 会議" would produce the bigram 京会, joining two
        # unrelated words.
        assert list(script_runs("東京 会議")) == [("ideograph", "東京"), ("ideograph", "会議")]

    def test_a_full_width_space_ends_a_run_too(self) -> None:
        assert list(script_runs("東京　会議")) == [("ideograph", "東京"), ("ideograph", "会議")]


class TestBigrams:
    def test_ideographs_become_overlapping_pairs(self, tokenizer: BigramTokenizer) -> None:
        # The hiragana between them is its own run and passes through whole.
        # Measured: bigramming it cost a fifth of the index and bought nothing,
        # because particles are the most frequent terms and the least
        # discriminating.
        assert list(tokenizer.index_terms("東京の会議")) == ["東京", "の", "会議"]

    def test_katakana_still_becomes_pairs(self, tokenizer: BigramTokenizer) -> None:
        # Loan words concatenate: スポーツクラブ has to be findable by
        # スポーツ, and a run passed through whole would not be. Dropping this
        # is the one variant that cost recall.
        assert "スポ" in tokenizer.index_terms("スポーツクラブ")
        assert "クラ" in tokenizer.index_terms("スポーツクラブ")

    def test_hangul_is_left_as_words(self, tokenizer: BigramTokenizer) -> None:
        # Korean writes its word boundaries, so a run is already a word.
        assert list(tokenizer.index_terms("가계부 지출")) == ["가계부", "지출"]

    def test_a_two_character_compound_is_a_term(self, tokenizer: BigramTokenizer) -> None:
        # The whole point of ADR-0007: trigram cannot do this, and 2-character
        # compounds are the backbone of written Japanese.
        assert "東京" in tokenizer.query_terms("東京")
        assert "会議" in tokenizer.query_terms("会議")

    def test_a_lone_character_is_indexed_whole(self, tokenizer: BigramTokenizer) -> None:
        assert list(tokenizer.index_terms("山")) == ["山"]

    def test_latin_words_are_not_cut_into_pairs(self, tokenizer: BigramTokenizer) -> None:
        # "bu ud dg ge et" loses precision and buys nothing: spaces already say
        # where the words are.
        assert list(tokenizer.index_terms("budget limit")) == ["budget", "limit"]

    def test_a_mixed_sentence_is_handled_per_run(self, tokenizer: BigramTokenizer) -> None:
        assert list(tokenizer.index_terms("tsumugiは予算")) == ["tsumugi", "は", "予算"]


class TestNormalization:
    def test_full_width_latin_folds(self, tokenizer: BigramTokenizer) -> None:
        assert tokenizer.index_terms("ｔｏｋｙｏ") == tokenizer.query_terms("tokyo")

    def test_case_folds(self, tokenizer: BigramTokenizer) -> None:
        assert tokenizer.index_terms("Budget") == tokenizer.query_terms("BUDGET")

    def test_half_width_kana_matches_full_width(self, tokenizer: BigramTokenizer) -> None:
        assert tokenizer.index_terms("ﾄｳｷｮｳ") == tokenizer.query_terms("トウキョウ")


class TestTheOverGeneration:
    def test_it_produces_terms_that_are_not_words(self, tokenizer: BigramTokenizer) -> None:
        # On purpose. 京の is not a word, and a search for it can surface a
        # document about 東京の会議. That is a candidate, and confirmation
        # against the anchored text is what turns candidates into results.
        assert "京都" in tokenizer.index_terms("東京都会議")

    def test_a_query_is_tokenized_the_same_way_as_a_document(
        self, tokenizer: BigramTokenizer
    ) -> None:
        # If the two diverged, a document would be findable by terms no query
        # could ever produce.
        text = "予算の単位は明示する"
        assert list(tokenizer.index_terms(text)) == list(tokenizer.query_terms(text))
