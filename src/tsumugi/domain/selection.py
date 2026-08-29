"""One piece of context that will be sent, and why it was chosen.

Two things travel with every item and neither is decoration.

**The layer.** `kiseki` separates facts, measures and interpretations, and that
separation survives the crossing into a package: an interpretation stays an
interpretation, with its own confidence and evidence. An interest that says
"you seem to care about ceramics, confidence 0.7, from these eleven
photographs" must still say that inside a package. Flattening it into a fact
because it crossed a library boundary would be laundering.

**The signals.** A ranker that cannot say why an item scored what it did is a
ranker nobody can debug, and a score with no explanation is the part of a
retrieval system users are right not to trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .anchor import Anchor

__all__ = ["ContextItem", "ItemProvenance", "Layer", "SelectionTrace"]


class Layer(Enum):
    """What kind of statement a piece of context is.

    Borrowed from `kiseki`, where it is enforced by construction: an
    interpretation without evidence cannot be built.
    """

    #: An observation. Text that exists in a document.
    FACT = "fact"
    #: A count, a duration, a share. True and silent about meaning.
    MEASURE = "measure"
    #: A reading of facts. Carries confidence, and is never a fact.
    INTERPRETATION = "interpretation"


@dataclass(frozen=True, slots=True)
class ItemProvenance:
    """Where this piece of context came from, and what kind of thing it is."""

    layer: Layer = Layer.FACT
    #: The component that produced it: ``tsumugi.ingest/1``, ``kiseki@0.10.0``.
    producer: str = "tsumugi.ingest/1"
    #: When the underlying observation was made, ISO 8601, where known.
    observed_at: str | None = None
    #: Required for an interpretation, refused for a fact. An interpretation
    #: with no confidence is an opinion wearing a fact's clothes.
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.producer:
            raise ValueError("a piece of context with no producer cannot be traced")
        if self.layer is Layer.INTERPRETATION and self.confidence is None:
            raise ValueError(
                "an interpretation must carry a confidence; without one it is "
                "indistinguishable from a fact"
            )
        if self.layer is not Layer.INTERPRETATION and self.confidence is not None:
            raise ValueError(
                f"a {self.layer.value} does not carry confidence; only an "
                f"interpretation is uncertain about itself"
            )
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence} is outside [0, 1]")


@dataclass(frozen=True, slots=True)
class SelectionTrace:
    """The ranker's account of itself, for one item."""

    rank: int
    score: float
    #: Named signals that contributed: ``heading_match``, ``term_density``,
    #: ``recency``. Empty is allowed and means the ranker declined to explain,
    #: which is a thing a reader should be able to see.
    signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError(f"rank {self.rank} is not a position; ranks start at 1")


@dataclass(frozen=True, slots=True)
class ContextItem:
    """A span of a document, selected, with everything needed to check it."""

    item_id: str
    text: str
    anchor: Anchor
    #: What the caller sees. Recorded separately from the anchor because a
    #: consumer holding only the package has no corpus to look the path up in.
    source_path: str = ""
    section: str = ""
    kind: str = "document_span"
    provenance: ItemProvenance = field(default_factory=ItemProvenance)
    selection: SelectionTrace | None = None
    #: In the package's budget unit.
    cost: int = 0

    def __post_init__(self) -> None:
        if not self.item_id:
            raise ValueError("an item with no id cannot be cited")
        if not self.text:
            raise ValueError(
                f"{self.item_id} carries no text; an empty item costs budget for nothing"
            )
        if self.cost < 0:
            raise ValueError(f"{self.item_id} has a negative cost of {self.cost}")
        if len(self.text) != len(self.anchor.span):
            # The one invariant that makes an item evidence rather than a
            # snippet. If these disagree, the anchor does not describe the text
            # that will be sent, and every citation into it is meaningless.
            raise ValueError(
                f"{self.item_id}: the text is {len(self.text)} characters and its "
                f"anchor covers {len(self.anchor.span)}"
            )

    def describe(self) -> str:
        where = self.source_path or self.anchor.document_id
        if self.section:
            where += f" ({self.section})"
        return f"{where}[{self.anchor.span.start}:{self.anchor.span.end}]"
