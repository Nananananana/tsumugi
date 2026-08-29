"""Reading a folder into the store and the index.

Reports everything: what was added, what was already held, what changed, what
was skipped and why. A run that says "indexed 412 documents" and nothing else
cannot be told apart from one that quietly excluded half the folder.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.document import Document
from ..domain.hashing import ContentHash
from ..errors import IngestionError
from ..ports.index import Index
from ..ports.parser import Parser
from ..ports.store import DocumentStore

__all__ = ["IngestReport", "Ingested", "ingest_paths"]


@dataclass(frozen=True, slots=True)
class Ingested:
    """One document that was read."""

    source_path: str
    document_id: str
    version: ContentHash
    #: ``True`` when this revision was not already held.
    is_new: bool
    #: ``True`` when the document existed and its content changed.
    is_revision: bool


@dataclass(slots=True)
class IngestReport:
    """What a run did, in enough detail to trust it."""

    added: list[Ingested] = field(default_factory=list)
    revised: list[Ingested] = field(default_factory=list)
    unchanged: list[Ingested] = field(default_factory=list)
    #: ``(path, reason)`` for every file that was seen and not read.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: ``(path, error)`` for every file that was read and could not be parsed.
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def read(self) -> int:
        return len(self.added) + len(self.revised) + len(self.unchanged)

    def summary(self) -> str:
        return (
            f"{len(self.added)} new, {len(self.revised)} revised, "
            f"{len(self.unchanged)} unchanged, {len(self.skipped)} skipped, "
            f"{len(self.failed)} failed"
        )


def ingest_paths(
    paths: Sequence[Path],
    *,
    root: Path,
    store: DocumentStore,
    index: Index,
    parser_for: Callable[[str], Parser | None],
    report: IngestReport | None = None,
) -> IngestReport:
    """Read each path, store it, index it.

    ``root`` is what source paths are recorded relative to, so that an index
    built on one machine describes the same corpus on another -- and so that a
    document id, which is derived from the path, does not change when the
    folder moves.
    """
    outcome = report if report is not None else IngestReport()

    for path in paths:
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            relative = path.as_posix()

        parser = parser_for(relative)
        if parser is None:
            outcome.skipped.append((relative, "no parser claims this suffix"))
            continue

        try:
            content = _read(path)
        except IngestionError as error:
            outcome.failed.append((relative, str(error)))
            continue

        try:
            parsed = parser.parse(content)
        except (ValueError, TypeError) as error:
            # A parser raises rather than returning nothing, so this is a real
            # failure and is reported as one rather than as an empty document.
            outcome.failed.append((relative, f"{parser.name}: {error}"))
            continue

        document = Document(
            document_id=Document.identity_for(relative),
            version=ContentHash.of(content),
            source_path=relative,
            media_type=parser.media_type,
            content=content,
            sections=parsed.sections,
            blocks=parsed.blocks,
            metadata=dict(parsed.metadata),
        )

        had_before = store.current_version(document.document_id)
        is_new = store.put(document, corpus_root=str(root.resolve()))
        entry = Ingested(
            source_path=relative,
            document_id=document.document_id,
            version=document.version,
            is_new=is_new,
            is_revision=is_new and had_before is not None,
        )

        if not is_new:
            outcome.unchanged.append(entry)
            continue

        index.add(document)
        (outcome.revised if entry.is_revision else outcome.added).append(entry)

    return outcome


def _read(path: Path) -> str:
    """Read a file as text, or say precisely why not.

    A byte order mark is stripped: it is an encoding artefact rather than a
    character in the document, and leaving it in shifts every offset in the
    file by one.
    """
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise IngestionError(f"cannot read: {error}") from error

    if b"\x00" in raw[:8192]:
        raise IngestionError("looks like a binary file")

    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise IngestionError("not valid UTF-8")
