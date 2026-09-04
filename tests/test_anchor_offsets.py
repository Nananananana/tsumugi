"""Matching happens in folded space; anchors live in the original. Not the same length.

The defect this file exists for was live and silent. `_confirm` and
`_confirm_by_coverage` searched `unicodedata.normalize("NFKC", content)` and
applied the resulting offsets straight to `content`. NFKC does not preserve
length:

    ㈱   -> (株)        1 -> 3
    ½    -> 1/2         1 -> 3
    ﬁ    -> fi          1 -> 2      <- what PDF extraction produces
    ｶ ﾞ  -> ガ          2 -> 1      <- half-width katakana, ordinary in exports

so every offset after one of those was wrong by the delta. A document beginning
`ｶﾞｲﾄﾞ:` anchored `テントは` two characters early and returned `': テ'`.

**It was silent because it was self-consistent.** `item.text` and
`item.text_hash` were both computed from the wrong span, so they agreed, and
`verify` resolved the anchor against the document and found exactly what the
anchor claimed. A citation pointing at the wrong text, verifying clean.

`_confirm`'s own comment said *"per-character normalization keeps lengths
aligned for the common case"*. The code did whole-string normalization, and the
sentence described a mitigation that was not there.

**No test could have caught it**: 0 of the 780 documents in `tests/cases` change
length under NFKC, so the corpus is not merely a mirror of its author's
vocabulary — it is a mirror of the author's *character repertoire*, and against
this defect it is powerless. `kiseki` named that category on the day this was
found.
"""

from __future__ import annotations

import unicodedata

from hypothesis import given
from hypothesis import strategies as st

from tsumugi.application.search import (
    _confirm,
    _confirm_by_coverage,
    _content_terms,
    _fold_with_origins,
    _needles,
)

#: One of each way NFKC changes length, plus a stable character to sit beside.
AWKWARD = "".join(
    (
        chr(0xFF76) + chr(0xFF9E),  # half-width ka + voiced mark -> ガ   (2 -> 1)
        chr(0xFB01),  # fi ligature                    -> fi   (1 -> 2)
        chr(0x3231),  # circled kabushiki-gaisha       -> (株) (1 -> 3)
        chr(0xBD),  # one half                       -> 1/2  (1 -> 3)
        chr(0x2460),  # circled one                    -> 1    (1 -> 1)
    )
)


class TestTheFold:
    """`_fold_with_origins` agrees with the fold it replaces, and maps back."""

    @given(st.text(max_size=120))
    def test_it_folds_exactly_as_whole_string_nfkc_casefold_does(self, text: str) -> None:
        """The grouping must not change *what* matches, only where it points.

        If this drifted, retrieval behaviour would change silently and every
        measured number in `docs/measurements.md` would be about a different
        matcher than the one shipped.
        """
        folded, _origins = _fold_with_origins(text)
        assert folded == unicodedata.normalize("NFKC", text).casefold()

    @given(st.text(max_size=120))
    def test_every_folded_character_names_a_real_original_index(self, text: str) -> None:
        folded, origins = _fold_with_origins(text)
        assert len(origins) == len(folded)
        assert all(0 <= o < len(text) for o in origins)
        # Non-decreasing: folding never reorders, so a later folded character
        # cannot come from an earlier place. A span built from a map that went
        # backwards would have `end < start`.
        assert list(origins) == sorted(origins)

    def test_the_awkward_characters_are_actually_awkward(self) -> None:
        """The fixture has to be able to show the defect.

        Written because the corpus could not: a test whose data cannot express
        the failure passes for the wrong reason, and this is the assertion that
        says the data can.
        """
        assert len(unicodedata.normalize("NFKC", AWKWARD)) != len(AWKWARD)


class TestTheAnchorLandsOnWhatMatched:
    """The span points at text that contains the thing that was confirmed."""

    def test_a_voiced_halfwidth_prefix_does_not_shift_the_anchor(self) -> None:
        """The original failure, exactly.

        `ｶﾞｲﾄﾞ` is five code points that fold to three, so everything after it
        used to anchor two characters early.
        """
        prefix = chr(0xFF76) + chr(0xFF9E) + chr(0xFF72) + chr(0xFF84) + chr(0xFF9E) + ": "
        content = prefix + "テントは 2.4kg です。"

        spans, _matched = _confirm(content, _needles("テントは"))
        assert spans, "the phrase was not confirmed at all"
        assert content[spans[0].start : spans[0].end] == "テントは"

        covered = _confirm_by_coverage(content, _content_terms("テントは"))
        assert covered, "coverage did not confirm"
        assert "テント" in content[covered[0].start : covered[0].end]

    @given(
        prefix=st.text(alphabet=AWKWARD, max_size=8),
        gap=st.text(alphabet="  \n abc", max_size=6),
    )
    def test_whatever_precedes_it_the_phrase_is_where_the_span_says(
        self, prefix: str, gap: str
    ) -> None:
        """Any amount of length-changing text in front, and the span still lands.

        This is the property the offsets exist for, and the one a corpus of
        plain Japanese and English could never have exercised.
        """
        needle = "テントは"
        content = prefix + gap + needle + " 2.4kg"
        spans, _matched = _confirm(content, [needle])
        assert spans, "the phrase was not confirmed"
        assert content[spans[0].start : spans[0].end] == needle
