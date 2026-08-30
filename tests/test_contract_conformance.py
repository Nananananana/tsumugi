"""The conformance suite: does a package satisfy the published contract?

A producer that is not tsumugi passes this same suite. That is the whole point
of writing the contract down (ADR-0002) -- a class other programs can import is
a different kind of object from a document other programs can produce.

The rules, from ``docs/context-package.md``:

  1. the JSON Schema in ``schemas/context-package-1.json``
  2. ``sum(item.cost) <= budget.limit``
  3. every anchor's ``text_hash`` matches its ``text``
  4. every omission names a defined rule and a non-empty reason
  5. the producer run twice yields the same ``package_id``
  6. no ``item.text`` appears in ``omissions``

Rules 1-4 and 6 need only the package. Rules 3 and 5 need the corpus and the
producer, and are checked here against tsumugi as the reference producer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Imported, not `importorskip`ed. `jsonschema` and `pytest` are in the same
# `[dev]` extra, so "this file is running" already means jsonschema is
# installed -- the skip guarded a state that cannot legitimately happen, and
# hid one that can: if jsonschema ever leaves the extra, this whole module
# disappears and CI stays green with no schema validated at all.
#
# The reference producer's self-check is not a thing to lose quietly. It
# caught a real defect the day it was written. Found by `akashi`, which had
# the same shape (#56).
#
# `importorskip` is still right for the sibling adapters: `mamori` lives in a
# separate `siblings` extra and is genuinely optional. The distinction is
# whether the dependency is one this suite is entitled to assume.
import jsonschema
import pytest

from tsumugi import contract_schema, contract_schema_text
from tsumugi.domain.anchor import Anchor
from tsumugi.domain.budget import Budget
from tsumugi.domain.hashing import ContentHash
from tsumugi.domain.omission import Omission, OmissionRule
from tsumugi.domain.package import (
    BudgetReport,
    ContextPackage,
    PackageProvenance,
    Protection,
    corpus_state,
)
from tsumugi.domain.selection import ContextItem, ItemProvenance, Layer
from tsumugi.domain.span import Span

from .helpers import build_document

#: Read through the package's own accessor rather than off the repository
#: floor. The contract is *published inside the wheel* so that a consumer does
#: not need the network to validate a package -- and until this suite read it
#: that way, the promise lived in a comment in `pyproject.toml` and nothing
#: would have noticed it breaking.
SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "tsumugi" / "schemas"
) / "context-package-1.json"

#: The seam fixture, which is also this suite's corpus. One source of truth:
#: a fixture published for consumers and a corpus used to check the producer
#: should not be able to drift apart, because the drift would be invisible on
#: both sides.
SEAM = Path(__file__).resolve().parent.parent / "fixtures" / "seam"
SEAM_CORPUS = SEAM / "corpus"
SEAM_QUESTION = (SEAM / "question.txt").read_text(encoding="utf-8").strip()
SEAM_PACKAGE = SEAM / "context-package.json"
DOCUMENT = build_document("notes/budget.md", "予算の単位は呼び出し側で明示する。" * 12)
ERROR = {
    "p50": 0.0495,
    "p95": 0.1828,
    "against": "cl100k_base",
    "dataset": "mixed ja/zh/en/code",
}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return contract_schema()


def a_package(**overrides: Any) -> ContextPackage:
    """A package exercising every optional field the contract defines."""
    items = (
        ContextItem(
            item_id="itm_001",
            text=DOCUMENT.content[0:30],
            anchor=Anchor.into(DOCUMENT, Span(0, 30)),
            source_path=DOCUMENT.source_path,
            section="Budget",
            cost=40,
        ),
        ContextItem(
            item_id="itm_002",
            text=DOCUMENT.content[40:60],
            anchor=Anchor.into(DOCUMENT, Span(40, 60)),
            source_path=DOCUMENT.source_path,
            provenance=ItemProvenance(
                layer=Layer.INTERPRETATION, producer="kiseki@0.10.0", confidence=0.7
            ),
            cost=25,
        ),
    )
    defaults: dict[str, Any] = {
        "query": "予算について何を決めたか",
        "items": items,
        "omissions": (
            Omission(
                OmissionRule.BUDGET_EXHAUSTED,
                "ranked 7th; 2210 estimated tokens would exceed the limit",
                "doc_77a2",
                Span(0, 2210),
                source_path="notes/archive.md",
                score=0.44,
                cost=2210,
            ),
            Omission(
                OmissionRule.REDUNDANT_CANDIDATE,
                "94% overlap with itm_001; kept the earlier-dated source",
                "doc_11c9",
                Span(300, 480),
                score=0.79,
            ),
        ),
        "budget": BudgetReport(Budget.tokens(8000), 65, "heuristic/cjk-aware@1", ERROR),
        "provenance": PackageProvenance(
            tsumugi_version="0.1.0.dev0",
            corpus_state=corpus_state([DOCUMENT.version]),
            settings_hash=ContentHash.of("settings"),
            providers=("filesystem",),
        ),
        "instructions": {"role": "Answer from the context only.", "rules": ["Quote what you use."]},
        "constraints": {"max_words": 400},
        "output_schema": {"claims": []},
        "created_at": "2026-08-30T11:04:22+09:00",
    }
    defaults.update(overrides)
    return ContextPackage(**defaults)


class TestRuleOneTheSchema:
    def test_the_schema_is_itself_valid(self, schema: dict[str, Any]) -> None:
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_a_reference_package_validates(self, schema: dict[str, Any]) -> None:
        jsonschema.validate(json.loads(a_package().to_json()), schema)

    def test_a_minimal_package_validates(self, schema: dict[str, Any]) -> None:
        minimal = ContextPackage(
            query="q",
            items=(),
            omissions=(),
            budget=BudgetReport(Budget.characters(100), 0, "characters@1"),
            provenance=PackageProvenance(tsumugi_version="0.1.0.dev0"),
        )
        jsonschema.validate(json.loads(minimal.to_json()), schema)

    def test_a_protected_package_validates(self, schema: dict[str, Any]) -> None:
        protected = a_package(
            provenance=PackageProvenance(
                tsumugi_version="0.1.0.dev0",
                protection=Protection(by="mamori@0.12.0", scope="sess_2f11", reversible=True),
            )
        )
        jsonschema.validate(json.loads(protected.to_json()), schema)

    def test_omissions_are_required_by_the_schema(self, schema: dict[str, Any]) -> None:
        # Not optional. A package that dropped eight of eleven documents and
        # said nothing would satisfy a schema that made this optional.
        payload = json.loads(a_package().to_json())
        del payload["omissions"]
        with pytest.raises(jsonschema.ValidationError, match="omissions"):
            jsonschema.validate(payload, schema)

    def test_an_unknown_omission_rule_is_rejected(self, schema: dict[str, Any]) -> None:
        payload = json.loads(a_package().to_json())
        payload["omissions"][0]["rule"] = "because_i_felt_like_it"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, schema)

    def test_an_omission_may_not_carry_text(self, schema: dict[str, Any]) -> None:
        payload = json.loads(a_package().to_json())
        payload["omissions"][0]["text"] = "the passage that was withheld"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, schema)

    def test_an_interpretation_without_confidence_is_rejected(self, schema: dict[str, Any]) -> None:
        payload = json.loads(a_package().to_json())
        del payload["items"][1]["provenance"]["confidence"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, schema)

    def test_a_fact_with_confidence_is_rejected(self, schema: dict[str, Any]) -> None:
        payload = json.loads(a_package().to_json())
        payload["items"][0]["provenance"]["confidence"] = 0.9
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, schema)

    def test_an_anchor_without_a_document_hash_is_rejected(self, schema: dict[str, Any]) -> None:
        # Without it, an anchor into an edited document silently resolves
        # against different text.
        payload = json.loads(a_package().to_json())
        del payload["items"][0]["anchor"]["document_hash"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, schema)

    def test_an_unrecognised_contract_version_is_rejected(self, schema: dict[str, Any]) -> None:
        payload = json.loads(a_package().to_json())
        payload["contract"] = "tsumugi.context-package/2"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, schema)


class TestRuleTwoTheBudget:
    def test_the_items_never_exceed_the_limit(self) -> None:
        payload = json.loads(a_package().to_json())
        assert sum(i["cost"] for i in payload["items"]) <= payload["budget"]["limit"]
        assert sum(i["cost"] for i in payload["items"]) == payload["budget"]["estimate"]

    def test_a_token_estimate_carries_its_error(self) -> None:
        budget = json.loads(a_package().to_json())["budget"]
        assert budget["unit"] == "tokens"
        assert budget["measured_error"]["against"]


class TestRuleThreeTheAnchors:
    def test_every_text_hash_matches_its_text(self) -> None:
        for item in json.loads(a_package().to_json())["items"]:
            assert ContentHash.of(item["text"]) == ContentHash.parse(item["anchor"]["text_hash"])

    def test_every_anchor_resolves_in_the_corpus_it_names(self) -> None:
        for item in json.loads(a_package().to_json())["items"]:
            anchor = item["anchor"]
            assert DOCUMENT.content[anchor["start"] : anchor["end"]] == item["text"]

    def test_the_span_length_matches_the_text(self) -> None:
        for item in json.loads(a_package().to_json())["items"]:
            span = item["anchor"]
            assert span["end"] - span["start"] == len(item["text"])


class TestRuleFourTheOmissions:
    def test_every_rule_is_one_the_contract_defines(self) -> None:
        for omission in json.loads(a_package().to_json())["omissions"]:
            OmissionRule.parse(omission["rule"])

    def test_every_reason_is_prose_and_not_empty(self) -> None:
        for omission in json.loads(a_package().to_json())["omissions"]:
            assert omission["reason"].strip()
            # Naming the rule is not explaining the decision.
            assert omission["reason"] != omission["rule"]


class TestRuleFiveReproducibility:
    def test_the_producer_run_twice_yields_the_same_id(self) -> None:
        assert a_package().package_id == a_package().package_id

    def test_and_byte_identical_output_apart_from_the_timestamp(self) -> None:
        first = json.loads(a_package(created_at="2026-01-01T00:00:00Z").to_json())
        second = json.loads(a_package(created_at="2026-12-31T23:59:59Z").to_json())
        first.pop("created_at")
        second.pop("created_at")
        assert first == second


class TestRuleSixNoLeakage:
    def test_no_item_text_appears_anywhere_in_the_omissions(self) -> None:
        payload = json.loads(a_package().to_json())
        rendered = json.dumps(payload["omissions"], ensure_ascii=False)
        for item in payload["items"]:
            assert item["text"] not in rendered


class TestTheSchemaAndTheCodeAgree:
    def test_every_omission_rule_in_the_code_is_in_the_schema(self, schema: dict[str, Any]) -> None:
        # The two representations have to be kept in step by hand -- there is
        # no pydantic to derive one from the other (ADR-0001, ADR-0002). This
        # test is the only thing standing between them.
        published = set(schema["$defs"]["omission"]["properties"]["rule"]["enum"])
        assert {rule.value for rule in OmissionRule} == published

    def test_every_layer_in_the_code_is_in_the_schema(self, schema: dict[str, Any]) -> None:
        published = set(
            schema["$defs"]["item"]["properties"]["provenance"]["properties"]["layer"]["enum"]
        )
        assert {layer.value for layer in Layer} == published

    def test_every_budget_unit_in_the_code_is_in_the_schema(self, schema: dict[str, Any]) -> None:
        from tsumugi.domain.budget import Unit

        published = set(schema["$defs"]["budget"]["properties"]["unit"]["enum"])
        assert {unit.value for unit in Unit} == published


class TestTheProducersOwnOutput:
    """The contract, validated against what the producer actually emits.

    Everything above this validates packages a *test* built. That checks the
    schema against the dataclasses, which is worth doing and is not the same
    thing: the schema's own description calls tsumugi the reference producer,
    and a reference producer whose real output has never been validated is a
    claim rather than a reference.

    So: a corpus on disk, ingested, through ``build_context`` and through the
    CLI, in every shape the pipeline emits.
    """

    def _corpus(self, tmp_path: Path) -> tuple[Any, Any, Any]:
        from tsumugi.application.ingest import ingest_paths
        from tsumugi.infrastructure.index.fts import FtsIndex
        from tsumugi.infrastructure.parsers import parser_for
        from tsumugi.infrastructure.storage.database import connect
        from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

        connection = connect(tmp_path / "index.db")
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        ingest_paths(
            sorted(SEAM_CORPUS.glob("*.md")),
            root=SEAM_CORPUS,
            store=store,
            index=index,
            parser_for=parser_for,
        )
        return store, index, connection

    @pytest.mark.parametrize(
        ("budget_text", "shape"),
        [
            ("characters:4000", "room for everything"),
            # Tight enough to force omissions, which are the half of the
            # contract a producer is most likely to emit wrongly.
            ("characters:60", "budget exhausted"),
            # A token budget carries measured_error, its own subtree.
            ("tokens:2000", "an estimate with its error"),
        ],
    )
    def test_a_built_package_validates(
        self, tmp_path: Path, schema: dict[str, Any], budget_text: str, shape: str
    ) -> None:
        from tsumugi.application.build_context import build_context
        from tsumugi.domain.budget import Budget, Unit
        from tsumugi.infrastructure.cost.heuristic import CharacterCost, HeuristicTokenCost

        store, index, connection = self._corpus(tmp_path)
        budget = Budget.parse(budget_text)
        cost = HeuristicTokenCost() if budget.unit is Unit.TOKENS else CharacterCost()
        package = build_context(
            SEAM_QUESTION,
            store=store,
            index=index,
            cost_model=cost,
            budget=budget,
            version="0.1.0.dev0",
        )
        jsonschema.validate(json.loads(package.to_json()), schema)
        assert package.items or package.omissions, shape
        connection.close()

    def test_the_answering_shape_validates(self, tmp_path: Path, schema: dict[str, Any]) -> None:
        # `ask` builds with a different instruction set and an output_schema
        # (ADR-0017), so it is a different document over the same corpus.
        from tsumugi.application.build_context import build_context
        from tsumugi.application.instructions import ANSWER_SCHEMA, ANSWERING
        from tsumugi.domain.budget import Budget
        from tsumugi.infrastructure.cost.heuristic import CharacterCost

        store, index, connection = self._corpus(tmp_path)
        package = build_context(
            SEAM_QUESTION,
            store=store,
            index=index,
            cost_model=CharacterCost(),
            budget=Budget.characters(4000),
            instructions=ANSWERING,
            output_schema=ANSWER_SCHEMA,
        )
        jsonschema.validate(json.loads(package.to_json()), schema)
        connection.close()

    def test_a_protected_built_package_validates(
        self, tmp_path: Path, schema: dict[str, Any]
    ) -> None:
        from dataclasses import replace as replace_field

        from tsumugi.application.build_context import build_context
        from tsumugi.domain.budget import Budget
        from tsumugi.domain.package import Protection
        from tsumugi.infrastructure.cost.heuristic import CharacterCost

        store, index, connection = self._corpus(tmp_path)
        package = build_context(
            SEAM_QUESTION,
            store=store,
            index=index,
            cost_model=CharacterCost(),
            budget=Budget.characters(4000),
        )
        protected = replace_field(
            package,
            provenance=replace_field(
                package.provenance,
                protection=Protection(by="mamori@0.22.0", scope="session-1", reversible=True),
            ),
        )
        jsonschema.validate(json.loads(protected.to_json()), schema)
        connection.close()

    def test_the_cli_emits_a_conforming_document(
        self, tmp_path: Path, schema: dict[str, Any], capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Through `main`, because the bytes a consumer receives come out of the
        # CLI and not out of a Python object. A serialisation that were right
        # in-process and wrong on stdout would be wrong where it counts.
        from tsumugi.interfaces.cli.main import main

        index_path = tmp_path / "index.db"
        assert main(["--index", str(index_path), "ingest", str(SEAM_CORPUS)]) == 0
        capsys.readouterr()
        assert main(["--index", str(index_path), "context", SEAM_QUESTION, "--json"]) == 0

        emitted = json.loads(capsys.readouterr().out)
        jsonschema.validate(emitted, schema)
        assert emitted["contract"] == "tsumugi.context-package/1"


class TestTheSeamFixture:
    """The published fixture, checked against the published schema.

    `fixtures/seam/` is vendored by consumers that test against tsumugi's
    output without importing tsumugi. A fixture that had drifted from its
    producer would be worse than none, because it would look like agreement --
    so the drift fails here, in this repository, before it reaches anyone.
    """

    def test_the_fixture_validates(self, schema: dict[str, Any]) -> None:
        jsonschema.validate(json.loads(SEAM_PACKAGE.read_text(encoding="utf-8")), schema)

    def test_it_is_what_the_producer_emits_today(self) -> None:
        # Regenerate with `python tools/make_seam_fixture.py` and commit the
        # diff. This failing means the producer changed, which is allowed --
        # silently shipping a stale fixture is not.
        sys.path.insert(0, str(SEAM.parents[1] / "tools"))
        import make_seam_fixture

        assert make_seam_fixture.build() + "\n" == SEAM_PACKAGE.read_text(encoding="utf-8")

    def test_it_carries_an_omission_and_an_item(self) -> None:
        # Both halves. `omissions[]` is the part a consumer is most likely to
        # get wrong, and a fixture that never shows one never tests it.
        payload = json.loads(SEAM_PACKAGE.read_text(encoding="utf-8"))
        assert payload["items"], "nothing to cite"
        assert payload["omissions"], "nothing left out, so nothing to account for"

    def test_created_at_is_pinned_and_the_id_is_still_real(self) -> None:
        # The one field outside package_id (ADR-0003), which is what makes
        # pinning it safe rather than a lie.
        payload = json.loads(SEAM_PACKAGE.read_text(encoding="utf-8"))
        assert payload["created_at"] == "2026-08-30T00:00:00+00:00"
        rebuilt = ContextPackage.from_json(SEAM_PACKAGE.read_text(encoding="utf-8"))
        assert str(rebuilt.package_id) == payload["package_id"]


class TestTheCounterExamples:
    """What the schema refuses, and the one thing it cannot."""

    def test_a_protection_missing_a_field_is_rejected(self, schema: dict[str, Any]) -> None:
        # All three are required. A protection naming its redactor but not
        # whether it can be undone would leave a verifier unable to tell
        # "unknown" from "false", which is the distinction ADR-0009 is for.
        payload = json.loads(a_package().to_json())
        payload["provenance"]["protection"] = {"by": "mamori@0.22.0", "scope": "session-1"}
        with pytest.raises(jsonschema.ValidationError, match="reversible"):
            jsonschema.validate(payload, schema)

    def test_an_unknown_field_on_a_protection_is_rejected(self, schema: dict[str, Any]) -> None:
        payload = json.loads(a_package().to_json())
        payload["provenance"]["protection"] = {
            "by": "mamori@0.22.0",
            "scope": "session-1",
            "reversible": True,
            "restorer_url": "https://example.com/restore",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, schema)

    def test_the_schema_cannot_say_that_a_span_ends_after_it_starts(
        self, schema: dict[str, Any]
    ) -> None:
        """And a consumer has to know that, which is why this is a test.

        JSON Schema 2020-12 cannot compare two properties of the same object,
        so ``end >= start`` is not expressible. A document with the two
        reversed **validates**, and a consumer that slices text with them must
        check for itself.

        The producer cannot emit one -- ``Span`` refuses at construction --
        so the invariant lives there. This test is what makes the division of
        labour explicit instead of assumed, and it is the honest answer to
        "does the schema reject an anchor whose end precedes its start": no,
        and it never will.
        """
        payload = json.loads(a_package().to_json())
        payload["items"][0]["anchor"]["start"] = 30
        payload["items"][0]["anchor"]["end"] = 10
        jsonschema.validate(payload, schema)  # accepted, and that is the point

    def test_the_producer_refuses_to_build_one(self) -> None:
        with pytest.raises(ValueError, match="ends before it starts"):
            Span(30, 10)


class TestAProtectionIsIrreversibleUntilItSaysOtherwise:
    """ADR-0020. The default decides what a false record costs.

    Wrong in the `True` direction reports honest citations as *unsupported* --
    a false accusation, and a silent one, because the output looks exactly
    like a correctly-caught fabrication. Wrong in the `False` direction
    reports everything as *unverifiable*, with its reason attached: useless,
    obvious, and fixed by passing the right value.
    """

    def test_the_default_is_irreversible(self) -> None:
        assert Protection(by="something@1", scope="s").reversible is False

    def test_a_document_missing_the_field_reads_as_irreversible(self) -> None:
        # Already non-conforming -- the schema requires it -- so all this
        # decides is how loudly a malformed input fails.
        payload = json.loads(a_package().to_json())
        payload["provenance"]["protection"] = {"by": "mamori@0.22.0", "scope": "s"}
        del payload["package_id"]  # it no longer describes this payload
        protection = ContextPackage.from_json(json.dumps(payload)).provenance.protection
        assert protection is not None
        assert protection.reversible is False

    def test_the_wire_is_unchanged(self, schema: dict[str, Any]) -> None:
        # The field was always required, so no emitted document differs. This
        # decision is about defaults in memory, not about the contract.
        assert schema["$defs"]["provenance"]["properties"]["protection"]["required"] == [
            "by",
            "scope",
            "reversible",
        ]

    @pytest.mark.parametrize("value", ["false", "true", 0, 1, "no", None, []])
    def test_a_value_that_is_merely_truthy_is_refused(self, value: object) -> None:
        """Found by `akashi`, which hit the same shape in its own reader.

        `verify` branches on `not reversible`, so the string `"false"` --
        which is what a producer that stringified its JSON sends -- is
        *truthy* and takes the restore path. A package that cannot be restored
        then reports its honest citations as fabrications, which is the exact
        failure ADR-0020 is about, arriving through the type system instead of
        through a default.

        `0` and `1` are refused too, though they would land on the right
        branch by accident. A producer sending them is not conforming, and
        accepting them teaches that the field is loosely typed -- which is how
        `"false"` arrives next.
        """
        with pytest.raises(ValueError, match="true or false"):
            Protection(by="mamori@0.22.0", scope="s", reversible=value)  # type: ignore[arg-type]

    def test_a_document_carrying_a_string_is_refused_on_read(self) -> None:
        # The realistic route in: a non-conforming producer, read by a
        # consumer that does not validate against the schema first.
        payload = json.loads(a_package().to_json())
        payload["provenance"]["protection"] = {
            "by": "mamori@0.22.0",
            "scope": "s",
            "reversible": "false",
        }
        del payload["package_id"]
        with pytest.raises(ValueError, match="true or false"):
            ContextPackage.from_json(json.dumps(payload))


class TestTheContractShipsWithTheLibrary:
    """`pyproject.toml` promised this and no code checked it.

    The schema is packaged so that a consumer validating a package does not
    have to fetch anything from the internet. That promise lived in a build
    comment: nothing imported the packaged copy, so removing the line that
    shipped it would have broken the promise permanently, silently, with every
    test still green. Found by `musubi`, which hit the same shape.

    It has an API now, and these are the tests that make the API load-bearing.
    """

    def test_it_is_readable_from_the_installed_package(self) -> None:
        # `importlib.resources`, not a path relative to this file. A test that
        # walks up to the repository root passes in a checkout and proves
        # nothing about a wheel.
        assert contract_schema()["$defs"]["item"]["type"] == "object"

    def test_the_bytes_are_available_too(self) -> None:
        # For a caller that wants to hash it, vendor it, or hand it to a
        # validator written in another language.
        assert json.loads(contract_schema_text()) == contract_schema()

    def test_the_packaged_copy_is_the_one_this_suite_validates_against(self) -> None:
        # One copy. Two would drift, and the drift would be invisible: the
        # tests would keep passing against the one in the repository while
        # consumers got the other.
        assert contract_schema_text() == SCHEMA_PATH.read_text(encoding="utf-8")

    def test_an_unknown_schema_name_fails_rather_than_returning_nothing(self) -> None:
        with pytest.raises(FileNotFoundError):
            contract_schema("context-package-99.json")


class TestThisSuiteCannotVanish:
    """The suite that checks the contract must not be skippable.

    It was. `pytest.importorskip("jsonschema")` meant that if jsonschema ever
    left the `[dev]` extra, this whole module would disappear and CI would
    stay green having validated no package against the published schema. The
    reference producer's self-check is not a thing to lose quietly -- it
    caught a real defect the day it was written.

    Found by `akashi`, which had the same shape and fixed it in #56.
    """

    def test_jsonschema_is_imported_and_not_skipped(self) -> None:
        # The call, not the word: this file discusses `importorskip` at
        # length, and a substring check would fail on its own prose.
        import ast

        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        skipped = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "importorskip"
        ]
        assert not skipped, "this suite must not be able to skip itself away"
        assert any(
            isinstance(node, ast.Import) and any(a.name == "jsonschema" for a in node.names)
            for node in ast.walk(tree)
        )

    def test_it_is_declared_where_pytest_is(self) -> None:
        # The reason a plain import is safe: this file running already means
        # the extra is installed. If jsonschema were ever moved to an extra of
        # its own, the plain import would start failing for people who have
        # pytest -- which is the loud version of the failure, and the right
        # place to have this conversation again.
        pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        dev = pyproject.split("dev = [", 1)[1].split("]", 1)[0]
        assert "pytest" in dev and "jsonschema" in dev

    def test_an_optional_sibling_may_still_be_skipped(self) -> None:
        # The distinction: `mamori` lives in a separate `siblings` extra and is
        # genuinely optional, so skipping is right there. What matters is
        # whether this suite is entitled to assume the dependency.
        adapter = (Path(__file__).resolve().parent / "test_adapter_mamori.py").read_text(
            encoding="utf-8"
        )
        assert "importorskip" in adapter


def test_the_contract_cannot_be_extended_and_says_so() -> None:
    """The compatibility promise matches what the schema enforces.

    For the whole life of the contract the schema said *a field may be added*
    while every object set ``additionalProperties: false``. A consumer
    validating against the published schema rejected the extension the same
    document told them to expect, in all three directions -- a new top-level
    field, a new field on an item, a new value in the omission ``rule`` enum.

    That could not be repaired by relaxing the schema, because consumers who
    vendored the strict copy still hold it, so ADR-0022 corrected the wording
    instead: v1 is closed, and evolution means ``/2``. This holds the two
    together. If ``additionalProperties`` is ever relaxed, the sentence has to
    move with it.
    """
    schema = contract_schema()
    assert "closed" in schema["properties"]["contract"]["description"]

    strict = [schema, *(schema["$defs"][name] for name in schema["$defs"])]
    open_objects = [
        obj.get("title", obj.get("description", "?"))[:40]
        for obj in strict
        if obj.get("type") == "object" and obj.get("additionalProperties") is not False
    ]
    assert not open_objects, (
        f"these accept unknown fields while the contract says nothing may be added: {open_objects}"
    )
