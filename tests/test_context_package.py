"""The ContextPackage contract, and the invariants it refuses to break.

The theme of the whole file: a package that cannot be built is better than one
built wrong and discovered later by a consumer with no way to tell.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tsumugi.domain.anchor import Anchor
from tsumugi.domain.assembly import CORPUS_WIDE, Candidate, fit_to_budget
from tsumugi.domain.budget import Budget
from tsumugi.domain.hashing import ContentHash
from tsumugi.domain.omission import Omission, OmissionRule
from tsumugi.domain.package import (
    CONTRACT,
    BudgetReport,
    ContextPackage,
    PackageProvenance,
    Protection,
    corpus_state,
)
from tsumugi.domain.selection import ContextItem, ItemProvenance, Layer, SelectionTrace
from tsumugi.domain.span import Span

from .helpers import build_document

# Deliberately non-repeating. An earlier version of this fixture was one
# sentence repeated twelve times, which made every pair of spans a genuine
# 100% duplicate and quietly turned the budget tests into redundancy tests.
DOCUMENT = build_document(
    "notes/budget.md",
    "予算の単位は呼び出し側で明示する。トークンは推定であり誤差を申告する。"
    "文字数とバイト数は正確に数える。索引は候補を出し、確認が結果を決める。"
    "証拠は原文の位置と一致し、編集後は古いものとして報告する。"
    "落としたものには規則と理由がつく。台帳は本文を持たない。"
    "契約は文書であって型ではない。エージェントには読み取り専用の口を開ける。",
)
ERROR = {"p50": 0.05, "p95": 0.18, "against": "cl100k_base", "dataset": "x"}


def item(name: str, start: int, end: int, cost: int = 10) -> ContextItem:
    return ContextItem(
        item_id=name,
        text=DOCUMENT.content[start:end],
        anchor=Anchor.into(DOCUMENT, Span(start, end)),
        source_path=DOCUMENT.source_path,
        cost=cost,
    )


def package(**overrides: Any) -> ContextPackage:
    items = overrides.pop("items", (item("itm_001", 0, 20),))
    spent = sum(i.cost for i in items)
    defaults: dict[str, Any] = {
        "query": "予算について何を決めたか",
        "items": items,
        "omissions": (),
        "budget": BudgetReport(Budget.characters(1000), spent, "characters@1"),
        "provenance": PackageProvenance(tsumugi_version="0.1.0.dev0"),
    }
    defaults.update(overrides)
    return ContextPackage(**defaults)


class TestTheContract:
    def test_a_package_names_its_contract(self) -> None:
        assert package().contract == CONTRACT

    def test_an_unrecognised_contract_is_refused(self) -> None:
        # Fail closed. A consumer that cannot verify the shape refuses it
        # rather than guessing.
        with pytest.raises(ValueError, match="unrecognised contract"):
            package(contract="tsumugi.context-package/99")

    def test_a_package_with_no_query_is_refused(self) -> None:
        with pytest.raises(ValueError, match="answers nothing"):
            package(query="   ")

    def test_duplicate_item_ids_are_refused(self) -> None:
        with pytest.raises(ValueError, match="duplicate item id"):
            package(items=(item("itm_001", 0, 20), item("itm_001", 30, 50)))


class TestTheBudgetInvariant:
    def test_a_package_cannot_exceed_its_own_budget(self) -> None:
        with pytest.raises(ValueError, match="over its own budget"):
            BudgetReport(Budget.characters(10), 500, "characters@1")

    def test_the_items_have_to_add_up_to_the_estimate(self) -> None:
        # A budget that does not add up cannot be checked by anyone.
        with pytest.raises(ValueError, match="does not add up"):
            package(budget=BudgetReport(Budget.characters(1000), 999, "characters@1"))

    def test_a_token_budget_without_a_measured_error_is_refused(self) -> None:
        # The rule of ADR-0006: an estimate that does not say how wrong it is
        # will mislead a caller exactly once, expensively.
        with pytest.raises(ValueError, match="measured error"):
            BudgetReport(Budget.tokens(1000), 10, "heuristic/cjk-aware@1")

    def test_a_token_budget_with_one_is_fine(self) -> None:
        report = BudgetReport(Budget.tokens(1000), 10, "heuristic/cjk-aware@1", ERROR)
        assert report.measured_error == ERROR

    def test_an_exact_unit_needs_no_error(self) -> None:
        BudgetReport(Budget.characters(1000), 10, "characters@1")
        BudgetReport(Budget.bytes(1000), 10, "bytes/utf-8@1")


class TestItems:
    def test_an_item_whose_text_does_not_match_its_anchor_is_refused(self) -> None:
        # The invariant that makes an item evidence rather than a snippet.
        with pytest.raises(ValueError, match="anchor covers"):
            ContextItem(
                item_id="itm_001",
                text="a much longer piece of text than the anchor covers",
                anchor=Anchor.into(DOCUMENT, Span(0, 4)),
            )

    def test_an_empty_item_is_refused(self) -> None:
        with pytest.raises(ValueError, match="carries no text"):
            ContextItem(item_id="itm_001", text="", anchor=Anchor.into(DOCUMENT, Span(0, 0)))

    def test_an_interpretation_must_carry_confidence(self) -> None:
        # kiseki's layering survives the crossing: an interpretation with no
        # confidence is an opinion wearing a fact's clothes.
        with pytest.raises(ValueError, match="must carry a confidence"):
            ItemProvenance(layer=Layer.INTERPRETATION, producer="kiseki@0.10.0")

    def test_a_fact_may_not_carry_confidence(self) -> None:
        with pytest.raises(ValueError, match="does not carry confidence"):
            ItemProvenance(layer=Layer.FACT, producer="tsumugi.ingest/1", confidence=0.7)

    def test_an_interpretation_with_confidence_is_fine(self) -> None:
        provenance = ItemProvenance(
            layer=Layer.INTERPRETATION, producer="kiseki@0.10.0", confidence=0.7
        )
        assert provenance.confidence == 0.7

    def test_a_rank_starts_at_one(self) -> None:
        with pytest.raises(ValueError, match="ranks start at 1"):
            SelectionTrace(rank=0, score=1.0)


class TestOmissions:
    def test_an_omission_must_say_why(self) -> None:
        # Naming the rule is not the same as explaining the decision.
        with pytest.raises(ValueError, match="explains nothing"):
            Omission(OmissionRule.BUDGET_EXHAUSTED, "  ", "doc_1", Span(0, 10))

    def test_an_unknown_rule_names_the_ones_that_exist(self) -> None:
        with pytest.raises(ValueError, match="budget_exhausted"):
            OmissionRule.parse("because_i_felt_like_it")

    def test_a_passage_cannot_be_both_sent_and_withheld(self) -> None:
        sent = item("itm_001", 0, 20)
        with pytest.raises(ValueError, match="both send and withhold"):
            package(
                items=(sent,),
                omissions=(
                    Omission(
                        OmissionRule.BUDGET_EXHAUSTED,
                        "did not fit",
                        sent.anchor.document_id,
                        sent.anchor.span,
                    ),
                ),
            )

    def test_why_not_groups_by_rule(self) -> None:
        built = package(
            omissions=(
                Omission(OmissionRule.BUDGET_EXHAUSTED, "no room", "doc_2", Span(0, 5)),
                Omission(OmissionRule.BELOW_THRESHOLD, "scored 0.02", "doc_3", Span(0, 5)),
            )
        )
        report = built.why_not()
        assert "budget_exhausted" in report
        assert "below_threshold" in report
        assert "2 candidates" in report

    def test_why_not_says_so_when_nothing_was_dropped(self) -> None:
        assert "Nothing was considered" in package().why_not()


class TestReproducibility:
    def test_the_same_inputs_produce_the_same_id(self) -> None:
        assert package().package_id == package().package_id

    def test_the_timestamp_does_not_change_the_id(self) -> None:
        # The only reason a timestamp is allowed in the document at all.
        early = package(created_at="2026-08-30T09:00:00+09:00")
        late = package(created_at="2026-12-25T23:59:59+09:00")
        assert early.package_id == late.package_id

    def test_a_different_query_produces_a_different_id(self) -> None:
        assert package().package_id != package(query="something else entirely").package_id

    def test_a_different_estimator_produces_a_different_id(self) -> None:
        # A change to the estimator changes every budget decision, so it has to
        # change the id (ADR-0003, ADR-0006).
        other = BudgetReport(Budget.characters(1000), 10, "characters@2")
        assert package().package_id != package(budget=other).package_id

    def test_the_corpus_state_does_not_depend_on_ordering(self) -> None:
        versions = [ContentHash.of("a"), ContentHash.of("b"), ContentHash.of("c")]
        assert corpus_state(versions) == corpus_state(list(reversed(versions)))

    def test_serialization_is_stable(self) -> None:
        assert package().to_json() == package().to_json()


class TestSerialization:
    def test_it_produces_the_contract_shape(self) -> None:
        payload = json.loads(package().to_json())
        assert set(payload) >= {
            "contract",
            "package_id",
            "query",
            "items",
            "omissions",
            "budget",
            "provenance",
        }

    def test_an_item_carries_its_anchor_and_both_hashes(self) -> None:
        anchor = json.loads(package().to_json())["items"][0]["anchor"]
        assert set(anchor) >= {"document_id", "start", "end", "text_hash", "document_hash"}

    def test_an_omission_never_carries_the_omitted_text(self) -> None:
        # Copying what was deliberately not sent into the thing being sent
        # would defeat the point.
        built = package(
            omissions=(
                Omission(
                    OmissionRule.BUDGET_EXHAUSTED,
                    "no room",
                    "doc_2",
                    Span(0, 5),
                    source_path="notes/secret.md",
                ),
            )
        )
        entry = json.loads(built.to_json())["omissions"][0]
        assert "text" not in entry
        assert set(entry) <= {"rule", "reason", "anchor", "score", "cost"}

    def test_protection_is_null_until_something_redacts(self) -> None:
        assert json.loads(package().to_json())["provenance"]["protection"] is None

    def test_protection_names_the_redactor_and_the_scope(self) -> None:
        # The field that lets a verifier fail loudly instead of reporting every
        # honest citation as unsupported (ADR-0009).
        built = package(
            provenance=PackageProvenance(
                tsumugi_version="0.1.0.dev0",
                protection=Protection(by="mamori@0.12.0", scope="sess_2f11", reversible=True),
            )
        )
        recorded = json.loads(built.to_json())["provenance"]["protection"]
        assert recorded == {"by": "mamori@0.12.0", "scope": "sess_2f11", "reversible": True}

    def test_a_protection_record_must_name_both(self) -> None:
        with pytest.raises(ValueError, match="must name"):
            Protection(by="mamori@0.12.0", scope="")


class TestRendering:
    def test_it_renders_named_sections(self) -> None:
        built = package(
            instructions={
                "role": "Answer from the context only.",
                "rules": ["Quote what you use."],
            },
            constraints={"max_words": 400},
            output_schema={"claims": []},
        )
        rendered = built.render()
        for section in ("# SYSTEM", "# TASK", "# CONTEXT", "# CONSTRAINTS", "# OUTPUT_SCHEMA"):
            assert section in rendered

    def test_the_model_is_told_the_selection_has_edges(self) -> None:
        # It cannot see them otherwise, and will answer with the confidence of
        # complete information (ADR-0005).
        built = package(
            omissions=(Omission(OmissionRule.BUDGET_EXHAUSTED, "no room", "doc_2", Span(0, 5)),)
        )
        rendered = built.render()
        assert "# NOT INCLUDED" in rendered
        assert "complete" in rendered

    def test_no_such_section_when_nothing_was_dropped(self) -> None:
        assert "# NOT INCLUDED" not in package().render()

    def test_an_interpretation_is_labelled_in_the_prompt(self) -> None:
        # It must not read as a fact just because it crossed a boundary.
        interpreted = ContextItem(
            item_id="itm_001",
            text=DOCUMENT.content[0:20],
            anchor=Anchor.into(DOCUMENT, Span(0, 20)),
            provenance=ItemProvenance(
                layer=Layer.INTERPRETATION, producer="kiseki@0.10.0", confidence=0.7
            ),
            cost=10,
        )
        rendered = package(items=(interpreted,)).render()
        assert "interpretation" in rendered
        assert "0.7" in rendered


class TestFittingToBudget:
    def _candidate(self, start: int, end: int, score: float) -> Candidate:
        return Candidate(
            text=DOCUMENT.content[start:end],
            anchor=Anchor.into(DOCUMENT, Span(start, end)),
            score=score,
            source_path=DOCUMENT.source_path,
            signals=("term_density",),
        )

    def test_everything_that_fits_is_included(self) -> None:
        fitted = fit_to_budget(
            [self._candidate(0, 10, 0.9), self._candidate(20, 30, 0.8)],
            budget=Budget.characters(100),
            cost_of=len,
        )
        assert len(fitted.items) == 2
        assert fitted.omissions == ()

    def test_what_does_not_fit_becomes_an_omission_with_the_budget_rule(self) -> None:
        fitted = fit_to_budget(
            [self._candidate(0, 10, 0.9), self._candidate(60, 100, 0.8)],
            budget=Budget.characters(15),
            cost_of=len,
        )
        assert len(fitted.items) == 1
        assert fitted.omissions[0].rule is OmissionRule.BUDGET_EXHAUSTED
        assert "would exceed the limit" in fitted.omissions[0].reason

    def test_a_low_scorer_is_omitted_under_the_threshold_rule(self) -> None:
        fitted = fit_to_budget(
            [self._candidate(0, 10, 0.9), self._candidate(20, 30, 0.01)],
            budget=Budget.characters(1000),
            cost_of=len,
            minimum_score=0.5,
        )
        assert fitted.omissions[0].rule is OmissionRule.BELOW_THRESHOLD
        assert "below the floor" in fitted.omissions[0].reason

    def test_a_cap_is_always_reported(self) -> None:
        # A cap the package does not mention is indistinguishable from having
        # considered everything.
        fitted = fit_to_budget(
            [self._candidate(0, 10, 0.9)],
            budget=Budget.characters(1000),
            cost_of=len,
            truncated_at=50,
        )
        capped = [o for o in fitted.omissions if o.rule is OmissionRule.TRUNCATED_BY_CAP]
        assert len(capped) == 1
        assert capped[0].document_id == CORPUS_WIDE
        assert "cap of 50" in capped[0].reason

    def test_one_oversized_candidate_does_not_stop_the_fill(self) -> None:
        # Stopping at the first overflow would silently prefer long passages
        # over short relevant ones.
        fitted = fit_to_budget(
            [self._candidate(0, 60, 0.9), self._candidate(80, 90, 0.8)],
            budget=Budget.characters(20),
            cost_of=len,
        )
        assert len(fitted.items) == 1
        assert fitted.items[0].anchor.span.start == 80

    def test_a_disqualified_candidate_carries_its_own_reason(self) -> None:
        stale = Candidate(
            text=DOCUMENT.content[0:10],
            anchor=Anchor.into(DOCUMENT, Span(0, 10)),
            score=0.9,
            disqualified=(OmissionRule.STALE_ANCHOR, "the document changed since May"),
        )
        fitted = fit_to_budget([stale], budget=Budget.characters(1000), cost_of=len)
        assert fitted.items == ()
        assert fitted.omissions[0].rule is OmissionRule.STALE_ANCHOR

    def test_fitting_is_deterministic(self) -> None:
        candidates = [self._candidate(i * 10, i * 10 + 8, 0.5) for i in range(6)]
        first = fit_to_budget(candidates, budget=Budget.characters(20), cost_of=len)
        second = fit_to_budget(
            list(reversed(candidates)), budget=Budget.characters(20), cost_of=len
        )
        assert [i.anchor for i in first.items] == [i.anchor for i in second.items]

    @given(
        scores=st.lists(st.floats(0.0, 1.0, allow_nan=False), min_size=1, max_size=12),
        limit=st.integers(min_value=1, max_value=200),
    )
    def test_every_candidate_leaves_as_an_item_or_an_omission(
        self, scores: list[float], limit: int
    ) -> None:
        # The rule the module exists to enforce. A candidate that vanished
        # between the two lists is a silent truncation.
        candidates = [self._candidate(i * 12, i * 12 + 9, s) for i, s in enumerate(scores)]
        fitted = fit_to_budget(
            candidates, budget=Budget.characters(limit), cost_of=len, minimum_score=0.3
        )
        assert fitted.accounts_for(len(candidates))

    @given(
        scores=st.lists(st.floats(0.0, 1.0, allow_nan=False), min_size=1, max_size=12),
        limit=st.integers(min_value=1, max_value=200),
    )
    def test_the_spend_never_exceeds_the_budget(self, scores: list[float], limit: int) -> None:
        candidates = [self._candidate(i * 12, i * 12 + 9, s) for i, s in enumerate(scores)]
        fitted = fit_to_budget(candidates, budget=Budget.characters(limit), cost_of=len)
        assert fitted.spent <= limit
        assert sum(i.cost for i in fitted.items) == fitted.spent


class TestTheRenderedPromptCarriesTheMarking:
    """ADR-0008 marks redundancy and never removes it. Marked *where*?

    Until now, only in the JSON. The one party that could act on a "this
    repeats c1" note -- the model reading the prompt -- was the one party
    never told. Marking a consumer cannot see is not marking.
    """

    def _package(self, *signals: str) -> ContextPackage:
        item = ContextItem(
            item_id="itm_002",
            text="the tent weighs 2.4kg",
            anchor=Anchor(
                document_id="doc_1",
                span=Span(0, 21),
                text_hash=ContentHash.of("the tent weighs 2.4kg"),
                version=ContentHash.of("the tent weighs 2.4kg"),
            ),
            source_path="notes/copy.md",
            cost=21,
            selection=SelectionTrace(rank=1, score=1.0, signals=signals),
        )
        return ContextPackage(
            query="how heavy is the tent",
            items=(item,),
            omissions=(),
            budget=BudgetReport(budget=Budget.characters(100), estimate=21, estimator="chars"),
            provenance=PackageProvenance(tsumugi_version="test"),
        )

    def test_a_duplicate_says_what_it_repeats(self) -> None:
        rendered = self._package("lexical_match", "redundant_with:itm_001").render()
        assert "repeats itm_001" in rendered

    def test_retrieval_signals_stay_out_of_the_prompt(self) -> None:
        # `lexical_match` and friends are how the ranker explains itself to a
        # reader of the document. They are noise in a prompt, and noise in a
        # prompt is budget spent on nothing.
        rendered = self._package("lexical_match", "heading_match").render()
        assert "lexical_match" not in rendered
        assert "repeats" not in rendered

    def test_the_marking_is_read_from_the_signal_rather_than_stored_twice(self) -> None:
        # One source of truth: the signal is what assembly produced and what
        # the published document carries.
        package = self._package("redundant_with:itm_001")
        assert "redundant_with:itm_001" in package.to_dict()["items"][0]["selection"]["signals"]
