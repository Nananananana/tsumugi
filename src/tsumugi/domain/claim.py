"""What a model said, and whether its citations are really there.

Four outcomes, and keeping them apart is the substance of the module.

``supported``     every citation resolved to text in the package
``unsupported``   a citation did not resolve -- the quotation is not there
``uncited``       the claim carries no citation at all
``unverifiable``  the citation cannot be checked, and tsumugi says so

``uncited`` is separate from ``unsupported`` on purpose: a model that cites
nothing has failed differently from one that cites something that does not
exist, and collapsing the two hides which of the two problems you have.

``unverifiable`` exists because of ADR-0009. When a package passed through an
irreversible redaction -- a masked or blocked value -- the citation *cannot* be
resolved, and calling that ``unsupported`` would report an honest citation as a
fabricated one. Unknown and false are different, and a verifier that conflates
them teaches its user to ignore the signal.

**And the thing this module does not do:** a supported claim is not a true
claim. It means the quoted text exists where the model said it does. A model
can quote your notes perfectly and reason from them badly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .anchor import Anchor
from .hashing import ContentHash
from .matching import find_all
from .selection import ContextItem
from .span import Span

__all__ = ["Citation", "Claim", "Support", "VerificationReport", "verify_claims"]


class Support(Enum):
    """What became of a claim when its citations were checked."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNCITED = "uncited"
    UNVERIFIABLE = "unverifiable"

    @property
    def is_problem(self) -> bool:
        """Everything except ``supported``. Not everything is a *lie*."""
        return self is not Support.SUPPORTED


@dataclass(frozen=True, slots=True)
class Located:
    """One place a quotation was found, anchored back to the real document."""

    item_id: str
    anchor: Anchor
    source_path: str = ""
    section: str = ""

    def describe(self) -> str:
        where = self.source_path or self.anchor.document_id
        if self.section:
            where += f" ({self.section})"
        return f"{where}[{self.anchor.span.start}:{self.anchor.span.end}]"


@dataclass(frozen=True, slots=True)
class Citation:
    """A quotation the model offered, and where it turned out to be."""

    quotation: str
    #: Every place it resolved. Empty means it did not.
    locations: tuple[Located, ...] = ()

    @property
    def resolved(self) -> bool:
        return bool(self.locations)

    @property
    def ambiguous(self) -> bool:
        """Resolved in more than one place. Information, not an error."""
        return len(self.locations) > 1


