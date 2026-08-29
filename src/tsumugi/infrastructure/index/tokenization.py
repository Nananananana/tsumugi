"""Script-aware tokenization: bigrams for CJK, words for everything else.

ADR-0007 measured the alternatives. On one Japanese document and six queries:

    unicode61 (FTS5's default)   0 / 6
    trigram                      4 / 6
    bigram                       6 / 6

The default tokenizer indexes whole Japanese sentences as single tokens -- the
only break is the full stop -- so a search returns nothing, forever, with no
error. ``trigram`` cannot match a two-character query at all, and two-character
compounds are the backbone of written Japanese: 東京, 会議, 開発, 方針.

So CJK is indexed as overlapping character bigrams. Latin runs are not: cutting
``budget`` into ``bu ud dg ge et`` loses precision and buys nothing, because
spaces already say where the words are. Deciding per run of text rather than
per document is what makes a mixed Japanese/English/code corpus work, and it
mirrors `mamori`'s script-driven language-pack selection.

The output over-generates on purpose. ``東京の会議`` yields ``京の``, which is not
a word, so a search for 東京 can surface a document that merely contains those
characters adjacently. That is a candidate, not a result: confirmation happens
against the anchored text, where it costs one string comparison. Approximate
retrieval confirmed by exact evidence is the shape of the whole library.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator, Sequence
from typing import Final

__all__ = ["BigramTokenizer", "is_cjk", "script_runs"]

#: Unicode blocks whose characters carry meaning one or two at a time and are
#: written without spaces. Hangul syllables are included: Korean is spaced in
#: modern use, but compounds inside a word behave the same way, and indexing
#: them both ways costs only index size.
_CJK_RANGES: Final = (
    (0x3040, 0x30FF),  # hiragana, katakana
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # compatibility ideographs
    (0xAC00, 0xD7AF),  # hangul syllables
    (0x20000, 0x2A6DF),  # extension B
)

#: Characters that separate terms in every script. Kept out of both the index
#: and the query so that punctuation cannot join two unrelated words into one
#: bigram.
_SEPARATORS: Final = frozenset(" \t\n\r　")


def is_cjk(character: str) -> bool:
    """Whether a character is written without spaces between words."""
    code = ord(character)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def script_runs(text: str) -> Iterator[tuple[bool, str]]:
    """Split ``text`` into maximal runs, each flagged as CJK or not.

    Separators end a run and are dropped. Two runs of the same kind separated
    by a space stay separate, which is what stops ``東京 会議`` from producing
    the bigram ``京会``.
    """
    run: list[str] = []
    kind: bool | None = None

    for character in text:
        if character in _SEPARATORS:
            if run and kind is not None:
                yield kind, "".join(run)
            run, kind = [], None
            continue
        this = is_cjk(character)
        if kind is None:
            kind = this
        elif this != kind:
            yield kind, "".join(run)
            run, kind = [], this
        run.append(character)

    if run and kind is not None:
        yield kind, "".join(run)


class BigramTokenizer:
    """The built-in tokenizer. Satisfies :class:`~tsumugi.ports.tokenizer.Tokenizer`.

    Text is normalized first (NFKC, case-folded), so ``ｔｏｋｙｏ``, ``TOKYO`` and
    ``tokyo`` are one term and ``ﾄｳｷｮｳ`` matches ``トウキョウ``.
    """

    name = "bigram/script-aware@1"

    def _terms(self, text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        terms: list[str] = []
        for cjk, run in script_runs(normalized):
            if not cjk:
                # Latin, digits, symbols: the run is already a word.
                terms.append(run)
                continue
            if len(run) == 1:
                # A lone CJK character has no bigram. Index it whole so that a
                # single-character query -- 山, 雨 -- can still find it.
                terms.append(run)
                continue
            terms.extend(run[i : i + 2] for i in range(len(run) - 1))
        return terms

    def index_terms(self, text: str) -> Sequence[str]:
        return self._terms(text)

    def query_terms(self, query: str) -> Sequence[str]:
        return self._terms(query)
