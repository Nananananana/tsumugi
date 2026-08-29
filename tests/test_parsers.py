"""Parsers report structure over the original string and never rewrite it.

The rule that makes a hand-written Markdown reader an acceptable trade against
a dependency: being wrong about structure produces worse *sections*; it cannot
produce a wrong *anchor*. The last test in this file is that rule.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tsumugi.errors import ConfigurationError
from tsumugi.infrastructure.parsers import (
    MarkdownParser,
    known_parsers,
    parser_for,
    register_parser,
)
from tsumugi.infrastructure.parsers.lines import iter_lines
from tsumugi.infrastructure.parsers.plaintext import PlainTextParser, SourceCodeParser
from tsumugi.infrastructure.parsers.structured import JsonParser

DOCUMENT = """---
title: Budget notes
tags: design
---

# 予算

本文です。テントは 2.4kg。

## 単位

- tokens
- characters

```python
# not a heading
x = 1
```

> quoted
"""


class TestLines:
    def test_offsets_point_at_the_line(self) -> None:
        content = "one\ntwo\nthree"
        for line in iter_lines(content):
            assert line.span.slice(content) == line.text

    @pytest.mark.parametrize("terminator", ["\n", "\r\n", "\r"])
    def test_every_line_ending_a_real_corpus_contains(self, terminator: str) -> None:
        content = terminator.join(["one", "two", "three"])
        assert [line.text for line in iter_lines(content)] == ["one", "two", "three"]

    def test_an_empty_document_yields_one_empty_line(self) -> None:
        assert [line.text for line in iter_lines("")] == [""]


class TestMarkdown:
    @pytest.fixture
    def parsed(self) -> object:
        return MarkdownParser().parse(DOCUMENT)

    def test_headings_become_nested_sections(self) -> None:
        sections = MarkdownParser().parse(DOCUMENT).sections
        headings = [(s.level, s.heading) for s in sections]
        assert (1, "予算") in headings
        assert (2, "単位") in headings

    def test_a_section_ends_where_the_next_of_its_level_begins(self) -> None:
        content = "# A\n\nalpha\n\n# B\n\nbeta\n"
        sections = {s.heading: s for s in MarkdownParser().parse(content).sections}
        assert sections["A"].span.slice(content) == "# A\n\nalpha\n\n"
        assert "beta" not in sections["A"].span.slice(content)

    def test_a_subsection_is_inside_its_parent(self) -> None:
        sections = {s.heading: s for s in MarkdownParser().parse(DOCUMENT).sections}
        assert sections["予算"].span.contains(sections["単位"].span)

    def test_a_hash_inside_a_fence_is_not_a_heading(self) -> None:
        # The bug every hand-written Markdown reader has on its first day.
        headings = [s.heading for s in MarkdownParser().parse(DOCUMENT).sections]
        assert "not a heading" not in headings

    def test_a_fence_becomes_one_code_block(self) -> None:
        blocks = [b for b in MarkdownParser().parse(DOCUMENT).blocks if b.kind == "code"]
        assert len(blocks) == 1
        assert "x = 1" in blocks[0].span.slice(DOCUMENT)

    def test_an_unclosed_fence_runs_to_the_end_rather_than_crashing(self) -> None:
        content = "# A\n\n```\nnever closed\n"
        blocks = [b for b in MarkdownParser().parse(content).blocks if b.kind == "code"]
        assert len(blocks) == 1

    def test_front_matter_is_read_as_flat_metadata(self) -> None:
        metadata = MarkdownParser().parse(DOCUMENT).metadata
        assert metadata["title"] == "Budget notes"
        assert metadata["tags"] == "design"

    def test_list_items_and_quotes_are_distinguished(self) -> None:
        kinds = {b.kind for b in MarkdownParser().parse(DOCUMENT).blocks}
        assert {"list_item", "quote", "paragraph", "heading", "front_matter"} <= kinds

    def test_a_document_with_no_headings_still_has_a_section(self) -> None:
        # So that no caller has to special-case the shape.
        sections = MarkdownParser().parse("just prose.\n").sections
        assert len(sections) == 1
        assert sections[0].level == 0

    def test_text_before_the_first_heading_is_not_lost(self) -> None:
        content = "preamble\n\n# A\n\nbody\n"
        sections = MarkdownParser().parse(content).sections
        assert sections[0].span.start == 0


class TestOtherFormats:
    def test_plain_text_splits_on_blank_lines(self) -> None:
        content = "one\ntwo\n\nthree\n"
        blocks = PlainTextParser().parse(content).blocks
        assert [b.span.slice(content) for b in blocks] == ["one\ntwo", "three"]

    def test_source_is_kept_whole(self) -> None:
        content = "def f():\n    return 1\n"
        blocks = SourceCodeParser().parse(content).blocks
        assert len(blocks) == 1
        assert blocks[0].kind == "code"

    def test_json_keys_become_sections(self) -> None:
        content = '{"title": "settings", "budget": "tokens"}'
        headings = {s.heading for s in JsonParser().parse(content).sections}
        assert {"title", "budget"} <= headings

    def test_invalid_json_raises_rather_than_returning_nothing(self) -> None:
        # A silently unparsed document is one that quietly stops being findable.
        with pytest.raises(ValueError, match="not valid JSON"):
            JsonParser().parse("{oh no")


class TestRegistry:
    def test_a_suffix_resolves_to_its_parser(self) -> None:
        parser = parser_for("notes/a.md")
        assert parser is not None
        assert parser.name == "markdown@1"

    def test_an_unclaimed_suffix_returns_none_rather_than_raising(self) -> None:
        # A corpus folder is full of files nobody meant to index.
        assert parser_for("photo.png") is None

    def test_a_path_with_no_suffix_returns_none(self) -> None:
        assert parser_for("Makefile") is None

    def test_the_builtins_are_registered(self) -> None:
        assert {p.name for p in known_parsers()} >= {
            "markdown@1",
            "plaintext@1",
            "source@1",
            "json@1",
        }

    def test_a_new_format_needs_no_change_to_the_library(self) -> None:
        class OrgParser:
            name = "orgmode@1"
            suffixes = (".orgmode",)
            media_type = "text/x-org"

            def parse(self, content: str) -> object:  # pragma: no cover - shape only
                raise NotImplementedError

        register_parser(OrgParser())
        found = parser_for("notes/a.orgmode")
        assert found is not None
        assert found.name == "orgmode@1"

    def test_stealing_a_claimed_suffix_is_refused(self) -> None:
        # Two parsers silently fighting over .md would make a document's
        # structure depend on import order.
        class Rival:
            name = "rival@1"
            suffixes = (".md",)
            media_type = "text/markdown"

            def parse(self, content: str) -> object:  # pragma: no cover - shape only
                raise NotImplementedError

        with pytest.raises(ConfigurationError, match="already claimed"):
            register_parser(Rival())

    def test_a_parser_claiming_nothing_is_refused(self) -> None:
        class Empty:
            name = "empty@1"
            suffixes: tuple[str, ...] = ()
            media_type = "text/plain"

            def parse(self, content: str) -> object:  # pragma: no cover - shape only
                raise NotImplementedError

        with pytest.raises(ConfigurationError, match="claims no suffixes"):
            register_parser(Empty())


documents = st.text(alphabet=st.sampled_from(list("# \n-`>あ亜aA1。ｱ")), min_size=0, max_size=300)


class TestTheRuleThatMakesThisSafe:
    @given(content=documents)
    def test_no_parser_ever_reports_a_span_outside_the_document(self, content: str) -> None:
        # This is the whole trade of ADR-0001. A parser may be wrong about
        # structure; it may not produce an offset that does not exist, because
        # that is what would turn a bad parse into a bad anchor.
        for parser in (MarkdownParser(), PlainTextParser(), SourceCodeParser()):
            parsed = parser.parse(content)
            for section in parsed.sections:
                assert section.span.end <= len(content)
                assert section.span.slice(content) is not None
            for block in parsed.blocks:
                assert block.span.end <= len(content)
                assert block.span.slice(content) is not None

    @given(content=documents)
    def test_parsing_never_changes_the_text(self, content: str) -> None:
        before = content
        MarkdownParser().parse(content)
        assert content == before
