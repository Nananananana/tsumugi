"""Run exactly what CI runs, and report by exit code.

    python tools/gates.py

Every gate that guards this repository, in one command, each judged by its
**exit status** rather than by what it printed.

It exists because of three failures in one day, all mine, all the same shape:

- `ruff format --check` was failing on a markdown code block while I ran
  `ruff format src/ tests/` -- the rewriting form, over two directories -- and
  reported every gate green;
- `lint-imports` had been broken for nine commits while I read
  `lint-imports | tail -1`, whose last line is blank on failure;
- and the third was reporting the result of a check run two commits earlier as
  though it described the current tree.

Each time the command I ran was narrower than the command that gates, and each
time the difference was invisible in the output I looked at. A gate verified by
reading a tail is a gate that can stop existing.

**This does not replace CI, and the gap is specific.** This runs in *this*
environment, and CI runs in several deliberately different ones. It cannot see:

- **a dependency that is present here and absent there.** mypy failed in CI for
  nine commits on `if TYPE_CHECKING: from mamori import PrivacySession`,
  because the lint job installs `[dev]` without the siblings. This file
  reported mypy green the whole time, correctly, for this machine.
- the no-extras install, the wheel contents, and the OS × Python matrix.

To reproduce an environment difference, build the environment:

    uv venv /tmp/lint && uv pip install --python /tmp/lint/Scripts/python -e ".[dev]"
    /tmp/lint/Scripts/python -m mypy

What this removes is one class of mistake -- running something *nearly* like
the gate and reading the output instead of the exit code. It does not remove
"green here" being mistaken for "green there".
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _script(name: str) -> str:
    """A console script from this interpreter's environment.

    Resolved from `sys.executable` rather than found on PATH: the point is to
    run the tool belonging to the environment under test, not whichever one a
    shell happens to see.
    """
    scripts = Path(sys.executable).parent
    for candidate in (scripts / f"{name}.exe", scripts / name):
        if candidate.exists():
            return str(candidate)
    return name


#: Name, and the command as CI runs it. Kept in the same order CI does, so a
#: reader comparing this file with `.github/workflows/ci.yml` can do it by eye.
GATES: tuple[tuple[str, list[str]], ...] = (
    ("ruff check", [sys.executable, "-m", "ruff", "check", "."]),
    ("ruff format --check", [sys.executable, "-m", "ruff", "format", "--check", "."]),
    ("mypy", [sys.executable, "-m", "mypy"]),
    # The console script, because that is what CI runs. `python -m
    # importlinter.cli lint` prints nothing and exits 0 -- a silent no-op, and
    # it was in this list on the first run of this file. A gate runner with a
    # dead gate is the joke this file exists to stop, arriving inside it.
    ("import-linter", [_script("lint-imports")]),
    ("pytest", [sys.executable, "-m", "pytest", "-q"]),
    (
        "evaluation fixtures",
        [sys.executable, "tools/generate_cases.py", "--out", "tests/cases", "--check-only"],
    ),
    (
        "eval floors",
        [sys.executable, "-m", "tsumugi.interfaces.cli.main", "eval", "--tier", "ci"],
    ),
)


def main() -> int:
    failures: list[tuple[str, int, str]] = []
    for name, command in GATES:
        began = time.perf_counter()
        finished = subprocess.run(  # noqa: S603
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**_environment(), "PYTHONUTF8": "1"},
        )
        took = time.perf_counter() - began
        status = "ok  " if finished.returncode == 0 else "FAIL"
        print(f"{status} {name:24} {took:5.1f}s")
        if finished.returncode != 0:
            failures.append((name, finished.returncode, finished.stdout + finished.stderr))

    if not failures:
        print(f"\n{len(GATES)} gates, all green.")
        return 0

    for name, code, output in failures:
        print(f"\n--- {name} (exit {code}) " + "-" * 40)
        # The tail, but only after the exit code has already decided. That is
        # the whole difference.
        print("\n".join(output.splitlines()[-30:]))
    print(f"\n{len(failures)} of {len(GATES)} gates failed.")
    return 1


def _environment() -> dict[str, str]:
    import os

    return dict(os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
