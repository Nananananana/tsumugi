"""How much may be sent, in a unit the caller chose on purpose.

The draft specification said ``max_tokens=8000`` and left the unit implicit.
That is a promise the core cannot keep: a tokenizer is a vocabulary file that
differs per model family and changes when a vendor says so, and
[ADR 0001] keeps it out of the library.

So the unit is named at the call site:

    Budget.tokens(8000)        # estimated, and the estimate states its error
    Budget.characters(20000)   # exact
    Budget.bytes(65536)        # exact

Characters and bytes are counted. Tokens are estimated, and an estimate that
does not say how wrong it is will mislead a caller exactly once, expensively --
so a token budget refuses to be spent by a model that cannot state its measured
error (ADR-0006).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["Budget", "Unit"]


class Unit(Enum):
    """What a budget counts."""

    #: Estimated. Requires a cost model with a measured error.
    TOKENS = "tokens"
    #: Exact. Python string length, matching every offset in the library.
    CHARACTERS = "characters"
    #: Exact. UTF-8.
    BYTES = "bytes"

    @property
    def is_exact(self) -> bool:
        return self is not Unit.TOKENS


@dataclass(frozen=True, slots=True)
class Budget:
    """A ceiling, and the unit it is measured in."""

    unit: Unit
    limit: int

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError(f"a budget of {self.limit} would admit nothing")

    @classmethod
    def tokens(cls, limit: int) -> Budget:
        return cls(Unit.TOKENS, limit)

    @classmethod
    def characters(cls, limit: int) -> Budget:
        return cls(Unit.CHARACTERS, limit)

    @classmethod
    def bytes(cls, limit: int) -> Budget:
        return cls(Unit.BYTES, limit)

    @classmethod
    def parse(cls, text: str) -> Budget:
        """Read ``tokens:8000``, for a command line.

        A bare number is refused. The whole point of this type is that the unit
        is a decision, and defaulting it would put the decision back where the
        draft had it.
        """
        name, separator, amount = text.partition(":")
        if not separator:
            raise ValueError(
                f"a budget needs its unit: {text!r} should be 'tokens:{text}', "
                f"'characters:{text}' or 'bytes:{text}'"
            )
        try:
            unit = Unit(name.strip().lower())
        except ValueError:
            raise ValueError(
                f"unknown budget unit {name.strip()!r}; "
                f"expected one of {', '.join(u.value for u in Unit)}"
            ) from None
        try:
            limit = int(amount)
        except ValueError:
            raise ValueError(f"a budget limit must be a number, not {amount!r}") from None
        return cls(unit, limit)

    def __str__(self) -> str:
        return f"{self.unit.value}:{self.limit}"

    def fits(self, cost: int) -> bool:
        return cost <= self.limit

    def remaining(self, spent: int) -> int:
        """What is left, never below zero."""
        return max(0, self.limit - spent)
