"""Going backwards: from a quotation to the document it came from.

The command that makes the rest believable. Everything else asks the library to
choose; this asks it to account for a choice, and an evidence system that
cannot go backwards is a search engine with better vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.anchor import Anchor, Resolution, ResolutionStatus, resolve
from ..domain.document import Section
from ..domain.span import Span
from ..ports.store import DocumentStore

__all__ = ["Trace", "trace_anchor", "trace_quotation"]


@dataclass(frozen=True, slots=True)
class Trace:
    """Where a piece of text came from, and whether it is still true there."""

    resolution: Resolution
    source_path: str
    #: The innermost section containing the anchor, when there is one.
    section: str = ""
    #: 1-based line number of the anchor's start, for a human reading a file.
    line: int = 0
    #: The current version, when it differs from the anchored one.
    current_version: str = ""

    @property
    def status(self) -> ResolutionStatus:
        return self.resolution.status

    def describe(self) -> str:
        """One line, for a terminal."""
        where = f"{self.source_path}:{self.line}"
        if self.section:
            where += f" ({self.section})"
        if self.status is ResolutionStatus.RESOLVED:
            return f"resolved  {where}"
        return f"{self.status.value:<9} {where} -- {self.resolution.detail}"


def trace_anchor(anchor: Anchor, store: DocumentStore) -> Trace | None:
    """Resolve ``anchor`` against whatever the store holds.

    The anchored revision is tried first. Falling back to the current one is
    what turns "the file changed" into a *stale* answer with a detail, rather
    than into nothing at all.
    """
    document = store.get(anchor.document_id, anchor.version)
    if document is None:
        document = store.get(anchor.document_id)
    if document is None:
        return None

    resolution = resolve(anchor, document)
    current = store.current_version(anchor.document_id)
    return Trace(
        resolution=resolution,
        source_path=document.source_path,
        section=_section_name(document.section_at(anchor.span.start)),
        line=document.content.count("\n", 0, anchor.span.start) + 1,
        current_version=str(current) if current and current != anchor.version else "",
    )


def trace_quotation(quotation: str, store: DocumentStore, *, limit: int = 20) -> list[Trace]:
    """Every place ``quotation`` occurs, exactly, in the corpus.

    Exact, with no fuzzy matching. A quotation that does not appear is not
    "nearly there": reporting it as found would be the failure this library
    exists to prevent (ADR-0004).

    More than one match is not an error. Ambiguity is information, and all of
    it is returned.
    """
    if not quotation:
        return []

    found: list[Trace] = []
    for document in store.all_current():
        start = document.content.find(quotation)
        while start != -1 and len(found) < limit:
            anchor = Anchor.into(document, Span(start, start + len(quotation)))
            found.append(
                Trace(
                    resolution=resolve(anchor, document),
                    source_path=document.source_path,
                    section=_section_name(document.section_at(start)),
                    line=document.content.count("\n", 0, start) + 1,
                )
            )
            start = document.content.find(quotation, start + 1)
        if len(found) >= limit:
            break
    return found


def _section_name(section: Section | None) -> str:
    return section.heading if section is not None else ""
