"""`tools/gates.py` says it runs what CI runs. This is what holds it to that.

Added after a question from a sibling project: every break-and-watch in this
repository injected a *changed* value, and none had *removed* something. A
loosened rule still parses; a deleted one leaves nothing to parse and nothing
to notice.

The deletion that mattered here is not in the code -- it is a gate quietly
covering less of CI than it claims. `gates.py` reports `7 gates, all green`,
and until now the only thing tying that seven to the workflow was a sentence in
its docstring saying a reader could compare the two files *by eye*.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from gates import GATES, NOT_RUN_HERE  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: Step names, not `run:` bodies. A name survives reformatting and a
#: multi-line `run: |`, and renaming one is exactly the removal this catches.
STEP = re.compile(r"^\s*- name: (.+?)\s*$", re.MULTILINE)


def test_every_ci_step_is_run_here_or_declared_unrunnable() -> None:
    """Each CI step is either a gate or an entry saying why it cannot be.

    Two directions, and the second is the one with teeth:

    - a gate naming a step that no longer exists means `gates.py` is running
      something CI does not, and its result no longer predicts CI;
    - **a CI step in neither list means the gate runner silently covers less
      than it did.** Nothing would have failed. `gates.py` would have gone on
      printing the same reassuring line over a smaller share of the workflow --
      not a broken check, a shrinking one, which is harder to see and is this
      project's own named failure class.

    Deliberately name-based and deliberately strict. If a step is renamed, this
    fails and someone decides which list it belongs in; that decision is the
    entire value. `NOT_RUN_HERE` carries a reason per entry, so declaring a step
    unrunnable costs a sentence rather than a word.
    """
    steps = set(STEP.findall(WORKFLOW.read_text(encoding="utf-8")))
    assert steps, "no steps parsed from ci.yml -- the regex, not the workflow"

    covered = {step for _name, step, _command in GATES}
    declared = set(NOT_RUN_HERE)

    missing = steps - covered - declared
    assert not missing, (
        f"CI steps that this repository's gate runner neither runs nor "
        f"declares unrunnable: {sorted(missing)}. Add a gate, or an entry in "
        f"NOT_RUN_HERE saying why not."
    )

    stale = (covered | declared) - steps
    assert not stale, f"named in tools/gates.py but not a step in ci.yml: {sorted(stale)}"
