"""Small builders shared across the suite.

Not a fixture module: these are plain functions so a test can call one twice
with different arguments in the same line and stay readable.
"""

from __future__ import annotations

from collections.abc import Mapping

from tsumugi.domain.document import Block, Document, Section
from tsumugi.domain.hashing import ContentHash


def build_document(
    source_path: str,
    content: str,
    *,
    media_type: str = "text/markdown",
    sections: tuple[Section, ...] = (),
    blocks: tuple[Block, ...] = (),
    metadata: Mapping[str, str] | None = None,
) -> Document:
    """A document whose version really is the hash of its content."""
    return Document(
        document_id=Document.identity_for(source_path),
        version=ContentHash.of(content),
        source_path=source_path,
        media_type=media_type,
        content=content,
        sections=sections,
        blocks=blocks,
        metadata=dict(metadata or {}),
    )
