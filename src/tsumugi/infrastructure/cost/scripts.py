"""Counting characters by the kind of thing they are.

A tokenizer does not charge the same for every character. Latin prose packs
several characters into one token; a kanji is often a whole token by itself and
sometimes more than one. Using a single characters-per-token constant for both
is wrong by a factor rather than by a percent, and a Japanese-first library
cannot afford that (ADR-0006).

So the estimator counts characters per class and weights each. The classes are
chosen to be cheap to compute and to correspond to real differences in how
byte-pair encodings behave, not to Unicode's own categories.
"""

from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import Final

__all__ = ["ScriptClass", "classify", "profile"]


class ScriptClass(Enum):
    """The classes the estimator weights separately."""

    #: a-z, A-Z, and Latin-1/Extended letters.
    LATIN = "latin"
    #: CJK unified ideographs and compatibility forms.
    IDEOGRAPH = "ideograph"
    #: Hiragana and katakana.
    KANA = "kana"
    #: Hangul syllables and jamo.
    HANGUL = "hangul"
    #: 0-9 and other decimal digits.
    DIGIT = "digit"
    #: Spaces, tabs, newlines.
    SPACE = "space"
    #: Everything else: punctuation, symbols, emoji, Cyrillic, Arabic, and so on.
    OTHER = "other"


_IDEOGRAPH_RANGES: Final = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2A6DF),
)
_KANA_RANGES: Final = ((0x3040, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9D))
_HANGUL_RANGES: Final = ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF))


def _in(code: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= code <= high for low, high in ranges)


def classify(character: str) -> ScriptClass:
    """Which class one character belongs to."""
    if character.isspace():
        return ScriptClass.SPACE
    code = ord(character)
    if _in(code, _KANA_RANGES):
        return ScriptClass.KANA
    if _in(code, _IDEOGRAPH_RANGES):
        return ScriptClass.IDEOGRAPH
    if _in(code, _HANGUL_RANGES):
        return ScriptClass.HANGUL
    if character.isdigit():
        return ScriptClass.DIGIT
    if character.isalpha() and code < 0x0600:
        return ScriptClass.LATIN
    return ScriptClass.OTHER


def profile(text: str) -> Counter[ScriptClass]:
    """How many characters of each class ``text`` holds."""
    counts: Counter[ScriptClass] = Counter()
    for character in text:
        counts[classify(character)] += 1
    return counts
