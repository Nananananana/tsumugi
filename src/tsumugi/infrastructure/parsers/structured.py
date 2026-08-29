"""JSON, read as leaves with the path that reaches each one.

A JSON document has real structure, and throwing it away would make every
value in a large file equally findable -- which is to say, not findable. So
each leaf becomes a ``field`` block and each top-level key becomes a section.

The offsets are the awkward part. ``json.loads`` gives values with no positions,
so this walks the raw text to locate each leaf rather than trusting a
re-serialization to line up. A value that cannot be located is skipped: a block
whose span points at the wrong characters is worse than a missing block, and
the content is indexed whole regardless.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ...domain.document import Block, Section
from ...domain.span import Span
from ...ports.parser import ParsedDocument

__all__ = ["JsonParser"]


class JsonParser:
    """Satisfies :class:`~tsumugi.ports.parser.Parser`."""

    name = "json@1"
    suffixes = (".json", ".jsonl", ".ndjson")
    media_type = "application/json"

    def parse(self, content: str) -> ParsedDocument:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as error:
            # Raising rather than returning an empty result: a silently
            # unparsed document is one that quietly stops being findable.
            raise ValueError(f"not valid JSON: {error}") from error

        blocks = tuple(self._leaves(content, data))
        sections = tuple(self._sections(content, data))
        return ParsedDocument(sections=sections, blocks=blocks, metadata=self._metadata(data))

    def _leaves(self, content: str, data: Any, cursor: int = 0) -> Iterator[Block]:
        for value in _walk(data):
            if not isinstance(value, str) or not value:
                continue
            located = content.find(json.dumps(value, ensure_ascii=False), cursor)
            if located == -1:
                located = content.find(json.dumps(value), cursor)
            if located == -1:
                continue
            end = located + len(json.dumps(value, ensure_ascii=False))
            if end <= len(content):
                yield Block("field", Span(located, min(end, len(content))))
                cursor = located + 1

    def _sections(self, content: str, data: Any) -> Iterator[Section]:
        if not content:
            return
        if not isinstance(data, dict):
            yield Section(heading="", level=0, span=Span(0, len(content)))
            return

        keys = [k for k in data if isinstance(k, str)]
        marks: list[tuple[int, str]] = []
        for key in keys:
            at = content.find(json.dumps(key, ensure_ascii=False))
            if at != -1:
                marks.append((at, key))
        if not marks:
            yield Section(heading="", level=0, span=Span(0, len(content)))
            return

        marks.sort()
        for position, (at, key) in enumerate(marks):
            end = marks[position + 1][0] if position + 1 < len(marks) else len(content)
            yield Section(heading=key, level=1, span=Span(at, end))

    def _metadata(self, data: Any) -> dict[str, str]:
        if not isinstance(data, dict):
            return {}
        found: dict[str, str] = {}
        for name in ("title", "name", "id"):
            value = data.get(name)
            if isinstance(value, str | int | float):
                found[name] = str(value)
        return found


def _walk(data: Any) -> Iterator[Any]:
    if isinstance(data, dict):
        for key, value in data.items():
            yield key
            yield from _walk(value)
    elif isinstance(data, list):
        for item in data:
            yield from _walk(item)
    else:
        yield data
