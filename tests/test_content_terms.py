"""Which characters of a question carry its subject, and which are grammar.

`_content_terms` turns `テントの重量は` into `[テント, 重量]`. The `の` and the
`は` are dropped, and that is the whole point: they are what changes when
somebody asks the same thing in different words. It decides **what a question
searches for**, so a wrong boundary in `_script_of` silently changes what a
CJK question means.

`python tools/mutate.py` found fourteen surviving mutants in `_script_of`
alone: every range boundary could be moved by one and nothing objected. The
suite exercised it only through whole queries whose characters sat comfortably
inside their ranges.

**Hiragana is the one script deliberately excluded**, and that is a decision
rather than an oversight: it writes particles and inflection, so it is grammar.
Hangul is *not* excluded — Korean glues particles to nouns with no script
change, which is why `INFLECTION_TAIL` exists.
"""

from __future__ import annotations

import pytest

from tsumugi.application.search import _content_terms, _script_of


class TestTheBoundariesOfEachScript:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            # Hiragana, both ends.
            (0x3040, "Hiragana"),
            (0x309F, "Hiragana"),
            (0x303F, "Other"),
            # Katakana, both blocks, both ends.
            (0x30A0, "Katakana"),
            (0x30FF, "Katakana"),
            (0xFF66, "Katakana"),
            (0xFF9D, "Katakana"),
            (0xFF65, "Other"),
            # Just past halfwidth katakana, and **not** Other: the halfwidth
            # voiced mark is alphabetic to Python, so it falls through to the
            # content fallback. That is right -- it carries sound, not grammar --
            # and it is written here because the first version of this test
            # expected Other and the code was the one telling the truth.
            (0xFF9E, "Latin"),
            # Han, all three blocks, both ends.
            (0x3400, "Han"),
            (0x4DBF, "Han"),
            (0x4E00, "Han"),
            (0x9FFF, "Han"),
            (0xF900, "Han"),
            (0xFAFF, "Han"),
            (0x33FF, "Other"),
            (0x4DC0, "Other"),
            # Past the Han blocks. A Yi syllable is alphabetic, so it is
            # content by the same fallback rather than by a range.
            (0xA000, "Latin"),
        ],
    )
    def test_a_code_point_lands_in_the_class_it_should(self, code: int, expected: str) -> None:
        """Each end of each range, and the character just outside it.

        A boundary moved by one is exactly what the mutation testing changed,
        and nothing noticed: `テ` at 0x30C6 sits far inside Katakana, so every
        real query kept working while the edges were undefended.
        """
        assert _script_of(chr(code)) == expected

    @pytest.mark.parametrize(
        ("character", "expected"),
        [
            ("7", "Digit"),
            ("٣", "Digit"),
            ("a", "Latin"),
            ("Z", "Latin"),
            ("é", "Latin"),
            ("한", "Latin"),
            (" ", "Other"),
            ("、", "Other"),
            ("!", "Other"),
        ],
    )
    def test_the_fallbacks_classify_by_what_python_knows(
        self, character: str, expected: str
    ) -> None:
        """`isdigit` before `isalpha`, and everything else is Other.

        `한` reads as Latin here, which is not a mistake to fix in this
        function: the name is coarse and the only question it answers is
        *content or grammar*. Hangul is content, and calling it Latin gets that
        right. `INFLECTION_TAIL` handles what the coarseness costs.
        """
        assert _script_of(character) == expected


class TestGrammarIsDropped:
    def test_japanese_particles_do_not_become_terms(self) -> None:
        assert _content_terms("テントの重量は") == ["テント", "重量"]

    def test_a_question_of_nothing_but_particles_has_no_terms(self) -> None:
        assert _content_terms("のはがを") == []

    def test_a_script_change_starts_a_new_term(self) -> None:
        """`東京タワー` is two terms, not one.

        A run is a run of *one* script, which is structure the string already
        has. Merging them would need a dictionary.
        """
        assert _content_terms("東京タワー") == ["東京", "タワー"]

    def test_digits_and_letters_are_separate_runs(self) -> None:
        assert _content_terms("tent 2.4kg") == ["tent", "2", "4", "kg"]

    def test_hangul_is_kept_because_korean_has_no_script_change(self) -> None:
        """The reason `INFLECTION_TAIL` exists.

        `가계부의` is `가계부` plus a particle, and the particle is Hangul like
        the stem — so the split that separates `テント` from `の` cannot help
        here, and the whole eojeol arrives as one term.
        """
        assert _content_terms("가계부의") == ["가계부의"]

    def test_punctuation_ends_a_run(self) -> None:
        assert _content_terms("テント、重量") == ["テント", "重量"]

    def test_an_empty_question_has_no_terms(self) -> None:
        assert _content_terms("") == []

    def test_a_trailing_run_is_not_lost(self) -> None:
        """The `if current` after the loop. Without it the last term of every
        question disappears, which no query ending in a particle would show."""
        assert _content_terms("の重量") == ["重量"]
        assert _content_terms("重量") == ["重量"]

    def test_full_width_characters_are_folded_first(self) -> None:
        """NFKC, so `ｔｅｎｔ` and `tent` are the same question."""
        assert _content_terms("ｔｅｎｔ") == ["tent"]
        assert _content_terms("ﾃﾝﾄ") == ["テント"]
