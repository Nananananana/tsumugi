"""Every error this library raises.

One base class, so a caller can catch ``TsumugiError`` and mean it, and one
subclass per thing that can go wrong in a way the caller could act on.

Errors carry identifiers and positions, never document text. A traceback is a
place text ends up in logs, and the index holds a person's notes.
"""

from __future__ import annotations

__all__ = [
    "AnchorError",
    "ConfigurationError",
    "IngestionError",
    "StaleAnchorError",
    "StorageError",
    "TsumugiError",
    "UnresolvableAnchorError",
]


class TsumugiError(Exception):
    """Base for everything raised by tsumugi."""


class ConfigurationError(TsumugiError):
    """A setting is missing, unknown or contradictory.

    Unknown keys are an error rather than being ignored: a typo in a budget or
    an ignore rule that silently does nothing is the worst available outcome.
    """


class IngestionError(TsumugiError):
    """A document could not be read or parsed."""


class StorageError(TsumugiError):
    """The store could not answer, or would have to lie to."""


class AnchorError(TsumugiError):
    """Base for the two ways an anchor fails."""


class UnresolvableAnchorError(AnchorError):
    """The anchor does not point at text that exists.

    Its offsets fall outside the document, or the text at those offsets does
    not hash to what the anchor recorded. Either way the anchor is wrong, not
    merely old -- see :class:`StaleAnchorError` for old.
    """


class StaleAnchorError(AnchorError):
    """The anchor was true when it was taken, and the document has changed.

    Distinct from :class:`UnresolvableAnchorError` because the distinction is
    the whole of ADR-0010: evidence that was true in May is historical, not
    false, and silently re-anchoring it against edited text would be the single
    most damaging thing this library could do.
    """
