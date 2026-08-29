"""Budgets name their unit, and token estimates admit they are estimates.

The failure this file guards against is a caller reading `tokens: 7412` as a
count. It is not one, and every path that could let it look like one is closed
here (ADR-0006).
"""

from __future__ import annotations

import pytest

from tsumugi.domain.budget import Budget, Unit
from tsumugi.infrastructure.cost.heuristic import ByteCost, CharacterCost, HeuristicTokenCost
from tsumugi.infrastructure.cost.scripts import ScriptClass, classify, profile
from tsumugi.ports.cost import CostModel, MeasuredError

JAPANESE = "東京の会議は明日です。テントは 2.4kg。"
ENGLISH = "The unit is explicit at the call site."


class TestBudget:
    def test_each_unit_has_a_constructor(self) -> None:
        assert Budget.tokens(8000).unit is Unit.TOKENS
        assert Budget.characters(20000).unit is Unit.CHARACTERS
        assert Budget.bytes(65536).unit is Unit.BYTES

    def test_only_tokens_are_inexact(self) -> None:
        assert not Unit.TOKENS.is_exact
        assert Unit.CHARACTERS.is_exact
        assert Unit.BYTES.is_exact

    @pytest.mark.parametrize("limit", [0, -1])
    def test_a_budget_that_admits_nothing_is_refused(self, limit: int) -> None:
        with pytest.raises(ValueError, match="admit nothing"):
            Budget(Unit.TOKENS, limit)

    def test_it_round_trips_through_its_string(self) -> None:
        assert Budget.parse(str(Budget.tokens(8000))) == Budget.tokens(8000)

    def test_a_bare_number_is_refused(self) -> None:
        # The whole point of the type is that the unit is a decision.
        # Defaulting it would put the decision back where the draft had it.
        with pytest.raises(ValueError, match="needs its unit"):
            Budget.parse("8000")

    def test_an_unknown_unit_names_the_ones_that_exist(self) -> None:
        with pytest.raises(ValueError, match="tokens"):
            Budget.parse("words:100")

    def test_a_non_numeric_limit_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be a number"):
            Budget.parse("tokens:lots")

    def test_fits_and_remaining(self) -> None:
        budget = Budget.tokens(100)
        assert budget.fits(100)
        assert not budget.fits(101)
        assert budget.remaining(30) == 70
        assert budget.remaining(500) == 0


class TestScriptClasses:
    @pytest.mark.parametrize(
        ("character", "expected"),
        [
            ("a", ScriptClass.LATIN),
            ("Z", ScriptClass.LATIN),
            ("東", ScriptClass.IDEOGRAPH),
            ("あ", ScriptClass.KANA),
            ("ア", ScriptClass.KANA),
            ("한", ScriptClass.HANGUL),
            ("7", ScriptClass.DIGIT),
            (" ", ScriptClass.SPACE),
            ("\n", ScriptClass.SPACE),
            ("。", ScriptClass.OTHER),
            ("→", ScriptClass.OTHER),
        ],
    )
    def test_classification(self, character: str, expected: ScriptClass) -> None:
        assert classify(character) == expected

    def test_a_profile_counts_every_character_exactly_once(self) -> None:
        text = "東京 tokyo 123\nあア。"
        assert sum(profile(text).values()) == len(text)


class TestTheEstimator:
    @pytest.fixture
    def model(self) -> HeuristicTokenCost:
        return HeuristicTokenCost()

    def test_it_satisfies_the_port(self, model: HeuristicTokenCost) -> None:
        assert isinstance(model, CostModel)

    def test_japanese_costs_several_times_more_per_character(
        self, model: HeuristicTokenCost
    ) -> None:
        # The reason this file exists. One constant for both scripts would be
        # comfortable in English and blow the window in Japanese.
        japanese = model.cost("東京会議設計方針") / 8
        english = model.cost("abcdefgh") / 8
        assert japanese > english * 4

    def test_empty_text_costs_nothing(self, model: HeuristicTokenCost) -> None:
        assert model.cost("") == 0

    def test_non_empty_text_never_costs_nothing(self, model: HeuristicTokenCost) -> None:
        # A piece of context that costs zero would be admitted to any budget,
        # however long the corpus of them.
        assert model.cost(" ") >= 1
        assert model.cost("\n") >= 1

    def test_cost_grows_with_length(self, model: HeuristicTokenCost) -> None:
        assert model.cost(JAPANESE * 3) > model.cost(JAPANESE)

    def test_it_states_its_error(self, model: HeuristicTokenCost) -> None:
        # A token count with no stated error is a number pretending to be a
        # measurement.
        error = model.measured_error
        assert error is not None
        assert 0 < error.p50 < error.p95 < 1
        assert error.against
        assert error.dataset

    def test_the_model_is_versioned(self, model: HeuristicTokenCost) -> None:
        # A change to the estimator changes every budget decision, so it has to
        # be identifiable in a package (ADR-0003).
        assert "@" in model.name

    def test_no_script_class_is_free(self, model: HeuristicTokenCost) -> None:
        # A class weighted zero would let a corpus in that script be sent for
        # nothing. Hangul is the one that was never fitted, and it is the one
        # this test exists for.
        for character in "aあ東한7 。":
            assert model.cost(character * 200) > 1, f"{character!r} costs nothing"


class TestTheExactModels:
    def test_characters_are_counted_not_estimated(self) -> None:
        model = CharacterCost()
        assert model.cost(JAPANESE) == len(JAPANESE)
        assert model.measured_error is None

    def test_bytes_are_utf8(self) -> None:
        model = ByteCost()
        assert model.cost("東京") == 6
        assert model.cost("ab") == 2
        assert model.measured_error is None

    @pytest.mark.parametrize("model", [CharacterCost(), ByteCost()])
    def test_an_exact_model_reports_no_error(self, model: CostModel) -> None:
        # There is nothing to report: it counts.
        assert model.measured_error is None

    @pytest.mark.parametrize("model", [CharacterCost(), ByteCost(), HeuristicTokenCost()])
    def test_every_model_satisfies_the_port(self, model: CostModel) -> None:
        assert isinstance(model, CostModel)
        assert model.name
        assert model.unit in {u.value for u in Unit}


class TestMeasuredError:
    def test_it_must_name_what_it_was_measured_against(self) -> None:
        # A p95 against one tokenizer says little about a model that tokenizes
        # differently. Naming it is honest rather than sufficient.
        with pytest.raises(ValueError, match="must name"):
            MeasuredError(p50=0.05, p95=0.18, against="", dataset="x")

    def test_p95_below_p50_is_refused(self) -> None:
        with pytest.raises(ValueError, match="below p50"):
            MeasuredError(p50=0.2, p95=0.1, against="cl100k_base", dataset="x")

    def test_a_negative_error_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            MeasuredError(p50=-0.1, p95=0.1, against="cl100k_base", dataset="x")
