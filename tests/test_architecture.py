"""The layering, as an executable table.

``docs/architecture.md`` describes these rules in prose. This file is the
authority: a diagram that stops matching the code turns the build red here,
rather than quietly becoming fiction.

``import-linter`` asserts the *direction* between layers and is configured in
``.importlinter``. It cannot express "everything except the standard library",
which is the rule that matters most (ADR-0001), so that one lives here.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "tsumugi"

#: Which layer may import which. The key is a layer, the value is every layer
#: its modules are allowed to name. A layer may always import itself.
#:
#: ``domain`` maps to the empty set on purpose: it knows about no other layer,
#: and about nothing outside the standard library.
ALLOWED: dict[str, frozenset[str]] = {
    # The domain knows about no other layer, and about nothing outside the
    # standard library. It raises built-in exceptions rather than importing
    # ``errors``, which keeps this set genuinely empty.
    "domain": frozenset(),
    "errors": frozenset(),
    "ports": frozenset({"domain", "errors"}),
    "infrastructure": frozenset({"domain", "ports", "errors"}),
    "application": frozenset({"domain", "ports", "errors"}),
    "evaluation": frozenset({"domain", "ports", "application", "infrastructure", "errors"}),
    "config": frozenset({"domain", "ports", "application", "infrastructure", "errors"}),
    "interfaces": frozenset(
        {"domain", "ports", "application", "infrastructure", "evaluation", "config", "errors"}
    ),
    # The package's own ``__init__`` is the public surface. It re-exports and
    # decides nothing.
    "public": frozenset(
        {"domain", "ports", "application", "infrastructure", "evaluation", "config", "errors"}
    ),
}

#: Layers whose modules may not import anything outside the standard library.
#: ADR-0001. The whole package declares zero runtime dependencies, so in
#: practice this holds everywhere -- but the domain is the one where it is a
#: guarantee rather than a current fact, so it is asserted separately and
#: loudly.
STDLIB_ONLY = frozenset({"domain"})

#: Nothing opens a socket except the interfaces layer and the adapters.
#: ADR-0001 and ADR-0016, and the first line of the threat model.
FORBIDDEN_ANYWHERE_IN_CORE = frozenset(
    {"socket", "ssl", "http", "asyncio", "urllib", "ftplib", "smtplib", "telnetlib"}
)

#: The adapters that are allowed to reach the network, by name. An allow-list
#: rather than a rule about the package, so that adding one is a decision
#: somebody makes on purpose (ADR-0016).
NETWORKED_ADAPTERS = frozenset({"ollama.py"})

#: The core stands alone. The siblings are optional adapters and the test suite
#: runs with neither installed.
SIBLINGS = frozenset({"kiseki", "mamori"})


def _layer_of(module: Path) -> str:
    """The layer a module file belongs to, by its path."""
    parts = module.relative_to(SRC).parts
    if len(parts) == 1:
        return "public" if parts[0] == "__init__.py" else parts[0].removesuffix(".py")
    return parts[0]


def _is_adapter(module: Path) -> bool:
    """Inside ``infrastructure/adapters/``, where the outside world is allowed."""
    parts = module.relative_to(SRC).parts
    return len(parts) > 2 and parts[0] == "infrastructure" and parts[1] == "adapters"


def _modules() -> list[Path]:
    found = sorted(SRC.rglob("*.py"))
    assert found, f"no modules under {SRC}; the test is measuring nothing"
    return found


def _imported_roots(module: Path) -> set[tuple[str, int]]:
    """Every top-level module name this file imports, with its line number.

    Relative imports are resolved against the file's own package, so
    ``from ..domain.anchor import Anchor`` reports ``tsumugi.domain.anchor``.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    package = ["tsumugi", *module.relative_to(SRC).parts[:-1]]
    found: set[tuple[str, int]] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                found.add((".".join([*base, node.module or ""]).rstrip("."), node.lineno))
            elif node.module:
                found.add((node.module, node.lineno))
    return found


ALL_MODULES = _modules()


