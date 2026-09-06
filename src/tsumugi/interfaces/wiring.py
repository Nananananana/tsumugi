"""What both composition roots have to build the same way.

The CLI and the MCP server are two thin shells over one application layer
(ADR-0012), and a behaviour available in one and not the other is a defect.
Each used to carry its own copy of the budget-unit-to-cost-model mapping, which
is the smallest possible version of that defect: three lines, duplicated, and
one day different.

This module is the interfaces layer's one shared piece of wiring. It may import
infrastructure, because wiring is what a composition root does; the
application layer may not, which is why `application.cost_model_for` returns
a *name* and this returns the object.
"""

from __future__ import annotations

from ..domain.budget import Unit
from ..infrastructure.cost.heuristic import ByteCost, CharacterCost, HeuristicTokenCost
from ..ports.cost import CostModel

__all__ = ["cost_model_for"]


def cost_model_for(unit: Unit) -> CostModel:
    """The one cost model a budget unit needs."""
    if unit is Unit.TOKENS:
        return HeuristicTokenCost()
    if unit is Unit.BYTES:
        return ByteCost()
    return CharacterCost()
