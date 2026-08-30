"""Script-aware tokenization: bigrams for CJK, words for everything else.

ADR-0007 measured the alternatives. On one Japanese document and six queries:

    unicode61 (FTS5's default)   0 / 6
    trigram                      4 / 6
    bigram                       6 / 6

The default tokenizer indexes whole Japanese sentences as single tokens -- the
only break is the full stop -- so a search returns nothing, forever, with no
error. ``trigram`` cannot match a two-character query at all, and two-character
compounds are the backbone of written Japanese: 東京, 会議, 開発, 方針.

So ideographs and katakana are indexed as overlapping character bigrams.
Nothing else is: cutting ``budget`` into ``bu ud dg ge et`` loses precision and
buys nothing, because spaces already say where the words are -- and the same
argument turns out to cover hiragana, which is grammar rather than content, and
Hangul, which is spaced. Measured: dropping those two removed a fifth of the
index and moved no score (see ``_CJK_RANGES``). Deciding per run of text rather than
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

__all__ = ["BIGRAM_SCRIPTS", "BigramTokenizer", "is_bigrammed", "script_class", "script_runs"]

#: The scripts that get bigrammed: ideographs, and katakana.
#:
#: **Not hiragana, and not Hangul.** Both used to be here, and dropping them
#: cost nothing measurable and removed a fifth of the index. Measured across
#: four variants on the evaluation corpus, train and held-out agreeing:
#:
#:     bigram                            terms/char   recall   precision
#:     ideographs + all kana + hangul          0.40    92.6%       96.5%
#:     ideographs + katakana                   0.32    92.6%       96.5%
#:     ideographs only                         0.32    91.9%       96.4%
#:
#: The reasoning, once the numbers existed:
#:
#: **Hiragana is grammar.** Particles and inflection, so its bigrams are the
#: most frequent terms in a Japanese index and the least discriminating --
#: ``のの``, ``した``, ``ます``. Passing a hiragana run through whole keeps a
#: genuinely hiragana word findable and stops the index paying for ``は``.
#:
#: **Katakana is not.** It writes loan words and they concatenate:
#: ``スポーツクラブ`` has to be findable by ``スポーツ``, and a run passed
#: through whole would not be. The third row is what dropping it costs.
#:
#: **Hangul is spaced.** Korean writes its word boundaries, so a run is already
#: a word. This was the one assumption in the original list -- "compounds
#: inside a word behave the same way" -- true, and worth less than the index it
#: cost.
BIGRAM_SCRIPTS: Final = frozenset({"ideograph", "katakana"})

#: Every script class this distinguishes, and the ranges that define it. Runs
#: break at **every** change of class, not only at the boundary of what gets
#: bigrammed: when hiragana stopped being bigrammed it started merging into the
#: Latin run beside it, and ``tsumugiは予算`` indexed ``tsumugiは`` as one term
#: -- a token no query could produce.
_SCRIPTS: Final = (
    ("hiragana", ((0x3040, 0x309F),)),
    ("katakana", ((0x30A0, 0x30FF),)),
    ("hangul", ((0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F))),
    (
        "ideograph",
        ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2A6DF)),
    ),
)

#: Characters that separate terms in every script. Kept out of both the index
#: and the query so that punctuation cannot join two unrelated words into one
#: bigram.
_SEPARATORS: Final = frozenset(" \t\n\r　")


def script_class(character: str) -> str:
    """Which script a character belongs to, coarsely.

    ``other`` covers Latin, digits, punctuation and everything else: they are
    one class because spaces already say where their words end, and splitting
    ``config.toml`` at the full stop would lose a name.
    """
    code = ord(character)
    for name, ranges in _SCRIPTS:
        if any(low <= code <= high for low, high in ranges):
            return name
    return "other"


def is_bigrammed(character: str) -> bool:
    """Whether this character's run is cut into overlapping pairs."""
    return script_class(character) in BIGRAM_SCRIPTS


def script_runs(text: str) -> Iterator[tuple[str, str]]:
    """Split ``text`` into maximal runs, each labelled with its script class.

    Separators end a run and are dropped. Two runs of the same class separated
    by a space stay separate, which is what stops ``東京 会議`` from producing
    the bigram ``京会``.
    """
    run: list[str] = []
    kind: str | None = None

    for character in text:
        if character in _SEPARATORS:
            if run and kind is not None:
                yield kind, "".join(run)
            run, kind = [], None
            continue
        this = script_class(character)
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

    name = "bigram/script-aware@2"

    def _terms(self, text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        terms: list[str] = []
        for script, run in script_runs(normalized):
            if script not in BIGRAM_SCRIPTS:
                # Latin, digits, hiragana, Hangul: the run is already a word,
                # or is grammar rather than content. Either way, cutting it
                # into pairs buys nothing and costs index.
                terms.append(run)
                continue
            if len(run) == 1:
                # A lone ideograph has no bigram. Index it whole so that a
                # single-character query -- 山, 雨 -- can still find it.
                terms.append(run)
                continue
            terms.extend(run[i : i + 2] for i in range(len(run) - 1))
        return terms

    def index_terms(self, text: str) -> Sequence[str]:
        return self._terms(text)

    def query_terms(self, query: str) -> Sequence[str]:
        return self._terms(query)
