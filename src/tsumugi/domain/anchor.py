"""Where a piece of text came from, and whether it is still there.

An anchor is the whole of this library's claim. Everything else -- parsing,
indexing, ranking, budgeting -- exists so that a span of text can be handed to
a model and later traced back to the document it was taken from.

The invariant, asserted by property tests over generated documents:

    document.content[anchor.span.start : anchor.span.end] == anchor.text
    ContentHash.of(anchor.text) == anchor.text_hash

An anchor resolves, is stale, or is unresolvable. The middle case is the one
that matters and the one that is easy to leave out: evidence taken before an
edit was true when it was taken. Reporting it as false, or silently moving it
to wherever the text went, are both worse than saying "this was true in the
version indexed in May" (ADR-0010).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .document import Document, DocumentId
from .hashing import ContentHash
from .span import Span

__all__ = ["Anchor", "Resolution", "ResolutionStatus", "resolve"]


class ResolutionStatus(Enum):
    """What became of an anchor when it was checked against a document."""

    #: The text is where the anchor says, in the version the anchor names.
    RESOLVED = "resolved"
    #: The text is where the anchor says, but the document has moved on. The
    #: evidence is historical, not wrong.
    STALE = "stale"
    #: The offsets do not hold, or the text there is not what was recorded.
    UNRESOLVABLE = "unresolvable"


@dataclass(frozen=True, slots=True)
class Anchor:
    """A span of text, and enough to prove it was there.

    Both identifiers are required. ``document_id`` says which file; ``version``
    says which revision of it. Without the version an anchor into an edited
    document silently resolves against different text, which is the failure
    this type exists to make impossible.
    """

    document_id: DocumentId
    span: Span
    text_hash: ContentHash
    #: The content hash of the whole document as it was when the anchor was
    #: taken.
    version: ContentHash

    def __post_init__(self) -> None:
        if not self.document_id:
            raise ValueError("an anchor with no document_id anchors nothing")

    @classmethod
    def into(cls, document: Document, span: Span) -> Anchor:
        """Anchor a span of ``document``, computing the hashes.

        The only supported way to build one. Constructing an ``Anchor`` by hand
        with a hash that does not match the text is possible and is a bug; this
        constructor makes the correct thing the easy thing.
        """
        text = span.slice(document.content)
        return cls(
            document_id=document.document_id,
            span=span,
            text_hash=ContentHash.of(text, document.version.algorithm),
            version=document.version,
        )


@dataclass(frozen=True, slots=True)
class Resolution:
    """The answer to "is this evidence still good?"."""

    status: ResolutionStatus
    anchor: Anchor
    #: The text found at the anchor, when there was any. ``None`` when the
    #: anchor could not be resolved at all.
    text: str | None = None
    #: Why, in words, when the answer is not ``RESOLVED``.
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True only for :attr:`ResolutionStatus.RESOLVED`.

        Stale is deliberately not ok. A caller that wants historical evidence
        asks for it by name, so that treating old evidence as current is
        always a visible decision.
        """
        return self.status is ResolutionStatus.RESOLVED


def resolve(anchor: Anchor, document: Document) -> Resolution:
    """Check ``anchor`` against ``document``.

    ``document`` must be the revision the anchor names, or the newest one for
    the same file. Passing an unrelated document is a programming error and
    raises rather than returning ``UNRESOLVABLE`` -- the two are different
    problems and only one of them is about the data.
    """
    if anchor.document_id != document.document_id:
        raise ValueError(
            f"anchor points at {anchor.document_id} and was checked against {document.document_id}"
        )

    if anchor.span.end > len(document.content):
        return Resolution(
            status=ResolutionStatus.UNRESOLVABLE,
            anchor=anchor,
            detail=(
                f"the anchor ends at {anchor.span.end} and the document is "
                f"{len(document.content)} characters"
            ),
        )

    found = anchor.span.slice(document.content)
    if ContentHash.of(found, anchor.text_hash.algorithm) != anchor.text_hash:
        if anchor.version != document.version:
            return Resolution(
                status=ResolutionStatus.STALE,
                anchor=anchor,
                detail=(
                    f"the document has changed since this was anchored "
                    f"(indexed {anchor.version.short()}, now {document.version.short()})"
                ),
            )
        return Resolution(
            status=ResolutionStatus.UNRESOLVABLE,
            anchor=anchor,
            detail="the text at these offsets is not what was recorded",
        )

    if anchor.version != document.version:
        # The text survived the edit intact at the same offsets. That is luck
        # rather than a guarantee, so it is still reported as stale: the
        # evidence is good, and the caller should know it came from a version
        # that no longer exists.
        return Resolution(
            status=ResolutionStatus.STALE,
            anchor=anchor,
            text=found,
            detail=(
                f"the text is unchanged, but the document is not "
                f"(indexed {anchor.version.short()}, now {document.version.short()})"
            ),
        )

    return Resolution(status=ResolutionStatus.RESOLVED, anchor=anchor, text=found)
