"""What the distribution metadata may and may not say.

One test, aimed at one trap, added the day a sibling `extra` was removed for
promising something no resolver could deliver.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


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


def test_every_exception_this_library_exports_can_actually_be_raised() -> None:
    """No exported exception describes a situation nothing produces.

    Four did. `errors.ContractError` was documented as *raised on reading a
    package whose contract version is unrecognised*, and the only code that
    reads one is in `domain`, which the layer table forbids from importing
    `errors` -- so it was a promise the architecture would not let anything
    keep. `AnchorError` and its two subclasses were a fossil: the
    stale-versus-unresolvable distinction is carried by `ResolutionStatus` and
    **returned**, because ADR-0010's whole point is that evidence which was
    true in May is historical rather than false, and a thing you return is not
    a thing you raise.

    An unraisable exception is worse than a missing one. A consumer writes
    `except StaleAnchorError:` and gets a branch that looks like handling and
    never runs -- the same shape as a check that cannot fail.

    Base classes are exempt, and only when something actually subclasses them.
    """
    import tsumugi
    from tsumugi import errors

    exported = {
        name: obj
        for name, obj in vars(errors).items()
        if isinstance(obj, type) and issubclass(obj, Exception) and not name.startswith("_")
    }
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src").rglob("*.py"))
    bases = {base.__name__ for obj in exported.values() for base in obj.__mro__[1:]}

    unraisable = sorted(
        name
        for name in exported
        if f"raise {name}" not in source and name not in bases and name in tsumugi.__all__
    )
    assert not unraisable, (
        f"exported, documented, and never raised: {unraisable}. Either raise it "
        "or delete it; a branch a consumer writes that can never run is worse "
        "than no branch."
    )


def test_the_library_ships_its_type_information() -> None:
    """PEP 561: without `py.typed`, every consumer's type checker ignores us.

    This library runs `mypy --strict` over itself and shipped as **untyped**.
    A consumer importing it got `Skipping analyzing "tsumugi": module is
    installed, but missing library stubs or py.typed marker`, and with that one
    line silenced -- which is the usual response -- the annotations stopped
    existing:

        schema: int = contract_schema(123)   # no error, before
        schema: int = contract_schema(123)   # two errors, after

    Strictest possible checking inside, nothing delivered outside, silently.
    The marker must also reach the wheel: `.github/workflows/ci.yml` asserts
    that on an installed copy, the way it does for the schema.
    """
    marker = ROOT / "src" / "tsumugi" / "py.typed"
    assert marker.exists(), "PEP 561 marker missing; consumers see an untyped package"
