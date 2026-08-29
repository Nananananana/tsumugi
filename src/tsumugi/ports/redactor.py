"""Something that takes values out of a package and can put them back.

tsumugi does not know what a placeholder looks like, does not parse one, and
does not hold a mapping. It asks the port to restore and works with what comes
back. Holding the mapping would put every real value into the index for no
benefit -- which is exactly what the index is not for.

`mamori` is the implementation this was shaped for, and the reason ADR-0009
exists: an anchor is taken against the original text, a redactor rewrites the
rendered package, and a model quotes what it was given. Verify without
restoring first and every honest citation reports as unsupported.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["Redactor"]


@runtime_checkable
class Redactor(Protocol):
    """Removes sensitive values on the way out and returns them on the way in."""

    @property
    def name(self) -> str:
        """Versioned identifier, recorded in the package's provenance."""
        ...

    @property
    def scope(self) -> str:
        """The conversation this redactor's mapping belongs to.

        Stored in the package as an identifier only. It is what makes a
        verifier able to say *which* session would be needed to check a
        protected package it cannot check.
        """
        ...

    def protect(self, text: str) -> str:
        """Replace sensitive values. **Raises on failure** -- returning the
        text unchanged would be indistinguishable from finding nothing in it,
        which is a fail-open bug."""
        ...

    def restore(self, text: str) -> str:
        """Put the real values back, for text that came from the model."""
        ...
