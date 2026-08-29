"""Whether the file a document was read from still says what it said.

The index holds the text it anchored, so an anchor checked against the *store*
always resolves — by construction. That is the point of
[ADR 0010](../../docs/adr/0010-the-index-stores-the-text.md) and it is also a
trap: staleness is invisible from inside the store, and a package built without
looking at the disk will offer a passage from a file that was rewritten last
week as though it were current.

Finding that out costs I/O, which is why it is a port and why it is optional. A
caller with no corpus to hand — verifying a package built elsewhere, running
over an index whose files are on an unmounted drive — gets a package with no
staleness reported rather than an error, and the package says which providers
it used.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.document import Document

__all__ = ["FreshnessCheck"]


@runtime_checkable
class FreshnessCheck(Protocol):
    """Compares a stored document against the file it came from."""

    @property
    def name(self) -> str:
        """Recorded in the package's providers, so a reader can tell a package
        that checked from one that could not."""
        ...

    def is_current(self, document: Document) -> bool:
        """``True`` when the file still hashes to the stored version.

        ``True`` is also the answer when the file cannot be read at all. A
        missing file is a different problem from a changed one, `tsumugi
        doctor` is where it belongs, and guessing "stale" here would report
        every document on an unmounted drive as historical.
        """
        ...
