"""Estimating tokens without a tokenizer, and saying how wrong it is.

The weights below are tokens per character, per script class, fitted by least
squares against ``cl100k_base`` over 7,363 four-hundred-character windows of
real mixed Japanese, Chinese, English and source code -- with every seventh
window held out and used only to score the result. Reproduce with:

    python tools/measure_cost.py calibrate <corpus> ...

In-sample and held-out error came out within half a percentage point of each
other (p95 0.1786 against 0.1828), which is what a model with seven parameters
should do. It is not evidence that the estimator is good; it is evidence that
the number below is not flattered by the corpus it was fitted on.

The spread is the reason this file exists. A Latin character costs about 0.22
tokens and a kanji about 1.29 -- a factor of six. A library that used one
constant for both would be comfortable in English and blow the context window
in Japanese, which is the direction that actually hurts (ADR-0006).

Nothing here is imported by the core. ``tiktoken`` appears only in the tool.
"""

from __future__ import annotations

from typing import Final

from ...ports.cost import MeasuredError
from .scripts import ScriptClass, profile

__all__ = ["ByteCost", "CharacterCost", "HeuristicTokenCost"]

#: Tokens per character. Fitted, except where noted.
_WEIGHTS: Final[dict[ScriptClass, float]] = {
    ScriptClass.LATIN: 0.2151,
    ScriptClass.IDEOGRAPH: 1.2923,
    ScriptClass.KANA: 1.1919,
    # NOT FITTED. The calibration corpus had no Korean, so least squares
    # returned zero -- which would say Hangul is free, and a budget that thinks
    # Korean costs nothing is worse than one that guesses. Set by analogy with
    # kana, which behaves similarly under byte-pair encoding, and marked here so
    # that nobody mistakes it for a measurement. A Korean corpus would fix it.
    ScriptClass.HANGUL: 1.2,
    ScriptClass.DIGIT: 0.814,
    ScriptClass.SPACE: 0.1601,
    ScriptClass.OTHER: 0.484,
}

#: Travels in every package built with this model, because a token count with
#: no stated error is a number pretending to be a measurement.
#: Measured on the held-out seventh of the calibration corpus -- windows the fit
#: never saw. The corpus contained no Korean, so this error says nothing about
#: Hangul, and naming that is honest rather than sufficient.
_MEASURED_ERROR: Final = MeasuredError(
    p50=0.0495,
    p95=0.1828,
    against="cl100k_base",
    dataset="mixed ja/zh/en/code, 1228 held-out windows of 400 characters",
)


class HeuristicTokenCost:
    """Satisfies :class:`~tsumugi.ports.cost.CostModel`. Estimates tokens."""

    name = "heuristic/cjk-aware@1"
    unit = "tokens"

    @property
    def measured_error(self) -> MeasuredError:
        return _MEASURED_ERROR

    def cost(self, text: str) -> int:
        if not text:
            return 0
        counts = profile(text)
        estimate = sum(_WEIGHTS[cls] * count for cls, count in counts.items())
        # Never zero for non-empty text: a piece of context that costs nothing
        # would be admitted to any budget, however long.
        return max(1, round(estimate))


class CharacterCost:
    """Exact. Python string length, which is the unit every offset uses."""

    name = "characters@1"
    unit = "characters"
    measured_error = None

    def cost(self, text: str) -> int:
        return len(text)


class ByteCost:
    """Exact. UTF-8, which is what the hashes and the store use."""

    name = "bytes/utf-8@1"
    unit = "bytes"
    measured_error = None

    def cost(self, text: str) -> int:
        return len(text.encode("utf-8"))
