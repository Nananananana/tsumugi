"""What a document is, once it has been read.

Identity is split in two, and the split is ADR-0010:

- ``document_id`` says *which file*. It is derived from the source path and
  does not change when the file is edited.
- ``version`` says *which revision*. It is the content hash, and a new one is
  created every time the file changes.

An anchor names both, so evidence taken in May still resolves after a June
edit -- as history, reported as stale, never silently re-anchored.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from .hashing import ContentHash
from .span import Span

__all__ = [
    "Block",
    "BlockKind",
    "Document",
    "DocumentId",
    "Section",
    "known_block_kinds",
    "register_block_kind",
]

DocumentId = str


# --------------------------------------------------------------------------
# Block kinds
#
# An open registry rather than an enum. A parser for a format nobody has
# written yet -- a spreadsheet row, a slide, an email header -- needs a kind
# for what it produces, and it should not have to patch this module to get
# one. This mirrors `mamori`'s ``register_type``.
# --------------------------------------------------------------------------

BlockKind = str

_registered: dict[BlockKind, str] = {
    "heading": "a section title",
    "paragraph": "running prose",
    "code": "a code block or fenced literal",
    "list_item": "one item of a list",
    "quote": "quoted text",
    "table": "a table, kept whole",
    "front_matter": "metadata at the head of a document",
    "field": "a leaf value in a structured document",
    "comment": "a comment in source code",
}


def register_block_kind(kind: BlockKind, description: str) -> None:
    """Make a new block kind usable by a parser.

    Registering an existing kind with a different description is refused. Two
    parsers quietly meaning different things by one name is the failure this
    guards against.
    """
    if not kind or not kind.replace("_", "").isalnum():
        raise ValueError(f"block kind {kind!r} must be alphanumeric with underscores")
    existing = _registered.get(kind)
    if existing is not None and existing != description:
        raise ValueError(f"block kind {kind!r} is already registered as {existing!r}")
    _registered[kind] = description


def known_block_kinds() -> Mapping[BlockKind, str]:
    """Every registered kind and what it means."""
    return dict(_registered)


# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Block:
    """One leaf run of text, with what kind of thing it is.

    A block carries a span rather than a copy of the text. There is exactly
    one authoritative copy of a document's characters -- ``Document.content``
    -- and every other structure points into it. Copies drift; offsets do not.
    """

    kind: BlockKind
    span: Span
    #: Heading depth for ``heading`` blocks, list depth for ``list_item``.
    #: ``None`` where depth is meaningless.
    level: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in _registered:
            raise ValueError(
                f"unknown block kind {self.kind!r}; register it with register_block_kind()"
            )
        if self.level is not None and self.level < 0:
            raise ValueError(f"negative level {self.level}")

    def text(self, document: Document) -> str:
        return self.span.slice(document.content)


@dataclass(frozen=True, slots=True)
class Section:
    """A heading and everything under it, up to the next heading of its level.

    Sections nest by ``level`` and their spans are the reason a search result
    can say *where* in a document a match landed. A document with no headings
    has one implicit section covering the whole of it, so callers never have
    to special-case the shape.
    """

    heading: str
    level: int
    span: Span
    #: Where the heading line itself sits, so it can be excluded from the body
    #: when a section is rendered as context.
    heading_span: Span | None = None

    def __post_init__(self) -> None:
        if self.level < 0:
            raise ValueError(f"negative heading level {self.level}")
        if self.heading_span is not None and not self.span.contains(self.heading_span):
            raise ValueError("the heading is not inside its own section")

    def body_span(self) -> Span:
        """The section without its heading line."""
        if self.heading_span is None:
            return self.span
        return Span(self.heading_span.end, self.span.end)


@dataclass(frozen=True, slots=True)
class Document:
    """A source document, exactly as it was read, plus what was made of it.

    ``content`` is the truth. ``sections`` and ``blocks`` are a reading of it
    and may be wrong without making an anchor wrong -- which is what lets a
    hand-written Markdown parser be shipped before a complete one exists
    (ADR-0001).
    """

    document_id: DocumentId
    version: ContentHash
    source_path: str
    media_type: str
    content: str
    sections: tuple[Section, ...] = ()
    blocks: tuple[Block, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.document_id:
            raise ValueError("document_id is empty")
        if not self.source_path:
            raise ValueError("source_path is empty")
        length = len(self.content)
        for section in self.sections:
            if section.span.end > length:
                raise ValueError(
                    f"section {section.heading!r} runs past the document "
                    f"({section.span.end} > {length})"
                )
        for block in self.blocks:
            if block.span.end > length:
                raise ValueError(f"a {block.kind} block runs past the document")

    @classmethod
    def identity_for(cls, source_path: str) -> DocumentId:
        """The stable id for a path.

        Derived from the path rather than from the content, so that editing a
        file produces a new *version* of the same document rather than an
        unrelated one. Hashed rather than used raw because a path is a string
        with a person's name and directory layout in it, and ids end up in
        logs, exports and packages.
        """
        return f"doc_{ContentHash.of(source_path).short(16)}"

    def section_at(self, offset: int) -> Section | None:
        """The innermost section containing ``offset``."""
        best: Section | None = None
        for section in self.sections:
            if section.span.start <= offset < section.span.end and (
                best is None or section.level > best.level
            ):
                best = section
        return best

    def blocks_in(self, span: Span) -> Iterator[Block]:
        for block in self.blocks:
            if block.span.overlaps(span):
                yield block

    def verify(self) -> None:
        """Check that ``version`` really is the hash of ``content``.

        Cheap, and worth calling whenever a document arrives from storage. A
        document whose recorded hash does not match its text makes every anchor
        into it meaningless, and finding that out at read time is far better
        than finding it out when a citation fails.
        """
        actual = ContentHash.of(self.content, self.version.algorithm)
        if actual != self.version:
            raise ValueError(
                f"{self.source_path}: stored version {self.version.short()} does not match "
                f"its content ({actual.short()})"
            )