@pytest.mark.parametrize("module", ALL_MODULES, ids=lambda m: str(m.relative_to(SRC)))
def test_a_module_imports_only_from_layers_it_is_allowed_to(module: Path) -> None:
    layer = _layer_of(module)
    assert layer in ALLOWED, f"{module} sits in an unknown layer {layer!r}; add it to ALLOWED"
    permitted = ALLOWED[layer] | {layer}

    for name, line in sorted(_imported_roots(module)):
        if not name.startswith("tsumugi"):
            continue
        parts = name.split(".")
        if len(parts) < 2:
            continue
        imported = parts[1] if not parts[1].endswith(".py") else parts[1].removesuffix(".py")
        assert imported in permitted, (
            f"{module.relative_to(SRC)}:{line} imports {name!r}: "
            f"the {layer!r} layer may not reach into {imported!r}. "
            f"Allowed: {sorted(permitted)}"
        )


@pytest.mark.parametrize("module", ALL_MODULES, ids=lambda m: str(m.relative_to(SRC)))
def test_the_domain_imports_only_the_standard_library(module: Path) -> None:
    if _layer_of(module) not in STDLIB_ONLY:
        pytest.skip("not a stdlib-only layer")

    for name, line in sorted(_imported_roots(module)):
        root = name.split(".")[0]
        if root == "tsumugi":
            continue
        assert root in sys.stdlib_module_names, (
            f"{module.relative_to(SRC)}:{line} imports {name!r}, which is not in the "
            f"standard library. See docs/adr/0001-the-domain-depends-on-nothing.md"
        )


@pytest.mark.parametrize("module", ALL_MODULES, ids=lambda m: str(m.relative_to(SRC)))
def test_nothing_in_the_core_opens_a_socket(module: Path) -> None:
    if _layer_of(module) == "interfaces":
        pytest.skip("the interfaces layer is the only place a server may live")
    if _is_adapter(module) and module.name in NETWORKED_ADAPTERS:
        pytest.skip("named in NETWORKED_ADAPTERS; see the test below")

    for name, line in sorted(_imported_roots(module)):
        root = name.split(".")[0]
        assert root not in FORBIDDEN_ANYWHERE_IN_CORE, (
            f"{module.relative_to(SRC)}:{line} imports {name!r}. Everything but the "
            f"interfaces layer and the adapters named in NETWORKED_ADAPTERS works with "
            f"no network at all, and that is a guarantee rather than a default."
        )


def test_the_adapters_that_reach_the_network_are_the_ones_named() -> None:
    """The allow-list is exhaustive in both directions.

    A new networked adapter fails the test above until it is named here, and a
    name left behind after its adapter stopped needing a socket fails this one.
    ADR-0016: the carve-out has to be argued for, so it has to be visible.
    """
    reaching = {
        module.name
        for module in ALL_MODULES
        if _is_adapter(module)
        and any(
            name.split(".")[0] in FORBIDDEN_ANYWHERE_IN_CORE for name, _ in _imported_roots(module)
        )
    }
    assert reaching == NETWORKED_ADAPTERS, (
        f"the adapters reaching the network are {sorted(reaching)}, and "
        f"NETWORKED_ADAPTERS says {sorted(NETWORKED_ADAPTERS)}. One of the two is out "
        f"of date. See docs/adr/0016-the-network-lives-in-one-place.md"
    )


@pytest.mark.parametrize("module", ALL_MODULES, ids=lambda m: str(m.relative_to(SRC)))
def test_only_the_adapters_may_know_about_a_sibling_project(module: Path) -> None:
    for name, line in sorted(_imported_roots(module)):
        root = name.split(".")[0]
        if root not in SIBLINGS:
            continue
        assert _is_adapter(module), (
            f"{module.relative_to(SRC)}:{line} imports {name!r} outside "
            f"infrastructure/adapters/. tsumugi works with neither sibling installed, "
            f"and that is checked rather than promised."
        )


def test_every_layer_in_the_tree_is_in_the_table() -> None:
    """A new top-level package must be classified, not silently unchecked."""
    layers = {_layer_of(m) for m in ALL_MODULES}
    unknown = layers - set(ALLOWED)
    assert not unknown, (
        f"unclassified layers: {sorted(unknown)}. Add them to ALLOWED and to "
        f"docs/architecture.md, or the layering stops meaning anything."
    )


def test_the_domain_is_not_empty() -> None:
    """Guards against the whole suite passing vacuously.

    Every assertion above is parametrized over discovered files. If the domain
    were empty, or moved, they would all pass while checking nothing.
    """
    domain = [m for m in ALL_MODULES if _layer_of(m) == "domain"]
    assert len(domain) > 1, "the domain layer is empty; the layering tests prove nothing"
