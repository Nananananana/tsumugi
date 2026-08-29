"""Measuring what the selection does, against a corpus with known answers.

The corpus is generated; the labels are computed; **nothing labels the ideal
output**. Every number here is arithmetic over anchors, with no grader, no
model and no rubric.

See ``docs/adr/0013-label-the-evidence-not-the-ideal-answer.md`` and
``docs/evaluation-corpus.md``.
"""

from __future__ import annotations

from .dataset import Case, Trap, load_case, load_cases
from .markup import PlantedFact, strip_markup
from .runner import run_case, run_cases
from .scoring import CaseScore, Summary, score_case, summarise

__all__ = [
    "Case",
    "CaseScore",
    "PlantedFact",
    "Summary",
    "Trap",
    "load_case",
    "load_cases",
    "run_case",
    "run_cases",
    "score_case",
    "strip_markup",
    "summarise",
]
