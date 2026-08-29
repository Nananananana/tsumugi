"""What a ledger holds: one entry per package, and what a stretch of them says.

The return path the draft specification was missing. Without it, every metric
about selection is computed on synthetic data, and the project's whole premise
-- that most of what gets sent is not needed -- stays an unmeasured belief.

Two rules, and they are the decision rather than implementation detail
(ADR-0011):

**It stores identifiers, offsets, scores and counts. Never text.** Not the
query, not the document, not the answer. A hash of the query is enough to group
repeats. Being textless is what lets it default to on without creating a second
sensitive artefact next to the index -- which is already a complete plaintext
copy of the corpus.

**It is derived data.** Deletable at any time, at the cost of history and
nothing else. It is never an input to a build: a ledger that fed back into
ranking would make packages depend on their own history, and reproducibility
would be gone (ADR-0003).

An entry opens when a package is built and closes when an answer is verified. A
caller who never verifies gets half a ledger -- costs without uses -- which is
still useful, and it is worth saying that it is half.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LedgerEntry", "Usage"]


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One package that was built."""

    package_id: str
    created_at: str
    query_hash: str
    unit: str
    limit: int
    estimate: int
    items: int
    omissions: int
    #: ``None`` until an answer has been verified against this package.
    verified_at: str | None = None
    cited_items: int | None = None

    @property
    def closed(self) -> bool:
        return self.verified_at is not None

    @property
    def unused_items(self) -> int | None:
        """Items sent and never cited. The number the project exists to reduce."""
        if self.cited_items is None:
            return None
        return self.items - self.cited_items


@dataclass(frozen=True, slots=True)
class Usage:
    """What a stretch of the ledger says. Counts only; no text anywhere."""

    packages: int = 0
    closed: int = 0
    items_sent: int = 0
    items_cited: int = 0
    omissions: int = 0
    budget_exhausted: int = 0

    @property
    def uncited_share(self) -> float | None:
        """Of the context that was sent *and checked*, how much went unused.

        ``None`` when nothing has been verified -- reporting 100% unused for a
        ledger nobody closed would be a lie about the tool rather than about
        the corpus.
        """
        if not self.closed or not self.items_sent:
            return None
        return 1.0 - (self.items_cited / self.items_sent)
