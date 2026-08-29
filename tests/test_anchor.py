"""Anchors: the invariant, and the three ways a check can come out.

The middle status is the point of the file. Evidence taken before an edit was
true when it was taken; reporting it as false, or silently moving it to
wherever the text went, are both worse than saying so (ADR-0010).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tsumugi.domain.anchor import Anchor, ResolutionStatus, resolve
from tsumugi.domain.hashing import ContentHash
from tsumugi.domain.span import Span

from .helpers import build_document

TEXT = "予算の単位は呼び出し側で明示する。The unit is explicit at the call site."


class TestAnchoring:
    def test_an_anchor_records_the_text_it_covers(self) -> None:
        document = build_document("a.md", TEXT)
        anchor = Anchor.into(document, Span(0, 6))
        assert anchor.text_hash == ContentHash.of("予算の単位は")

    def test_an_anchor_carries_both_identities(self) -> None:
        document = build_document("a.md", TEXT)
        anchor = Anchor.into(document, Span(0, 6))
        assert anchor.document_id == document.document_id
        assert anchor.version == document.version

    def test_an_anchor_with_no_document_is_refused(self) -> None:
        with pytest.raises(ValueError, match="anchors nothing"):
            Anchor("", Span(0, 1), ContentHash.of("x"), ContentHash.of("y"))


class TestResolution:
    def test_an_untouched_document_resolves(self) -> None:
        document = build_document("a.md", TEXT)
        result = resolve(Anchor.into(document, Span(0, 6)), document)
        assert result.status is ResolutionStatus.RESOLVED
        assert result.text == "予算の単位は"
        assert result.ok

    def test_an_edited_document_is_stale_not_wrong(self) -> None:
        original = build_document("a.md", TEXT)
        anchor = Anchor.into(original, Span(0, 6))
        edited = build_document("a.md", "前置き。" + TEXT)

        result = resolve(anchor, edited)
        assert result.status is ResolutionStatus.STALE
        assert not result.ok
        assert "changed since" in result.detail

    def test_stale_is_reported_even_when_the_text_happens_to_survive(self) -> None:
        # The span still holds the recorded text after an edit elsewhere. That
        # is luck, not a guarantee, and the caller should know the version it
        # was taken from no longer exists.
        original = build_document("a.md", TEXT)
        anchor = Anchor.into(original, Span(0, 6))
        edited = build_document("a.md", TEXT + "\n\n追記。")

        result = resolve(anchor, edited)
        assert result.status is ResolutionStatus.STALE
        assert result.text == "予算の単位は"
        assert "unchanged" in result.detail

    def test_an_anchor_past_the_end_is_unresolvable(self) -> None:
        document = build_document("a.md", TEXT)
        anchor = Anchor(document.document_id, Span(0, 9999), ContentHash.of("x"), document.version)
        result = resolve(anchor, document)
        assert result.status is ResolutionStatus.UNRESOLVABLE
        assert "characters" in result.detail

    def test_a_wrong_hash_at_the_same_version_is_unresolvable(self) -> None:
        # Same version, so this is not staleness. Either the store is corrupt
        # or the anchor was built by hand and wrongly.
        document = build_document("a.md", TEXT)
        anchor = Anchor(
            document.document_id, Span(0, 6), ContentHash.of("not that"), document.version
        )
        result = resolve(anchor, document)
        assert result.status is ResolutionStatus.UNRESOLVABLE
        assert "not what was recorded" in result.detail

    def test_checking_against_a_different_document_is_a_programming_error(self) -> None:
        # Not UNRESOLVABLE: the two are different problems and only one of them
        # is about the data.
        anchor = Anchor.into(build_document("a.md", TEXT), Span(0, 6))
        with pytest.raises(ValueError, match="was checked against"):
            resolve(anchor, build_document("b.md", TEXT))


# Documents that exercise the awkward cases: mixed scripts, full-width forms,
# emoji outside the basic plane, combining marks and newlines.
documents = st.text(
    alphabet=st.sampled_from(
        list("あア亜aA1 　\n\t。、．ｱＡ１̀é🗾"),
    ),
    min_size=1,
    max_size=200,
)


class TestTheInvariant:
    @given(content=documents, data=st.data())
    def test_a_span_always_slices_back_to_the_text_that_was_anchored(
        self, content: str, data: st.DataObject
    ) -> None:
        document = build_document("prop.md", content)
        start = data.draw(st.integers(min_value=0, max_value=len(content)))
        end = data.draw(st.integers(min_value=start, max_value=len(content)))
        anchor = Anchor.into(document, Span(start, end))

        result = resolve(anchor, document)

        assert result.status is ResolutionStatus.RESOLVED
        assert result.text == content[start:end]
        assert ContentHash.of(result.text or "") == anchor.text_hash

    @given(content=documents, addition=documents)
    def test_editing_a_document_never_produces_a_false_resolved(
        self, content: str, addition: str
    ) -> None:
        # The one thing that must never happen: an anchor into an old version
        # reporting RESOLVED against text it did not come from.
        original = build_document("prop.md", content)
        anchor = Anchor.into(original, Span(0, len(content)))
        edited = build_document("prop.md", content + addition)

        result = resolve(anchor, edited)

        if edited.version == original.version:
            assert result.status is ResolutionStatus.RESOLVED
        else:
            assert result.status is not ResolutionStatus.RESOLVED
