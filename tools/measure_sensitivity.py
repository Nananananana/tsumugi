"""Which constants are on a cliff, and which are on a plateau.

    python tools/measure_sensitivity.py

Every number in this library that decides something was set by a person, and
the corpus it was set against is one corpus. **A threshold that swings the
results when nudged is a threshold fitted to this data**, and it will meet
somebody else's notes and behave differently -- which is the failure that
matters after release, when nobody is watching the number.

So each one is moved and the whole labelled corpus re-scored. What comes out is
not "the best value": that would be fitting them harder. It is the *shape*
around the value already chosen.

    plateau   the numbers barely move -- the value is not doing fine work, and
              a different corpus will not need a different one
    cliff     a small change swings recall or the trap rate -- the value is
              carrying weight it cannot carry off this corpus, and it needs to
              be a setting, or measuring again on data nobody here wrote

**Every knob proves it is connected before it is believed.** Writing this the
obvious way produced a confident plateau for two constants that the sweep was
not moving at all: `SHINGLE` and `DEFAULT_THRESHOLD` are default *arguments*,
bound when the function was defined, so assigning the module attribute changed
a name nothing reads. A plateau and a disconnected wire look identical in the
output -- which is this repository's oldest bug, wearing a measurement's
clothes. So each knob carries `witness`, a cheap observation that must differ
between its extremes, and a knob whose witness does not move is reported as
INERT instead of being scored.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")
from tsumugi.application import build_context as build_module
from tsumugi.application import search as search_module
from tsumugi.domain import redundancy as redundancy_module
from tsumugi.evaluation.dataset import load_cases
from tsumugi.evaluation.runner import cost_model_for, prepared_case
from tsumugi.evaluation.scoring import score_case, summarise
from tsumugi.infrastructure.freshness import FilesystemFreshness

#: How far recall may move across a knob's whole range and still be called a
#: plateau. Three points is about the width of the corpus's own noise: 240
#: cases, so one case is 0.4 points, and a shift of seven cases is not a
#: rounding artefact.
CLIFF = 3.0


@dataclass
class Knob:
    """One number, the values worth trying, and proof that moving it lands."""

    name: str
    values: tuple[float, ...]
    #: What it is for, in the report, so a reader need not go and look it up.
    does: str
    #: Install the value. Returns nothing; `restore` puts it back.
    apply: Callable[[float], Callable[[], None]]
    #: Something cheap that must differ between the extreme values. If it does
    #: not, `apply` is not connected to anything and the sweep says so.
    witness: Callable[[], Any]
    #: Passed to `build_context` instead of patching, for settings that are
    #: already parameters.
    as_argument: str | None = None
    shipped: Any = field(default=None)


def _patch(module: Any, attribute: str) -> Callable[[float], Callable[[], None]]:
    """Assign a module-level constant that is read at call time."""

    def apply(value: float) -> Callable[[], None]:
        before = getattr(module, attribute)
        setattr(module, attribute, value)

        def restore() -> None:
            setattr(module, attribute, before)

        return restore

    return apply


def _patch_shingle(value: float) -> Callable[[], None]:
    """`SHINGLE` reaches the code as a default argument, not as a lookup.

    `shingles(text, size=SHINGLE)` captured the number when the module was
    imported. Rebinding the attribute is invisible to it, so the defaults
    themselves are rewritten -- and `witness` checks that this worked, because
    the tuple index being wrong would fail exactly as silently.
    """
    size = int(value)
    before = redundancy_module.SHINGLE
    functions = (redundancy_module.shingles, redundancy_module.similarity)
    originals = [f.__defaults__ for f in functions]
    # Through `vars()` because `SHINGLE` is `Final`. The annotation is right --
    # nothing in the library may reassign it -- and a sweep whose whole job is
    # moving it is the one caller it is not addressed to. `setattr` would read
    # better and ruff rewrites it straight back into an assignment mypy refuses.
    vars(redundancy_module)["SHINGLE"] = size
    for function in functions:
        assert function.__defaults__, f"{function.__name__} has no default to move"
        function.__defaults__ = (size,)

    def restore() -> None:
        vars(redundancy_module)["SHINGLE"] = before
        for function, original in zip(functions, originals, strict=True):
            function.__defaults__ = original

    return restore


KNOBS = (
    Knob(
        name="COVERAGE_THRESHOLD",
        values=(0.6, 0.8, 1.0),
        does="how much of a question must be present before coverage confirms it",
        apply=_patch(search_module, "COVERAGE_THRESHOLD"),
        witness=lambda: search_module.COVERAGE_THRESHOLD,
    ),
    Knob(
        name="RELATIVE_MATCH_FLOOR",
        values=(0.5, 0.65, 0.8, 0.95),
        does="how weak a match may be, measured against the best for the same query",
        apply=_patch(search_module, "RELATIVE_MATCH_FLOOR"),
        witness=lambda: search_module.RELATIVE_MATCH_FLOOR,
    ),
    Knob(
        name="MATCH_WEIGHT",
        values=(0.0, 0.05, 0.1, 0.3),
        does="how much confirming more of the question adds to a score",
        apply=_patch(search_module, "MATCH_WEIGHT"),
        witness=lambda: search_module.MATCH_WEIGHT,
    ),
    Knob(
        name="INFLECTION_TAIL",
        values=(0, 1, 2, 3),
        does="characters allowed to hang off the end of a term when matching",
        apply=_patch(search_module, "INFLECTION_TAIL"),
        witness=lambda: search_module.INFLECTION_TAIL,
    ),
    Knob(
        name="redundancy_threshold",
        values=(0.5, 0.75, 0.9),
        does="how alike two passages must be before one is marked a duplicate",
        apply=lambda _: lambda: None,
        as_argument="redundancy_threshold",
        witness=lambda: None,
        shipped=redundancy_module.DEFAULT_THRESHOLD,
    ),
    Knob(
        name="SHINGLE",
        values=(3, 5, 8),
        does="characters per shingle, when two passages are compared",
        apply=_patch_shingle,
        witness=lambda: redundancy_module.similarity("abcdefgh", "abcXefgh").score,
        shipped=redundancy_module.SHINGLE,
    ),
)


@contextmanager
def _moved(knob: Knob, value: float) -> Iterator[None]:
    """Move a knob for one run and put it back, even if the run raises."""
    restore = knob.apply(value)
    try:
        yield
    finally:
        restore()


def _connected(knob: Knob) -> bool:
    """Does moving this knob change anything observable at all?"""
    if knob.as_argument:
        return True  # passed straight to the call; there is no wire to be loose
    low, high = knob.values[0], knob.values[-1]
    with _moved(knob, low):
        first = knob.witness()
    with _moved(knob, high):
        second = knob.witness()
    return bool(first != second)


def _score(cases: list[Any], extra: dict[str, Any]) -> tuple[float, float]:
    """Evidence recall and trap rate, **as the gate defines them**.

    Written the short way first, and it disagreed with the gate: it counted one
    required fact per case and one trap per case, where `score_case` counts
    every required fact and every forbidden one. The sweep read 5.0% where
    `tsumugi eval` read 4.2%, and a tuning report that quietly grades on its own
    curve is worse than no report. So the build is the runner's build, the
    scoring is the runner's scoring, and only the knob differs.
    """
    scores = []
    for case in cases:
        with prepared_case(case) as (store, index, root):
            package = build_module.build_context(
                case.question,
                store=store,
                index=index,
                cost_model=cost_model_for(case.budget.unit),
                budget=case.budget,
                version="eval",
                freshness=FilesystemFreshness(root),
                **extra,
            )
        scores.append(score_case(case, package))
    summary = summarise(scores)
    return (
        (summary.evidence_recall or 0.0) * 100,
        (summary.trap_rate or 0.0) * 100,
    )


def main() -> int:
    cases = [c for c in load_cases(Path("tests/cases")) if c.must_include]
    assert cases, "no cases with a required fact; measuring nothing"
    print(f"{len(cases)} cases, one case is {100 / len(cases):.1f} recall points")
    print(f"a range wider than {CLIFF} points is called a cliff")

    verdicts: list[tuple[str, str, float]] = []
    for knob in KNOBS:
        shipped = knob.shipped if knob.shipped is not None else knob.witness()
        if not _connected(knob):
            print(f"{chr(10)}{knob.name}  INERT -- moving it changes nothing observable")
            print("  not scored: a disconnected knob measures as a perfect plateau")
            verdicts.append((knob.name, "INERT", 0.0))
            continue

        print(f"{chr(10)}{knob.name}  (shipped: {shipped})")
        print(f"  {knob.does}")
        rows: list[tuple[float, float, float]] = []
        for value in knob.values:
            extra = {knob.as_argument: value} if knob.as_argument else {}
            with _moved(knob, value):
                recall, trap = _score(cases, extra)
            rows.append((value, recall, trap))
            here = "  <- shipped" if value == shipped else ""
            print(f"    {value:>6}  recall {recall:5.1f}%  trap {trap:5.1f}%{here}")

        swing = max(r for _, r, _ in rows) - min(r for _, r, _ in rows)
        traps = max(t for _, _, t in rows) - min(t for _, _, t in rows)
        verdict = "CLIFF" if swing >= CLIFF or traps >= CLIFF else "plateau"
        print(f"    range: recall {swing:.1f} pts, trap {traps:.1f} pts -> {verdict}")
        verdicts.append((knob.name, verdict, max(swing, traps)))

    print(f"{chr(10)}{'-' * 62}")
    for name, verdict, swing in sorted(verdicts, key=lambda v: -v[2]):
        print(f"  {verdict:>8}  {swing:5.1f} pts  {name}")
    cliffs = [n for n, v, _ in verdicts if v == "CLIFF"]
    inert = [n for n, v, _ in verdicts if v == "INERT"]
    print()
    print(
        "A plateau means the value is not doing fine work, and another corpus will"
        + chr(10)
        + "not need a different one. A cliff means it is carrying weight it cannot"
        + chr(10)
        + "carry off this corpus: it belongs in configuration, or wants measuring"
        + chr(10)
        + "on data nobody here wrote."
    )
    if cliffs:
        print(f"{chr(10)}cliffs: {', '.join(cliffs)}")
    if inert:
        print(f"inert (fix the sweep, not the constant): {', '.join(inert)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
