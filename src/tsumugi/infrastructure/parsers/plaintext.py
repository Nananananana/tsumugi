"""Plain text, and source code.

Neither reports much structure, and that is correct rather than lazy. A
paragraph break is real information in a text file; an indentation-derived
"section" in a Python module would be a guess, and a guess in the structure
layer costs ranking quality with no way to notice.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from ...domain.document import Block, Section
from ...domain.span import Span
from ...ports.parser import ParsedDocument
from .lines import Line, iter_lines

__all__ = ["PlainTextParser", "SourceCodeParser"]


def _paragraphs(lines: Sequence[Line]) -> Iterator[Block]:
    """Runs of non-blank lines."""
    pending: list[Line] = []
    for line in lines:
        if line.text.strip():
            pending.append(line)
            continue
        if pending:
            yield Block("paragraph", Span(pending[0].span.start, pending[-1].span.end))
            pending = []
    if pending:
        yield Block("paragraph", Span(pending[0].span.start, pending[-1].span.end))


class PlainTextParser:
    """Satisfies :class:`~tsumugi.ports.parser.Parser`."""

    name = "plaintext@1"
    suffixes = (".txt", ".text", ".log", ".rst", ".org")
    media_type = "text/plain"

    def parse(self, content: str) -> ParsedDocument:
        blocks = tuple(_paragraphs(list(iter_lines(content))))
        sections = (Section(heading="", level=0, span=Span(0, len(content))),) if content else ()
        return ParsedDocument(sections=sections, blocks=blocks)


class SourceCodeParser:
    """Source files, kept whole.

    The whole file is one ``code`` block. Splitting by function would need a
    parser per language, and the thing that makes code findable in a personal
    corpus is the identifiers in it, which the index already has.
    """

    name = "source@1"
    suffixes = (
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".rb",
        ".sh",
        ".sql",
        ".toml",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
    )
    media_type = "text/x-source"

    def parse(self, content: str) -> ParsedDocument:
        if not content:
            return ParsedDocument()
        span = Span(0, len(content))
        return ParsedDocument(
            sections=(Section(heading="", level=0, span=span),),
            blocks=(Block("code", span),),
        )
