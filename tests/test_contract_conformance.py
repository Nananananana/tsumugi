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
from pathlib import Path
from typing import Any

import pytest

jsonschema = pytest.importorskip(
    "jsonschema", reason="the schema check needs jsonschema, a development dependency"
)

from tsumugi.domain.anchor import Anchor  # noqa: E402
from tsumugi.domain.budget import Budget  # noqa: E402
from tsumugi.domain.hashing import ContentHash  # noqa: E402
from tsumugi.domain.omission import Omission, OmissionRule  # noqa: E402
from tsumugi.domain.package import (  # noqa: E402
    BudgetReport,
    ContextPackage,
    PackageProvenance,
    Protection,
    corpus_state,
)
from tsumugi.domain.selection import ContextItem, ItemProvenance, Layer  # noqa: E402
from tsumugi.domain.span import Span  # noqa: E402

from .helpers import build_document  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "context-package-1.json"
DOCUMENT = build_document("notes/budget.md", "予算の単位は呼び出し側で明示する。" * 12)
ERROR = {
    "p50": 0.0495,
    "p95": 0.1828,
    "against": "cl100k_base",
    "dataset": "mixed ja/zh/en/code",
}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


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
                protection=Protection(by="mamori@0.12.0", scope="sess_2f11"),
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
