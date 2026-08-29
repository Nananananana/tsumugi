"""Fit the token estimator's coefficients, then measure how wrong they are.

ADR-0006 says a token budget is an estimate, that the estimate must state its
error, and that the error is measured against a real tokenizer in
development-only tests. This tool does both halves:

    python tools/measure_cost.py calibrate <corpus> [<corpus> ...]
    python tools/measure_cost.py fit       <corpus> [<corpus> ...]
    python tools/measure_cost.py score     <corpus> [<corpus> ...]

``calibrate`` is the one to run. It holds out every seventh window, fits on the
rest, and scores the fit on the windows it never saw -- because weights scored
on the text they were fitted to will always look better than they are, and the
number that ships has to be the honest one.

``fit`` and ``score`` do the two halves separately over everything, for when you
want to see in-sample error or re-derive weights from a specific corpus.

The split is deterministic (every seventh window, no random seed) so that two
runs over the same corpus produce the same number, for the same reason a
package has to be reproducible (ADR-0003).

Requires ``tiktoken``, which is a development dependency and is not installed
by ``pip install tsumugi``. The library never imports it: the whole reason the
estimator exists is that the core has no tokenizer.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsumugi.infrastructure.cost.scripts import ScriptClass, profile
from tsumugi.infrastructure.filesystem import walk

ORDER = list(ScriptClass)

#: Long enough that the ratio is stable, short enough that a document
#: contributes several samples. Prompts are built from passages, not files.
WINDOW = 400


def _samples(roots: list[Path]) -> list[str]:
    found: list[str] = []
    for root in roots:
        for path in walk(root).files:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for start in range(0, len(text), WINDOW):
                window = text[start : start + WINDOW]
                if len(window) >= WINDOW // 2:
                    found.append(window)
    return found


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Gauss-Jordan on the normal equations. Small system, no numpy."""
    size = len(vector)
    augmented = [[*row, vector[i]] for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            continue
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * other
                for value, other in zip(augmented[row], augmented[column], strict=True)
            ]
    return [row[size] for row in augmented]


def fit(samples: list[str], encoder: object) -> dict[ScriptClass, float]:
    """Least squares: tokens ~= sum(count[class] * weight[class])."""
    size = len(ORDER)
    normal = [[0.0] * size for _ in range(size)]
    right = [0.0] * size

    for text in samples:
        counts = profile(text)
        row = [float(counts[cls]) for cls in ORDER]
        target = float(len(encoder.encode(text)))  # type: ignore[attr-defined]
        for i in range(size):
            right[i] += row[i] * target
            for j in range(size):
                normal[i][j] += row[i] * row[j]

    weights = _solve(normal, right)
    # A negative tokens-per-character is arithmetic, not meaning. Clamp rather
    # than letting one noisy class make the whole estimate absurd.
    return {cls: max(0.0, round(weight, 4)) for cls, weight in zip(ORDER, weights, strict=True)}


def score(samples: list[str], encoder: object) -> dict[str, float]:
    """Relative error of the committed estimator against the real tokenizer."""
    from tsumugi.infrastructure.cost.heuristic import HeuristicTokenCost

    model = HeuristicTokenCost()
    errors: list[float] = []
    for text in samples:
        actual = len(encoder.encode(text))  # type: ignore[attr-defined]
        if actual == 0:
            continue
        errors.append(abs(model.cost(text) - actual) / actual)
    errors.sort()
    return {
        "samples": float(len(errors)),
        "p50": round(statistics.median(errors), 4),
        "p95": round(errors[min(len(errors) - 1, int(len(errors) * 0.95))], 4),
        "worst": round(errors[-1], 4),
        "mean": round(statistics.fmean(errors), 4),
    }


def _split(samples: list[str], every: int = 7) -> tuple[list[str], list[str]]:
    """Deterministic hold-out: every ``every``-th window is unseen by the fit."""
    train = [s for i, s in enumerate(samples) if i % every]
    held = [s for i, s in enumerate(samples) if not i % every]
    return train, held


def _score_with(
    samples: list[str], encoder: object, weights: dict[ScriptClass, float]
) -> dict[str, float]:
    errors: list[float] = []
    for text in samples:
        actual = len(encoder.encode(text))  # type: ignore[attr-defined]
        if actual == 0:
            continue
        estimate = max(1, round(sum(weights[c] * n for c, n in profile(text).items())))
        errors.append(abs(estimate - actual) / actual)
    errors.sort()
    return {
        "samples": float(len(errors)),
        "p50": round(statistics.median(errors), 4),
        "p95": round(errors[min(len(errors) - 1, int(len(errors) * 0.95))], 4),
        "worst": round(errors[-1], 4),
        "mean": round(statistics.fmean(errors), 4),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("calibrate", "fit", "score"))
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--encoding", default="cl100k_base")
    args = parser.parse_args(argv)

    try:
        import tiktoken
    except ImportError:
        print(
            "this tool needs tiktoken, a development dependency:\n"
            "    uv pip install tiktoken\n"
            "The library itself has no tokenizer, which is why the estimator exists.",
            file=sys.stderr,
        )
        return 2

    encoder = tiktoken.get_encoding(args.encoding)
    samples = _samples([r.resolve() for r in args.roots])
    if not samples:
        print("no readable text under those paths", file=sys.stderr)
        return 2

    print(f"{len(samples)} windows of {WINDOW} characters, against {args.encoding}\n")

    if args.action == "calibrate":
        train, held = _split(samples)
        weights = fit(train, encoder)
        print(f"fitted on {len(train)}, held out {len(held)}")
        print()
        print("_WEIGHTS: Final = {")
        for cls, weight in weights.items():
            print(f"    ScriptClass.{cls.name}: {weight},")
        print("}")
        print()
        for label, subset in (("in-sample", train), ("HELD OUT", held)):
            row = _score_with(subset, encoder, weights)
            print(
                f"  {label:10} p50 {row['p50']:.4f}  p95 {row['p95']:.4f}  worst {row['worst']:.4f}"
            )
        print()
        print("The held-out row is the one that ships.")
    elif args.action == "fit":
        weights = fit(samples, encoder)
        print("_WEIGHTS: Final = {")
        for cls, weight in weights.items():
            print(f"    ScriptClass.{cls.name}: {weight},")
        print("}")
    else:
        for name, value in score(samples, encoder).items():
            print(f"  {name:8} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
