"""Change the code on purpose and see whether the tests notice.

    python tools/mutate.py src/tsumugi/application/search.py tests/test_ingest_and_search.py
    python tools/mutate.py src/tsumugi/domain/budget.py tests/test_budget_and_cost.py --limit 20

Every check in this repository has been broken by hand and watched to fail,
one at a time, because *a check that cannot fail is not a check*. This does
that mechanically: it edits one expression, runs the tests, and records whether
anything went red.

**A surviving mutant is a change to shipped code that no test objects to.** Not
always a defect -- some survivors are equivalent programs, and a few are
behaviour nobody promised -- but every one is a question the suite cannot
answer, and this week produced a concrete reason to care. `_confirm` computed
anchor offsets in folded space and applied them to the original; the tests were
green because 0 of 780 corpus documents change length under NFKC. That is a
surviving mutant that shipped.

`mutmut` is the usual tool and refuses to run on Windows, which is the platform
this project is developed on, so this is forty lines instead of a dependency
that works elsewhere.

**It restores the file in a `finally` and compares bytes afterwards.** An
earlier hand-run of this idea left a stray line in `pyproject.toml` for an hour
because the restore was assumed rather than checked.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Comparisons, swapped for the neighbour a typo would produce. Not `==` to
#: `!=`: that is usually caught by anything, and the interesting survivors sit
#: on boundaries.
COMPARISONS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}


@dataclass
class Mutant:
    line: int
    what: str


class _Mutator(ast.NodeTransformer):
    """Applies exactly one mutation, chosen by index, and reports what it did."""

    def __init__(self, target: int) -> None:
        self.target = target
        self.seen = 0
        self.applied: Mutant | None = None

    def _take(self, node: ast.AST, what: str) -> bool:
        hit = self.seen == self.target
        if hit:
            self.applied = Mutant(getattr(node, "lineno", 0), what)
        self.seen += 1
        return hit

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        for index, op in enumerate(node.ops):
            replacement = COMPARISONS.get(type(op))
            if replacement is None:
                continue
            if self._take(node, f"{type(op).__name__} -> {replacement.__name__}"):
                node.ops[index] = replacement()
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        self.generic_visit(node)
        flipped = "Or" if isinstance(node.op, ast.And) else "And"
        if self._take(node, f"{type(node.op).__name__} -> {flipped}"):
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, bool) and self._take(node, f"{node.value} -> {not node.value}"):
            return ast.Constant(value=not node.value)
        if (
            not isinstance(node.value, bool)
            and isinstance(node.value, int)
            and -2 <= node.value <= 64
            and self._take(node, f"{node.value} -> {node.value + 1}")
        ):
            return ast.Constant(value=node.value + 1)
        return node


def _count(source: str) -> int:
    mutator = _Mutator(target=-1)
    mutator.visit(ast.parse(source))
    return mutator.seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="the module to mutate")
    parser.add_argument("tests", nargs="+", help="test paths that should object")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many mutants")
    args = parser.parse_args(argv)

    path = (ROOT / args.target).resolve()
    original = path.read_bytes()
    source = original.decode("utf-8")
    total = _count(source)
    if args.limit:
        total = min(total, args.limit)
    print(f"{args.target}: {total} mutants, tests: {' '.join(args.tests)}")

    survivors: list[Mutant] = []
    killed = 0
    try:
        for index in range(total):
            mutator = _Mutator(index)
            tree = mutator.visit(ast.parse(source))
            if mutator.applied is None:
                continue
            ast.fix_missing_locations(tree)
            path.write_text(ast.unparse(tree), encoding="utf-8")

            finished = subprocess.run(  # noqa: S603 - argv is built here
                [sys.executable, "-m", "pytest", *args.tests, "-x", "-q", "--no-header"],
                cwd=ROOT,
                capture_output=True,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
            if finished.returncode == 0:
                survivors.append(mutator.applied)
                print(f"  SURVIVED line {mutator.applied.line:>4}  {mutator.applied.what}")
            else:
                killed += 1
    finally:
        path.write_bytes(original)
        assert path.read_bytes() == original, "the original was not restored"

    print()
    print(f"{killed} killed, {len(survivors)} survived of {total}")
    if survivors:
        print("A survivor is a change no test objects to. Some are equivalent programs;")
        print("the rest are questions this suite cannot answer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
