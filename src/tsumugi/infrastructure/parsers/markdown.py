"""Markdown, read for structure and never rewritten.

Hand-written, and deliberately incomplete. A parser here reports spans over the
original string, so being wrong about a nested list produces worse *sections*
and cannot produce a wrong *anchor* -- which is what makes shipping an
incomplete reader an acceptable trade against a runtime dependency (ADR-0001).

What it understands: ATX headings, fenced code, blockquotes, list items, YAML
front matter, and paragraphs as whatever is left. What it does not: setext
headings, reference links, tables as anything other than lines, and every
Markdown extension there has ever been.
"""

from __future__ import annotations

from collections.abc import Iterator

from ...domain.document import Block, Section
from ...domain.span import Span
from ...ports.parser import ParsedDocument
from ..parsers.lines import Line, iter_lines

__all__ = ["MarkdownParser"]

_FENCES = ("```", "~~~")


class MarkdownParser:
    """Satisfies :class:`~tsumugi.ports.parser.Parser`."""

    name = "markdown@1"
    suffixes = (".md", ".markdown", ".mdown", ".mkd")
    media_type = "text/markdown"

    def parse(self, content: str) -> ParsedDocument:
        lines = list(iter_lines(content))
        blocks = tuple(self._blocks(lines))
        headings = [b for b in blocks if b.kind == "heading"]
        sections = tuple(self._sections(content, headings))
        return ParsedDocument(
            sections=sections,
            blocks=blocks,
            metadata=self._metadata(content, blocks),
        )

    # -- blocks ----------------------------------------------------------

    def _blocks(self, lines: list[Line]) -> Iterator[Block]:
        fence: str | None = None
        pending: list[Line] = []
        index = 0

        # Front matter, only when it is the very first line.
        if lines and lines[0].text.strip() == "---":
            for offset, line in enumerate(lines[1:], start=1):
                if line.text.strip() in {"---", "..."}:
                    yield Block("front_matter", Span(lines[0].span.start, line.span.end))
                    index = offset + 1
                    break

        def flush() -> Iterator[Block]:
            if pending:
                yield Block("paragraph", Span(pending[0].span.start, pending[-1].span.end))
                pending.clear()

        while index < len(lines):
            line = lines[index]
            stripped = line.text.strip()
            index += 1

            if fence is not None:
                if stripped.startswith(fence):
                    fence = None
                continue

            opening = next((f for f in _FENCES if stripped.startswith(f)), None)
            if opening is not None:
                yield from flush()
                fence = opening
                start = line.span.start
                # Consume to the closing fence, or to the end of the document
                # if the writer forgot one.
                while index < len(lines):
                    if lines[index].text.strip().startswith(opening):
                        index += 1
                        break
                    index += 1
                end = lines[index - 1].span.end if index <= len(lines) else line.span.end
                fence = None
                yield Block("code", Span(start, end))
                continue

            if not stripped:
                yield from flush()
                continue

            if stripped.startswith("#"):
                level = len(stripped) - len(stripped.lstrip("#"))
                if 1 <= level <= 6 and stripped[level : level + 1] in {" ", ""}:
                    yield from flush()
                    yield Block("heading", line.span, level=level)
                    continue

            if stripped.startswith(">"):
                yield from flush()
                yield Block("quote", line.span)
                continue

            if self._is_list_item(stripped):
                yield from flush()
                depth = (len(line.text) - len(line.text.lstrip())) // 2
                yield Block("list_item", line.span, level=depth)
                continue

            pending.append(line)

        yield from flush()

    @staticmethod
    def _is_list_item(stripped: str) -> bool:
        if stripped[:2] in {"- ", "* ", "+ "}:
            return True
        head, _, rest = stripped.partition(". ")
        return bool(head.isdigit() and rest)

    # -- sections --------------------------------------------------------

    def _sections(self, content: str, headings: list[Block]) -> Iterator[Section]:
        if not headings:
            # A document with no headings still has one section, so that every
            # caller can assume the shape rather than special-casing it.
            if content:
                yield Section(heading="", level=0, span=Span(0, len(content)))
            return

        if headings[0].span.start > 0:
            yield Section(heading="", level=0, span=Span(0, headings[0].span.start))

        for position, block in enumerate(headings):
            level = block.level or 1
            end = len(content)
            for later in headings[position + 1 :]:
                if (later.level or 1) <= level:
                    end = later.span.start
                    break
            title = content[block.span.start : block.span.end].lstrip("#").strip()
            yield Section(
                heading=title,
                level=level,
                span=Span(block.span.start, end),
                heading_span=block.span,
            )

    # -- metadata --------------------------------------------------------

    def _metadata(self, content: str, blocks: tuple[Block, ...]) -> dict[str, str]:
        """Only what the document states about itself, as strings.

        Front matter is read as flat ``key: value`` lines and nothing more. A
        real YAML parser is a dependency, and the structure inside front matter
        has never been what makes a document findable.
        """
        metadata: dict[str, str] = {}
        for block in blocks:
            if block.kind == "heading" and block.level == 1 and "title" not in metadata:
                metadata["title"] = content[block.span.start : block.span.end].lstrip("#").strip()
            if block.kind != "front_matter":
                continue
            for line in content[block.span.start : block.span.end].splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip() and not key.startswith("-"):
                    cleaned = key.strip()
                    if cleaned not in {"---", "..."}:
                        metadata.setdefault(cleaned, value.strip())
        return metadata
