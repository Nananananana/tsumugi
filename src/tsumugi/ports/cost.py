"""What a piece of text will cost to send.

The unit is explicit at the call site and the estimate is honest about being
one (ADR-0006). Characters and bytes are exact. Tokens are not: the core has
no tokenizer, because a tokenizer is a vocabulary file that differs per model
family and changes when a vendor says so.

So a token cost model reports its own measured error, and a package carries it.
A token count with no stated error is a number pretending to be a measurement,
and it will mislead a caller exactly once, expensively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["CostModel", "MeasuredError"]


@dataclass(frozen=True, slots=True)
class MeasuredError:
    """How wrong an estimator was, against something that counts exactly.

    ``against`` names the real tokenizer and ``dataset`` names the text. Both
    are required: a p95 measured against one tokenizer says little about a
    model that tokenizes differently, and saying which one is honest rather
    than sufficient.
    """

    p50: float
    p95: float
    against: str
    dataset: str

    def __post_init__(self) -> None:
        if self.p50 < 0 or self.p95 < 0:
            raise ValueError("an error rate cannot be negative")
        if self.p95 < self.p50:
            raise ValueError(f"p95 ({self.p95}) is below p50 ({self.p50})")
        if not self.against or not self.dataset:
            raise ValueError("a measured error must name what it was measured against")


@runtime_checkable
class CostModel(Protocol):
    """Estimates, or counts, what text costs in one unit."""

    @property
    def name(self) -> str:
        """Versioned identifier, e.g. ``heuristic/cjk-aware@1``.

        Versioned because a change to the estimator changes every budget
        decision and therefore every ``package_id`` (ADR-0003).
        """
        ...

    @property
    def unit(self) -> str:
        """``tokens``, ``characters`` or ``bytes``."""
        ...

    @property
    def measured_error(self) -> MeasuredError | None:
        """``None`` when this model counts exactly rather than estimating.

        Required to be non-``None`` when :attr:`unit` is ``tokens`` and the
        model is a heuristic. The contract's conformance suite refuses a
        package that reports estimated tokens without it.
        """
        ...

    def cost(self, text: str) -> int:
        """What ``text`` costs. Never negative, and zero only for empty text."""
        ...
