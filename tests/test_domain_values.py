"""Value object invariants: spans, hashes, documents and their structure."""

from __future__ import annotations

import pytest

from tsumugi.domain.document import Block, Document, Section, known_block_kinds, register_block_kind
from tsumugi.domain.hashing import ContentHash
from tsumugi.domain.span import Span

from .helpers import build_document


class TestSpan:
    def test_a_span_knows_its_length(self) -> None:
        assert len(Span(4, 11)) == 7

    def test_an_empty_span_is_allowed(self) -> None:
        assert Span(3, 3).is_empty

    @pytest.mark.parametrize(("start", "end"), [(-1, 4), (9, 2)])
    def test_an_impossible_span_is_refused(self, start: int, end: int) -> None:
        with pytest.raises(ValueError, match="span"):
            Span(start, end)

    def test_slicing_past_the_end_raises_rather_than_clamping(self) -> None:
        # Python would silently return a short string. A clamped anchor
        # resolves to the wrong text without saying so, which is the failure
        # this library exists to prevent.
        with pytest.raises(ValueError, match="runs past"):
            Span(0, 99).slice("short")

    def test_overlap_is_half_open(self) -> None:
        assert not Span(0, 5).overlaps(Span(5, 9))
        assert Span(0, 6).overlaps(Span(5, 9))

    def test_containment(self) -> None:
        assert Span(0, 10).contains(Span(2, 4))
        assert not Span(2, 4).contains(Span(0, 10))


class TestContentHash:
    def test_it_renders_and_parses_round_trip(self) -> None:
        original = ContentHash.of("テスト")
        assert ContentHash.parse(str(original)) == original

    def test_the_algorithm_is_part_of_the_value(self) -> None:
        assert str(ContentHash.of("x")).startswith("sha256:")

    def test_the_same_text_hashes_the_same_way_under_utf8(self) -> None:
        assert ContentHash.of("東京") == ContentHash.of("東京")

    def test_an_unqualified_string_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a qualified hash"):
            ContentHash.parse("9f2c4a")

    def test_an_unsupported_algorithm_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unsupported"):
            ContentHash.of("x", "md5")

    def test_uppercase_hex_is_refused(self) -> None:
        # Two spellings of one digest compare unequal, so only one is allowed
        # to exist.
        with pytest.raises(ValueError, match="lowercase"):
            ContentHash("sha256", "9F2C")


class TestBlockKinds:
    def test_the_builtin_kinds_are_registered(self) -> None:
        assert "paragraph" in known_block_kinds()
        assert "code" in known_block_kinds()

    def test_an_unknown_kind_cannot_be_used(self) -> None:
        with pytest.raises(ValueError, match="unknown block kind"):
            Block(kind="slide_note", span=Span(0, 1))

    def test_a_new_kind_can_be_registered_without_patching_the_module(self) -> None:
        register_block_kind("slide_note", "a note attached to a slide")
        assert Block(kind="slide_note", span=Span(0, 1)).kind == "slide_note"

    def test_registering_the_same_kind_twice_the_same_way_is_fine(self) -> None:
        register_block_kind("cell", "one cell of a spreadsheet")
        register_block_kind("cell", "one cell of a spreadsheet")

    def test_redefining_a_kind_is_refused(self) -> None:
        # Two parsers quietly meaning different things by one name is the
        # failure the registry guards against.
        with pytest.raises(ValueError, match="already registered"):
            register_block_kind("paragraph", "something else entirely")


class TestDocument:
    def test_its_id_is_derived_from_the_path_not_the_content(self) -> None:
        first = build_document("notes/a.md", "one")
        second = build_document("notes/a.md", "two, entirely different")
        assert first.document_id == second.document_id
        assert first.version != second.version

    def test_two_paths_are_two_documents(self) -> None:
        assert build_document("a.md", "x").document_id != build_document("b.md", "x").document_id

    def test_the_id_does_not_contain_the_path(self) -> None:
        # Ids reach logs, exports and packages. A path carries a person's name
        # and directory layout.
        document = build_document("/home/someone/private/diary.md", "x")
        assert "someone" not in document.document_id
        assert "diary" not in document.document_id

    def test_verify_accepts_a_consistent_document(self) -> None:
        build_document("a.md", "hello").verify()

    def test_verify_rejects_a_version_that_does_not_match_its_text(self) -> None:
        document = build_document("a.md", "hello")
        tampered = Document(
            document_id=document.document_id,
            version=document.version,
            source_path=document.source_path,
            media_type=document.media_type,
            content="something else",
        )
        with pytest.raises(ValueError, match="does not match"):
            tampered.verify()

    def test_a_section_running_past_the_document_is_refused(self) -> None:
        with pytest.raises(ValueError, match="runs past"):
            build_document("a.md", "short", sections=(Section("H", 1, Span(0, 500)),))

    def test_a_block_running_past_the_document_is_refused(self) -> None:
        with pytest.raises(ValueError, match="runs past"):
            build_document("a.md", "short", blocks=(Block("paragraph", Span(0, 500)),))

    def test_section_at_returns_the_innermost(self) -> None:
        document = build_document(
            "a.md",
            "x" * 100,
            sections=(
                Section("outer", 1, Span(0, 100)),
                Section("inner", 2, Span(40, 80)),
            ),
        )
        found = document.section_at(50)
        assert found is not None
        assert found.heading == "inner"

    def test_section_at_returns_none_outside_every_section(self) -> None:
        document = build_document("a.md", "x" * 100, sections=(Section("s", 1, Span(0, 10)),))
        assert document.section_at(50) is None

    def test_a_body_span_excludes_the_heading(self) -> None:
        section = Section("Budget", 2, Span(0, 40), heading_span=Span(0, 10))
        assert section.body_span() == Span(10, 40)

    def test_a_heading_outside_its_own_section_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not inside"):
            Section("Budget", 2, Span(20, 40), heading_span=Span(0, 10))
