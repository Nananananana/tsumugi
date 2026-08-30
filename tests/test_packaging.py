"""What the distribution metadata may and may not say.

One test, aimed at one trap, added the day a sibling `extra` was removed for
promising something no resolver could deliver.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_no_dependency_is_a_direct_reference() -> None:
    """A `pkg @ git+https://...` requirement makes the project unpublishable.

    PyPI rejects an upload whose `requires_dist` carries a direct URL with a
    400. Nothing local tells you that, and the road there is short and
    signposted the wrong way: hatchling *does* refuse the build, and its error
    message names `tool.hatch.metadata.allow-direct-references` as the fix.
    Set it and the build succeeds, `twine check` reports PASSED, and the
    package cannot be uploaded. Every check goes green as the door closes.

    This is not hypothetical. `mamori` is not on PyPI, so the obvious repair
    for the `siblings` extra -- which promised `mamori>=0.14` and could never
    resolve -- is exactly this direct reference. The extra was removed instead;
    see the comment where it used to be.

    The rule is not "never depend on git". It is that a *published* dependency
    may not, because the publisher is the one who cannot then publish. CI
    installs the sibling from git on the command line, which has no such
    problem, and that is the supported way to develop against one.
    """
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    requirements = list(metadata.get("dependencies", []))
    for extra, entries in metadata.get("optional-dependencies", {}).items():
        requirements += [f"{extra}: {entry}" for entry in entries]

    direct = [requirement for requirement in requirements if "@" in requirement]
    assert not direct, (
        "a direct reference in the distribution metadata makes this project "
        f"unpublishable, and no local check would say so: {direct}"
    )


def test_the_runtime_has_no_dependencies() -> None:
    """Zero runtime dependencies, asserted where the claim is made.

    The README says it, `docs/architecture.md` says it, and until now nothing
    checked it -- a `dependencies` entry added in a hurry would have made all
    three wrong at once and quietly.
    """
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert metadata.get("dependencies", []) == []
