"""The tokenizer ADR-0007 measured into existence.

The failure being guarded against is specific: SQLite's default tokenizer turns
a whole Japanese sentence into one token, so search returns nothing forever and
raises nothing. These tests are the reason that cannot come back.
"""

from __future__ import annotations

import pytest

from tsumugi.infrastructure.index.tokenization import BigramTokenizer, is_cjk, script_runs


@pytest.fixture
def tokenizer() -> BigramTokenizer:
    return BigramTokenizer()


class TestScriptDetection:
    @pytest.mark.parametrize("character", ["東", "あ", "ア", "京", "한"])
    def test_cjk_is_recognised(self, character: str) -> None:
        assert is_cjk(character)

    @pytest.mark.parametrize("character", ["a", "Z", "1", "-", "。", "、"])
    def test_everything_else_is_not(self, character: str) -> None:
        assert not is_cjk(character)

    def test_a_mixed_line_splits_into_runs(self) -> None:
        assert list(script_runs("東京tokyo会議")) == [
            (True, "東京"),
            (False, "tokyo"),
            (True, "会議"),
        ]

    def test_spaces_end_a_run(self) -> None:
        # Otherwise "東京 会議" would produce the bigram 京会, joining two
        # unrelated words.
        assert list(script_runs("東京 会議")) == [(True, "東京"), (True, "会議")]

    def test_a_full_width_space_ends_a_run_too(self) -> None:
        assert list(script_runs("東京　会議")) == [(True, "東京"), (True, "会議")]


class TestBigrams:
    def test_japanese_becomes_overlapping_pairs(self, tokenizer: BigramTokenizer) -> None:
        assert list(tokenizer.index_terms("東京の会議")) == ["東京", "京の", "の会", "会議"]

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
        assert list(tokenizer.index_terms("tsumugiは予算")) == ["tsumugi", "は予", "予算"]


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
        assert "京の" in tokenizer.index_terms("東京の会議")

    def test_a_query_is_tokenized_the_same_way_as_a_document(
        self, tokenizer: BigramTokenizer
    ) -> None:
        # If the two diverged, a document would be findable by terms no query
        # could ever produce.
        text = "予算の単位は明示する"
        assert list(tokenizer.index_terms(text)) == list(tokenizer.query_terms(text))