@dataclass(frozen=True, slots=True)
class Claim:
    """One statement from the answer, and the citations behind it."""

    text: str
    citations: tuple[Citation, ...] = ()
    #: Set when the claim could not be checked at all, with the reason.
    unverifiable_because: str = ""

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("a claim with no text asserts nothing")

    @property
    def support(self) -> Support:
        if self.unverifiable_because:
            return Support.UNVERIFIABLE
        if not self.citations:
            return Support.UNCITED
        if all(citation.resolved for citation in self.citations):
            return Support.SUPPORTED
        return Support.UNSUPPORTED

    @property
    def unresolved(self) -> tuple[Citation, ...]:
        return tuple(c for c in self.citations if not c.resolved)


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Every claim in an answer, classified."""

    claims: tuple[Claim, ...]
    #: The package these were checked against.
    package_id: str = ""
    counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def of(cls, claims: Sequence[Claim], package_id: str = "") -> VerificationReport:
        counts = {support.value: 0 for support in Support}
        for claim in claims:
            counts[claim.support.value] += 1
        return cls(claims=tuple(claims), package_id=package_id, counts=counts)

    @property
    def clean(self) -> bool:
        """At least one claim, and every claim supported.

        **The first half is not pedantry.** ``all()`` over nothing is true, so
        an answer of ``{"claims": []}`` used to verify clean, exit 0 from
        `tsumugi verify`, and report as trustworthy from `ask`. A model that
        asserts nothing had passed the check -- which is the fail-open this
        library says it does not have, in the one place it most matters.

        And it is reachable: a model told to answer in JSON and unable to
        answer the question produces exactly that shape. Use
        :attr:`asserts_nothing` to tell "nothing was checked" from "something
        failed"; they are different, and neither is success.
        """
        return bool(self.claims) and all(
            claim.support is Support.SUPPORTED for claim in self.claims
        )

    @property
    def asserts_nothing(self) -> bool:
        """No claims at all. Not clean, and not a failure either."""
        return not self.claims

    def with_support(self, support: Support) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if c.support is support)

    def summary(self) -> str:
        return ", ".join(f"{n} {name}" for name, n in self.counts.items() if n)

    def to_dict(self) -> dict[str, Any]:
        """The report as plain data, the same shape everywhere it is emitted.

        Here rather than at the edge that happens to print it. A verification
        report is a document -- it is what a reader keeps to show that an
        answer was checked -- and a shape defined inside one CLI branch is a
        shape the next consumer writes again, slightly differently.

        Offsets are reported and quotations are not resolved into text: this
        says where the model's words were found, and the package it was checked
        against still holds what was sent.
        """
        return {
            "package_id": self.package_id,
            "counts": dict(self.counts),
            "claims": [
                {
                    "text": claim.text,
                    "support": claim.support.value,
                    "unverifiable_because": claim.unverifiable_because,
                    "citations": [
                        {
                            "quotation": citation.quotation,
                            # Derivable from ``locations``, and stated anyway.
                            # A consumer reading this to decide whether to
                            # trust a sentence should not have to infer the
                            # answer from the length of a list.
                            "resolved": citation.resolved,
                            "locations": [
                                {
                                    "item_id": location.item_id,
                                    "source_path": location.source_path,
                                    "section": location.section,
                                    "start": location.anchor.span.start,
                                    "end": location.anchor.span.end,
                                }
                                for location in citation.locations
                            ],
                        }
                        for citation in claim.citations
                    ],
                }
                for claim in self.claims
            ],
        }


def verify_claims(
    claims: Sequence[tuple[str, Sequence[str]]],
    items: Sequence[ContextItem],
    *,
    package_id: str = "",
    unverifiable_because: str = "",
) -> VerificationReport:
    """Resolve every citation against the text that was actually sent.

    ``claims`` is ``(claim text, quotations)`` pairs -- deliberately plain, so
    that parsing a model's output is somebody else's problem and this stays a
    pure function with no format opinion in it.

    ``unverifiable_because``, when set, classifies every claim as
    ``unverifiable`` without attempting resolution. That is the ADR-0009 path:
    a package redacted irreversibly cannot be checked, and reporting each
    honest citation as ``unsupported`` would be worse than saying so.
    """
    verified: list[Claim] = []

    for text, quotations in claims:
        if unverifiable_because:
            verified.append(Claim(text=text, unverifiable_because=unverifiable_because))
            continue

        citations: list[Citation] = []
        for quotation in quotations:
            citations.append(Citation(quotation=quotation, locations=_locate(quotation, items)))
        verified.append(Claim(text=text, citations=tuple(citations)))

    return VerificationReport.of(verified, package_id=package_id)


def _locate(quotation: str, items: Sequence[ContextItem]) -> tuple[Located, ...]:
    """Every place ``quotation`` occurs across the package's items.

    Ordered by item and then by position, so a report over the same package is
    the same report every time (ADR-0003).
    """
    found: list[Located] = []
    for item in items:
        for span in find_all(quotation, item.text):
            # The item's anchor is into the document; the span is into the
            # item. Adding them gives an anchor a `trace` can follow all the
            # way back to a line in a file.
            start = item.anchor.span.start + span.start
            found.append(
                Located(
                    item_id=item.item_id,
                    anchor=Anchor(
                        document_id=item.anchor.document_id,
                        span=Span(start, start + len(span)),
                        text_hash=ContentHash.of(
                            span.slice(item.text), item.anchor.text_hash.algorithm
                        ),
                        version=item.anchor.version,
                    ),
                    source_path=item.source_path,
                    section=item.section,
                )
            )
    return tuple(found)
