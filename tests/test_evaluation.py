"""The harness that measures selection, and the fixtures it runs on.

A generator that plants a trap wrongly produces a case that fails a *correct*
implementation, and that failure is expensive precisely because the instinct is
to go looking in the code. So most of this file is about the harness being
right, not about the scores being good.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tsumugi.domain.anchor import Anchor
from tsumugi.domain.budget import Budget
from tsumugi.domain.omission import Omission, OmissionRule
from tsumugi.domain.package import BudgetReport, ContextPackage, PackageProvenance
from tsumugi.domain.selection import ContextItem
from tsumugi.domain.span import Span
from tsumugi.evaluation import load_case, load_cases, run_case, score_case, strip_markup, summarise
from tsumugi.evaluation.dataset import Case
from tsumugi.evaluation.scoring import FLOORS, Summary

from .helpers import build_document

CASES = Path(__file__).resolve().parent / "cases"


class TestMarkup:
    def test_it_strips_the_markup_and_locates_the_fact(self) -> None:
        plain, facts = strip_markup("前置き{{F:a}}テントは 2.4kg{{/F}}。あと。")
        assert plain == "前置きテントは 2.4kg。あと。"
        assert facts["a"].span.slice(plain) == "テントは 2.4kg"

    def test_offsets_are_computed_not_written_by_hand(self) -> None:
        # Hand-written offsets measure the annotator rather than the system.
        plain, facts = strip_markup("{{F:a}}one{{/F}} and {{F:b}}two{{/F}}")
        assert facts["a"].span == Span(0, 3)
        assert facts["b"].span == Span(8, 11)
        assert plain == "one and two"

    def test_a_document_with_no_markup_is_unchanged(self) -> None:
        plain, facts = strip_markup("just prose\n")
        assert plain == "just prose\n"
        assert facts == {}

    @pytest.mark.parametrize(
        "broken",
        [
            "{{F:a}}unclosed",
            "closed only{{/F}}",
            "{{F:a}}one{{/F}} {{F:a}}again{{/F}}",
            "{{F:a}}{{/F}}",
        ],
    )
    def test_broken_markup_raises_rather_than_doing_something_reasonable(self, broken: str) -> None:
        with pytest.raises(ValueError):
            strip_markup(broken)


class TestTheFixtures:
    def test_there_are_cases(self) -> None:
        assert len(load_cases(CASES)) >= 50

    def test_every_case_loads(self) -> None:
        for case in load_cases(CASES):
            assert case.question
            assert case.must_include or case.traps

    def test_every_required_fact_slices_back_out_of_its_document(self) -> None:
        # The oracle's first check, kept here so it runs on every commit
        # rather than only when the generator does.
        for case in load_cases(CASES):
            for fact_id in case.must_include:
                fact = case.facts[fact_id]
                document = case.documents[case.fact_document[fact_id]]
                assert fact.span.slice(document) == fact.text

    def test_no_case_is_trivially_solvable(self) -> None:
        # A corpus whose only document is the answer measures nothing.
        for case in load_cases(CASES):
            assert len(case.documents) >= 2, case.case_id

    def test_all_seven_trap_kinds_are_planted(self) -> None:
        # The genres are decoration; the traps are the dataset. A kind defined
        # in docs/evaluation-corpus.md and never planted measures nothing.
        from tsumugi.evaluation.dataset import TRAP_KINDS

        planted = {trap.kind for case in load_cases(CASES) for trap in case.traps.values()}
        assert planted == TRAP_KINDS

    def test_every_case_plants_at_least_one_trap(self) -> None:
        # The genres are decoration; the traps are the dataset.
        for case in load_cases(CASES):
            assert case.traps, case.case_id

    def test_materialising_preserves_the_offsets(self, tmp_path: Path) -> None:
        # The bug this exists for: Python rewrites \n as \r\n on write under
        # Windows, and one byte per line makes every offset wrong. It surfaced
        # as 0% recall and looked like a retrieval failure.
        for case in load_cases(CASES)[:3]:
            root = case.materialise(tmp_path)
            for relative, expected in case.documents.items():
                assert (root / relative).read_bytes().decode("utf-8") == expected

    def test_the_ci_tier_is_small_enough_to_run_always(self) -> None:
        # A suite too slow to run is a suite that decays. The number is a
        # proxy for that, and the proxy was set when a case cost more than it
        # does: 120 cases run in about two and a half seconds, so the cap is
        # nowhere near what actually matters. Raised rather than removed --
        # something has to notice if a case starts costing a second.
        assert 0 < len(load_cases(CASES, tier="ci")) <= 300

    def test_some_cases_are_held_out(self) -> None:
        # A ranker tuned against every case it will be scored on is a ranker
        # fitted to the dataset.
        assert load_cases(CASES, split="held_out")

    def test_no_fixture_contains_anything_that_looks_real(self) -> None:
        # These files ship inside the package. A real address or key committed
        # here is published to everyone who installs tsumugi.
        for case in load_cases(CASES):
            for relative, text in case.documents.items():
                for shape in ("@", "://", "-----BEGIN", "password", "sk-"):
                    assert shape not in text, f"{case.case_id}/{relative}"


class TestCaseValidation:
    def _write(
        self, tmp_path: Path, manifest: dict[str, object], documents: dict[str, str]
    ) -> Path:
        directory = tmp_path / "case"
        (directory / "corpus").mkdir(parents=True, exist_ok=True)
        for name, text in documents.items():
            (directory / "corpus" / name).write_text(text, encoding="utf-8")
        (directory / "case.json").write_text(json.dumps(manifest), encoding="utf-8")
        return directory

    def test_a_case_naming_a_fact_nobody_planted_is_refused(self, tmp_path: Path) -> None:
        directory = self._write(
            tmp_path,
            {"question": "q", "must_include": ["ghost"]},
            {"a.md": "{{F:real}}text{{/F}}"},
        )
        with pytest.raises(ValueError, match="not planted"):
            load_case(directory)

    def test_a_fact_both_required_and_forbidden_is_refused(self, tmp_path: Path) -> None:
        directory = self._write(
            tmp_path,
            {"question": "q", "must_include": ["a"], "must_not_include": ["a"]},
            {"a.md": "{{F:a}}text{{/F}}"},
        )
        with pytest.raises(ValueError, match="both required and forbidden"):
            load_case(directory)

    def test_a_case_asserting_nothing_is_refused(self, tmp_path: Path) -> None:
        # It would measure nothing.
        directory = self._write(tmp_path, {"question": "q"}, {"a.md": "{{F:a}}text{{/F}}"})
        with pytest.raises(ValueError, match="asserts nothing"):
            load_case(directory)

    def test_a_case_may_require_nothing_if_it_expects_a_reported_exclusion(
        self, tmp_path: Path
    ) -> None:
        # A budget squeeze: the answer is meant to be *reported*, not included,
        # which is exactly what ADR-0005 is about. An earlier rule knew only
        # about required and forbidden facts and rejected every such case.
        directory = self._write(
            tmp_path,
            {
                "question": "q",
                "traps": {
                    "a": {"kind": "budget_squeeze", "expect_omission_rule": "budget_exhausted"}
                },
            },
            {"a.md": "{{F:a}}text{{/F}}"},
        )
        assert load_case(directory).must_include == ()

    def test_an_absent_answer_case_may_require_nothing(self, tmp_path: Path) -> None:
        directory = self._write(
            tmp_path,
            {"question": "q", "traps": {"a": {"kind": "absent_answer"}}},
            {"a.md": "{{F:a}}text{{/F}}"},
        )
        assert load_case(directory).must_include == ()

    def test_an_unknown_trap_kind_is_refused(self, tmp_path: Path) -> None:
        directory = self._write(
            tmp_path,
            {"question": "q", "must_include": ["a"], "traps": {"a": {"kind": "vibes"}}},
            {"a.md": "{{F:a}}text{{/F}}"},
        )
        with pytest.raises(ValueError, match="unknown trap kind"):
            load_case(directory)

    def test_the_same_fact_in_two_documents_is_refused(self, tmp_path: Path) -> None:
        directory = self._write(
            tmp_path,
            {"question": "q", "must_include": ["a"]},
            {"one.md": "{{F:a}}text{{/F}}", "two.md": "{{F:a}}text{{/F}}"},
        )
        with pytest.raises(ValueError, match="planted in both"):
            load_case(directory)


class TestScoring:
    def test_a_package_containing_the_required_fact_scores_recall_of_one(self) -> None:
        case = load_case(CASES / "ja-mountaineering-00")
        document_key = case.fact_document["answer"]
        content = case.documents[document_key]
        document = build_document(document_key, content)
        fact = case.facts["answer"]

        package = ContextPackage(
            query=case.question,
            items=(
                ContextItem(
                    item_id="itm_001",
                    text=fact.span.slice(content),
                    anchor=Anchor.into(document, fact.span),
                    source_path=document_key,
                    cost=len(fact.text),
                ),
            ),
            omissions=(),
            budget=BudgetReport(Budget.characters(1000), len(fact.text), "characters@1"),
            provenance=PackageProvenance(tsumugi_version="test"),
        )
        score = score_case(case, package)
        assert score.evidence_recall == 1.0
        assert score.missed == ()

    def test_an_empty_package_scores_recall_of_zero(self) -> None:
        case = load_case(CASES / "ja-mountaineering-00")
        package = ContextPackage(
            query=case.question,
            items=(),
            omissions=(),
            budget=BudgetReport(Budget.characters(1000), 0, "characters@1"),
            provenance=PackageProvenance(tsumugi_version="test"),
        )
        score = score_case(case, package)
        assert score.evidence_recall == 0.0
        assert not score.clean

    def test_omission_correctness_asks_whether_the_reason_was_right(self) -> None:
        # The metric that separates "the budget is too small" from "the ranker
        # is broken". Same outcome, two diagnoses.
        # The `duplicate` trap, not `superseded`: a verbatim copy is what
        # `redundant_candidate` means, and a superseded version is not
        # detectable by character overlap (ADR-0015).
        case = load_case(CASES / "ja-mountaineering-01")
        document_key = case.fact_document["duplicate"]
        fact = case.facts["duplicate"]

        def package_with(rule: OmissionRule) -> ContextPackage:
            return ContextPackage(
                query=case.question,
                items=(),
                omissions=(
                    Omission(
                        rule,
                        "a reason",
                        "doc_whatever",
                        fact.span,
                        source_path=document_key,
                    ),
                ),
                budget=BudgetReport(Budget.characters(1000), 0, "characters@1"),
                provenance=PackageProvenance(tsumugi_version="test"),
            )

        expected = case.traps["duplicate"].expect_omission_rule
        assert expected is not None
        assert score_case(case, package_with(expected)).explained == ("duplicate",)

        wrong = OmissionRule.BELOW_THRESHOLD
        assert wrong is not expected
        misexplained = score_case(case, package_with(wrong)).misexplained
        assert misexplained and misexplained[0][1] == wrong.value

    def test_a_package_over_its_budget_is_not_clean(self) -> None:
        case = load_case(CASES / "ja-mountaineering-01")
        # Scaled to the content rather than a constant: a fixed character
        # budget squeezes English and not Japanese.
        assert case.budget.limit < 100
        document_key = case.fact_document["answer"]
        content = case.documents[document_key]
        document = build_document(document_key, content)
        fact = case.facts["answer"]

        # A package built against a larger budget than the case allows.
        package = ContextPackage(
            query=case.question,
            items=(
                ContextItem(
                    item_id="itm_001",
                    text=fact.span.slice(content),
                    anchor=Anchor.into(document, fact.span),
                    source_path=document_key,
                    cost=len(fact.text),
                ),
            ),
            omissions=(),
            budget=BudgetReport(Budget.characters(9999), len(fact.text), "characters@1"),
            provenance=PackageProvenance(tsumugi_version="test"),
        )
        object.__setattr__(package.budget, "estimate", 9000)
        assert not score_case(case, package).within_budget


class TestRunningForReal:
    def test_a_case_runs_end_to_end(self) -> None:
        score = run_case(load_case(CASES / "ja-mountaineering-00"))
        assert score.items > 0
        assert score.within_budget
        # Every package is built twice and the ids compared: reproducibility is
        # an invariant, not a metric (ADR-0003).
        assert score.reproducible

    def test_the_ci_tier_finds_the_answer_in_every_case(self) -> None:
        # A floor, not a target. If this drops, retrieval regressed.
        scores = [run_case(case) for case in load_cases(CASES, tier="ci")]
        summary = summarise(scores)
        assert summary.evidence_recall == 1.0
        assert summary.over_budget == ()
        assert summary.unreproducible == ()

    def test_the_lexical_near_miss_trap_mostly_holds(self) -> None:
        # 10% at the time of writing, with the residual diagnosed in
        # `application/search.py`: three cases confirm on a stopword phrase.
        # A ceiling, so that a regression is caught without pretending the
        # number is a target to optimise.
        scores = [run_case(case) for case in load_cases(CASES, tier="ci")]
        trap_rate = summarise(scores).trap_rate
        assert trap_rate is not None
        assert trap_rate <= 0.2


class TestOneRuleForWhichDocument:
    """`Case.document_for` existed twice, with two different answers.

    One matched any suffix, so a package citing `other.md` resolved to a case
    document called `her.md`. It never fired on the committed corpus -- every
    document there has a distinct name -- which is exactly why it was worth
    consolidating before it did.
    """

    def _case(self) -> Case:
        return load_cases(CASES)[0]

    def test_an_exact_key_matches(self) -> None:
        case = self._case()
        key = next(iter(case.documents))
        assert case.document_for(key) == key

    def test_a_path_under_a_workspace_matches(self) -> None:
        case = self._case()
        key = next(iter(case.documents))
        assert case.document_for(f"/tmp/workspace/corpus/{key}") == key
        assert case.document_for(rf"C:\workspace\corpus\{key}") == key

    def test_a_suffix_that_is_not_a_path_boundary_does_not_match(self) -> None:
        case = self._case()
        key = next(iter(case.documents))
        # "another-current.md" is not "current.md" in a subdirectory.
        assert case.document_for(f"another-{key}") is None

    def test_an_unknown_document_is_none_rather_than_a_guess(self) -> None:
        assert self._case().document_for("nothing/like/it.md") is None


class TestTheGateItself:
    """`breached_by` is what makes every quality claim enforceable.

    It had no tests. A gate that never fires and a gate that always passes
    look identical from a green build, and this one is the only thing standing
    between "the trap rate is 7.5%" and a sentence in a README.
    """

    def _summary(self, **fields: object) -> Summary:
        # A summary that is comfortably inside every floor, so each test moves
        # exactly one number and the failure names exactly one cause.
        base: dict[str, object] = {
            "cases": 10,
            "found": 100,
            "required": 100,
            "sprung": 0,
            "forbidden": 100,
            "explained": 100,
            "accounted": 100,
        }
        base.update(fields)
        return Summary(**base)  # type: ignore[arg-type]

    def test_a_healthy_summary_breaches_nothing(self) -> None:
        assert FLOORS.breached_by(self._summary()) == []

    def test_recall_below_the_floor_is_named(self) -> None:
        problems = FLOORS.breached_by(self._summary(found=50))
        assert len(problems) == 1
        assert "evidence recall" in problems[0] and "50.0%" in problems[0]

    def test_the_trap_rate_is_a_ceiling_not_a_floor(self) -> None:
        # The one that reads backwards, and would be easy to write the wrong
        # way round: more traps sprung is worse.
        assert FLOORS.breached_by(self._summary(sprung=1)) == []
        problems = FLOORS.breached_by(self._summary(sprung=99))
        assert len(problems) == 1 and "trap rate" in problems[0]

    def test_omission_correctness_below_the_floor_is_named(self) -> None:
        problems = FLOORS.breached_by(self._summary(explained=10))
        assert len(problems) == 1 and "omission correctness" in problems[0]

    def test_budget_and_reproducibility_are_exact_rather_than_rates(self) -> None:
        # No threshold on either. A package that exceeds its budget or does
        # not rebuild identically is a defect at any frequency (ADR-0003).
        assert FLOORS.breached_by(self._summary(over_budget=("case-1",)))
        assert FLOORS.breached_by(self._summary(unreproducible=("case-2",)))

    def test_an_empty_run_breaches_nothing_rather_than_dividing_by_zero(self) -> None:
        # Reporting "0% recall" for a run with no cases would be a build
        # failure caused by a filter that matched nothing.
        assert FLOORS.breached_by(Summary()) == []

    def test_every_breach_is_reported_not_only_the_first(self) -> None:
        problems = FLOORS.breached_by(
            self._summary(found=0, sprung=100, explained=0, unreproducible=("c",))
        )
        assert len(problems) == 4

    def test_the_floors_are_looser_than_the_current_scores(self) -> None:
        # AGENTS.md: floors, not targets. A gate set at today's number makes
        # every honest experiment a build failure. This asserts the *policy*,
        # not the scores -- if someone tightens a floor to today's value, this
        # is where it is noticed.
        assert FLOORS.evidence_recall <= 0.95
        assert FLOORS.trap_rate >= 0.20
        assert FLOORS.omission_correctness <= 0.70
