"""Parsers, and the registry that picks one.

Adding a format is registering a parser. Nothing else in the library changes,
and the parser does not have to import the port it satisfies -- it only has to
have the right shape (``kiseki``'s ADR-0004).

    from tsumugi.infrastructure.parsers import register_parser

    class OrgModeParser:
        name = "orgmode@1"
        suffixes = (".org",)
        media_type = "text/x-org"
        def parse(self, content): ...

    register_parser(OrgModeParser())

A suffix belongs to one parser. Registering over an existing claim is allowed
and returns the parser that was displaced, so an override is deliberate and
reversible rather than a silent last-write-wins.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...errors import ConfigurationError
from ...ports.parser import ParsedDocument, Parser
from .markdown import MarkdownParser
from .plaintext import PlainTextParser, SourceCodeParser
from .structured import JsonParser

__all__ = [
    "JsonParser",
    "MarkdownParser",
    "ParsedDocument",
    "Parser",
    "PlainTextParser",
    "SourceCodeParser",
    "known_parsers",
    "parser_for",
    "register_parser",
    "registered_suffixes",
]

_by_suffix: dict[str, Parser] = {}


def register_parser(parser: Parser, *, replace: bool = False) -> tuple[Parser, ...]:
    """Claim this parser's suffixes. Returns whatever it displaced.

    Refuses to take a suffix another parser already holds unless ``replace`` is
    passed. Two parsers silently fighting over ``.md`` would produce documents
    whose structure depends on import order.
    """
    if not parser.suffixes:
        raise ConfigurationError(f"parser {parser.name!r} claims no suffixes")

    displaced: list[Parser] = []
    for suffix in parser.suffixes:
        if not suffix.startswith("."):
            raise ConfigurationError(f"suffix {suffix!r} must start with a dot")
        held = _by_suffix.get(suffix.lower())
        if held is not None and held.name != parser.name:
            if not replace:
                raise ConfigurationError(
                    f"{suffix!r} is already claimed by {held.name!r}; "
                    f"pass replace=True to override it deliberately"
                )
            displaced.append(held)

    for suffix in parser.suffixes:
        _by_suffix[suffix.lower()] = parser
    return tuple(dict.fromkeys(displaced))


def parser_for(source_path: str) -> Parser | None:
    """The parser claiming this path's suffix, or ``None``.

    ``None`` rather than a raise: a corpus folder is full of files nobody meant
    to index, and refusing to walk a directory because it holds a ``.png`` is
    not helpful. The caller reports what it skipped (ADR-0005).
    """
    _, dot, suffix = source_path.rpartition(".")
    if not dot:
        return None
    return _by_suffix.get(f".{suffix.lower()}")


def known_parsers() -> tuple[Parser, ...]:
    """Every registered parser, once each, in a stable order."""
    return tuple(sorted({p.name: p for p in _by_suffix.values()}.values(), key=lambda p: p.name))


def registered_suffixes() -> Mapping[str, str]:
    """Every claimed suffix and the parser holding it."""
    return {suffix: parser.name for suffix, parser in sorted(_by_suffix.items())}


for _builtin in (MarkdownParser(), PlainTextParser(), SourceCodeParser(), JsonParser()):
    register_parser(_builtin)
