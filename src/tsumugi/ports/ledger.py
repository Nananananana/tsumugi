"""The record of what was sent, and afterwards, what was used.

A port rather than a concrete class because the ledger is the one piece of
derived data a user might reasonably want to keep somewhere else -- or not to
keep at all. A null implementation that records nothing satisfies this, and
that is the point.

Two rules travel with the contract rather than with any implementation
(ADR-0011):

**Identifiers, offsets, scores and counts. Never text.** Not the query, not the
document, not the answer. A hash of the query is enough to group repeats. Being
textless is what lets it default to on without creating a second sensitive
artefact beside the index, which is already a complete plaintext copy of the
corpus.

**Derived data.** Deletable at any time, at the cost of history and nothing
else. Never an input to a build: a ledger that fed back into ranking would make
packages depend on their own history, and reproducibility would be gone
(ADR-0003).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.claim import VerificationReport
from ..domain.package import ContextPackage
from ..domain.usage import LedgerEntry, Usage

__all__ = ["LedgerStore"]


@runtime_checkable
class LedgerStore(Protocol):
    """Opens an entry when a package is built, closes it when one is verified."""

    def open(self, package: ContextPackage, *, at: str | None = None) -> LedgerEntry:
        """Record that a package was built.

        **Idempotent on ``package_id``.** A package id is a hash of its inputs,
        so building the same package twice is the same event, and counting it
        twice would overstate what was sent.
        """
        ...

    def close(self, report: VerificationReport, *, at: str | None = None) -> bool:
        """Record which items an answer actually cited.

        Returns ``False`` when the package was never opened. Verifying against
        a package built elsewhere is legitimate and is not an error.
        """
        ...

    def entries(self, *, since: str | None = None, limit: int = 100) -> Sequence[LedgerEntry]: ...

    def usage(self, *, since: str | None = None) -> Usage:
        """What a stretch of the ledger says. Counts only."""
        ...

    def forget(self) -> int:
        """Delete everything. Costs history and nothing else."""
        ...
