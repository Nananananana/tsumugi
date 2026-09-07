"""The published documents say what the code does, checked rather than believed.

That document is the contract. It is what sora, kiseki and akashi read to
decide what to expect, and the JSON block near the top of it is the first
thing anybody looks at -- earlier than the field rules, and much earlier than
the schema.

It had drifted, and in the way prose does: quietly, and only in the details a
reader would act on.

- ``"signals": ["heading_match", "term_density", "recency"]``. One of those
  three exists. A consumer branching on `term_density` waits for a value that
  has never been emitted;
- ``"item_id": "itm_01"`` where every real id has three digits, so an example
  id would not match a real one;
- ``"reason": "94% overlap with itm_01; kept the earlier-dated source"``, which
  describes a choice **ADR-0015 refuses to make**: redundancy says two passages
  are alike and has no way to know which is right. The document promised
  judgement the library deliberately does not exercise;
- ``"ranked 7th; 2210 estimated tokens would exceed the 8000 limit"``, a
  sentence in a format nothing produces.

None of that was reachable by the conformance suite, which checks packages
against the schema and never reads the document. So this file reads the
document.

**The elisions stay.** ``"sha256:..."`` in an example is doing its job, and a
sixty-four character digest in a document nobody can verify is worse than an
honest gap. They are filled in here before validating, and nowhere else.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tsumugi.application.build_context import (
    SIGNAL_CONFIRMED,
    SIGNAL_LEXICAL,
    SIGNAL_SECTION,
)
from tsumugi.domain.assembly import REDUNDANT_SIGNAL
from tsumugi.domain.omission import OmissionRule

DOCUMENT = Path(__file__).resolve().parent.parent / "docs" / "context-package.md"
SCHEMA = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "tsumugi"
    / "schemas"
    / "context-package-1.json"
)

#: Signals with no value attached.
PLAIN_SIGNALS = frozenset({SIGNAL_LEXICAL, SIGNAL_SECTION, SIGNAL_CONFIRMED})

#: Signals that carry one, and the shape of the value. `NN% overlap` is
#: `Overlap.describe()` reaching the item beside its `redundant_with:`.
VALUED_SIGNALS = (
    re.compile(r"^confirmed_share:\d\.\d\d$"),
    re.compile(rf"^{re.escape(REDUNDANT_SIGNAL)}:itm_\d{{3}}$"),
    re.compile(r"^\d{1,3}% overlap$"),
)

#: An elided digest in the document. Filled in to validate and left alone in
#: the file: a reader cannot check a digest either way, and a real-looking one
#: invites them to try.
ELIDED = re.compile(r"sha256:[0-9a-f]*\.\.\.")


def _blocks() -> list[dict[str, Any]]:
    text = DOCUMENT.read_text(encoding="utf-8")
    found = []
    for raw in re.findall(r"```json\n(.*?)\n```", text, re.S):
        try:
            parsed = json.loads(ELIDED.sub("sha256:" + "0" * 64, raw))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            found.append(parsed)
    return found


@pytest.fixture(scope="module")
def example() -> dict[str, Any]:
    packages = [block for block in _blocks() if "contract" in block]
    assert len(packages) == 1, f"expected one package in {DOCUMENT.name}, found {len(packages)}"
    return packages[0]


class TestTheExampleIsReal:
    def test_it_is_a_package_worth_checking(self, example: dict[str, Any]) -> None:
        """The positive control, first.

        Every assertion below walks a list. An example that lost its `items`
        or `omissions` would satisfy all of them by having nothing to check --
        which is the failure this file was written to catch in the document,
        and would be embarrassing to reproduce in the file itself.
        """
        assert example["contract"] == "tsumugi.context-package/1"
        assert len(example["items"]) >= 1
        assert len(example["omissions"]) >= 2, "one per rule the example means to show"

    def test_it_validates_against_the_schema_it_publishes(self, example: dict[str, Any]) -> None:
        """The document and the schema ship together and disagreed.

        Not caught by the conformance suite: that validates *packages*, and
        the example is prose until somebody parses it.
        """
        jsonschema.validate(example, json.loads(SCHEMA.read_text(encoding="utf-8")))

    def test_every_signal_is_one_the_library_emits(self, example: dict[str, Any]) -> None:
        signals = [
            signal
            for item in example["items"]
            for signal in item.get("selection", {}).get("signals", [])
        ]
        assert signals, "an example with no signals demonstrates nothing"
        for signal in signals:
            recognised = signal in PLAIN_SIGNALS or any(
                pattern.match(signal) for pattern in VALUED_SIGNALS
            )
            assert recognised, f"{signal!r} is not a signal this library emits"

    def test_every_omission_names_a_defined_rule(self, example: dict[str, Any]) -> None:
        defined = {rule.value for rule in OmissionRule}
        for omission in example["omissions"]:
            assert omission["rule"] in defined, omission["rule"]
            assert omission["reason"].strip(), "a rule without a reason is a rule nobody can act on"

    def test_every_item_id_has_the_shape_the_assembler_gives_it(
        self, example: dict[str, Any]
    ) -> None:
        """`itm_{rank:03d}`. Two digits in the document and three in the code
        is the kind of difference a consumer only finds by comparing strings
        that should have matched."""
        for item in example["items"]:
            assert re.fullmatch(r"itm_\d{3}", item["item_id"]), item["item_id"]

    def test_a_reason_that_mentions_an_item_mentions_a_real_one(
        self, example: dict[str, Any]
    ) -> None:
        """The `redundant_candidate` reason names the item it repeats, and
        naming one that is not in the package is how the old example read."""
        present = {item["item_id"] for item in example["items"]}
        for omission in example["omissions"]:
            for named in re.findall(r"itm_\d{3}", omission["reason"]):
                assert named in present, f"{named} is cited by an omission and is not an item"


#: `tsumugi.<name>/<version>`, the shape of every contract this family passes
#: between programs.
CONTRACT_STRING = re.compile(r"tsumugi\.[a-z-]+/[0-9a-z.-]+")

PUBLISHED = ("context-package.md", "mcp.md", "architecture.md", "README.md")


def _string_literals() -> set[str]:
    """Every string constant in the package, from the syntax tree.

    Not a grep: a contract named only in a comment is a contract the library
    talks about and does not emit, which is precisely the case this is looking
    for.
    """
    source = DOCUMENT.parent.parent / "src" / "tsumugi"
    found: set[str] = set()
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    return found


class TestEveryContractTheDocumentsNameIsOneWeEmit:
    """A version bump written down and not shipped, or shipped and not written
    down, is the same failure from either side.

    sora pins `tsumugi.errors/1-draft` in its own `KNOWN` list and refuses a
    document whose contract it does not recognise -- fail closed, which is
    right, and which turns a one-character drift here into a consumer that
    stops reading anything we send.
    """

    def test_the_scan_finds_the_contracts_it_should(self) -> None:
        """The positive control. A regex that matched nothing, or a source
        walk that found no literals, would make the test below vacuous."""
        named = {
            contract
            for name in PUBLISHED
            for contract in CONTRACT_STRING.findall(
                (DOCUMENT.parent / name).read_text(encoding="utf-8")
            )
        }
        assert "tsumugi.context-package/1" in named
        assert len(named) >= 4, sorted(named)
        assert len(_string_literals()) > 200

    @pytest.mark.parametrize("name", PUBLISHED)
    def test_a_documented_contract_is_a_string_the_source_carries(self, name: str) -> None:
        emitted = _string_literals()
        text = (DOCUMENT.parent / name).read_text(encoding="utf-8")
        for contract in sorted(set(CONTRACT_STRING.findall(text))):
            assert contract in emitted, f"{name} names {contract!r}, which nothing emits"
