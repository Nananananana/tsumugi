"""Every error this library raises.

One base class, so a caller can catch ``TsumugiError`` and mean it, and one
subclass per thing that can go wrong in a way the caller could act on.

Errors carry identifiers and positions, never document text. A traceback is a
place text ends up in logs, and the index holds a person's notes.
"""

from __future__ import annotations

__all__ = [
    "ConfigurationError",
    "IngestionError",
    "StorageError",
    "TsumugiError",
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
