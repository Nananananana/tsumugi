"""The opt-in half of the evaluation, and what it is allowed to conclude.

No model runs here. The providers below are rigged to behave like the four
model behaviours worth telling apart -- quotes the answer, invents a quotation,
abstains, quotes the adversary -- because the point of the scorer is that it
distinguishes them, and a real model would only tell us that on the day it was
run.

The real numbers live in ``docs/measurements.md``, dated and named by model,
where every other measurement in this project lives.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from tsumugi.errors import TsumugiError
from tsumugi.evaluation.answering import (
    AnswerScore,
    answer_case,
    answer_cases,
    summarise_answers,
)
from tsumugi.evaluation.dataset import Case, load_cases
from tsumugi.ports.llm import Endpoint

CASES = Path(__file__).resolve().parent / "cases"


@dataclass
class Scripted:
    """Answers with whatever it was told to, having read the prompt.

    Reading the prompt matters: a provider that ignored it could not be fooled
    by a trap, and a scorer tested only against one that ignores the context
    would be measuring nothing.
    """

    quotations: tuple[str, ...] = ()
    text: str = "an answer"
    fail: bool = False
    prompts: list[str] | None = None

    @property
    def name(self) -> str:
        return "scripted/1"

    @property
    def endpoint(self) -> Endpoint:
        return Endpoint(url="memory://scripted", is_local=True)

    def generate(self, prompt: str) -> str:
        if self.prompts is not None:
            self.prompts.append(prompt)
        if self.fail:
            raise TsumugiError("the model is not running")
        return json.dumps(
            {"claims": [{"text": self.text, "citations": list(self.quotations)}]},
            ensure_ascii=False,
        )


@dataclass
class Prose:
    """Answers in perfectly good English that no verifier can check."""

    @property
    def name(self) -> str:
        return "prose/1"

    @property
    def endpoint(self) -> Endpoint:
        return Endpoint(url="memory://prose", is_local=True)

    def generate(self, prompt: str) -> str:
        return "The retry policy uses exponential backoff, according to the notes."


def _case_with(kind: str | None = None, *, without: str | None = None) -> Case:
    """One real case from the committed corpus, chosen by what it plants.

    An ``absent_answer`` case has no ``must_include`` -- that is what it is --
    so the requirement is only applied when a planted answer is what the test
    is about.
    """
    for case in load_cases(CASES):
        kinds = {trap.kind for trap in case.traps.values()}
        if kind is not None and kind not in kinds:
            continue
        if without is not None and without in kinds:
            continue
        if kind == "absent_answer" or case.must_include:
            return case
    pytest.skip(f"no case with kind={kind!r} without={without!r}")


def _planted_text(case: Case) -> str:
    return case.facts[case.must_include[0]].text


class TestWhatItCanTellApart:
    def test_a_model_that_quotes_the_answer_is_grounded_and_on_target(self) -> None:
        case = _case_with(without="absent_answer")
        score = answer_case(case, Scripted(quotations=(_planted_text(case),)))
        assert score.ran
        assert score.grounded, "the quotation is verbatim from a planted fact"
        assert score.on_target, "and the planted fact is one the case requires"

    def test_a_model_that_invents_a_quotation_is_not_grounded(self) -> None:
        case = _case_with(without="absent_answer")
        score = answer_case(case, Scripted(quotations=("これはどの文書にも存在しない一文である",)))
        assert not score.grounded
        # And not on target either, which is the distinction that matters: an
        # ungrounded answer cannot have used the right evidence.
        assert not score.on_target

    def test_a_model_that_cites_nothing_has_abstained(self) -> None:
        case = _case_with(without="absent_answer")
        score = answer_case(case, Scripted(quotations=()))
        assert score.abstained
        # Wrong here. The corpus does answer this one.
        assert not score.abstained_correctly

    def test_abstaining_is_correct_where_the_corpus_has_no_answer(self) -> None:
        case = _case_with("absent_answer")
        score = answer_case(case, Scripted(quotations=()))
        assert score.expected_to_abstain
        assert score.abstained_correctly

    def test_answering_anyway_is_wrong_where_the_corpus_has_no_answer(self) -> None:
        # The failure the deterministic suite deliberately cannot catch.
        # tsumugi reports that a corpus may not answer and does not gate on
        # it, because that call is the model's -- so this is the only place
        # the cost of that decision is visible.
        case = _case_with("absent_answer")
        prompts: list[str] = []
        probe = Scripted(quotations=(), prompts=prompts)
        answer_case(case, probe)
        quoted = _first_context_line(prompts[0])
        score = answer_case(case, Scripted(quotations=(quoted,)))
        assert not score.abstained
        assert not score.abstained_correctly


class TestTheTrapMetric:
    def test_quoting_a_superseded_passage_is_recorded_as_trapped(self) -> None:
        # Separate from `grounded` on purpose. The old version really is in the
        # corpus, so the citation resolves and grounding cannot catch it.
        # Being fooled and fabricating are different failures.
        case = _case_with("superseded")
        trap_id = next(id for id, trap in case.traps.items() if trap.kind == "superseded")
        score = answer_case(case, Scripted(quotations=(case.facts[trap_id].text,)))
        if not score.trapped:
            # The package is allowed to leave an adversary out entirely; when
            # it does, the quotation resolves nowhere and there is nothing to
            # be fooled by.
            assert not score.grounded
        else:
            assert trap_id in score.trapped
            assert score.grounded, "the adversary really is in the corpus"

    def test_quoting_a_verbatim_copy_is_not_being_fooled(self) -> None:
        # A near_duplicate carries the answer's own content, so citing it is
        # citing the answer. Counting it reported 88% of a held-out run as
        # fooled -- alarming, precise, and about nothing.
        case = _case_with("near_duplicate")
        copy_id = next(id for id, trap in case.traps.items() if trap.kind == "near_duplicate")
        score = answer_case(case, Scripted(quotations=(case.facts[copy_id].text,)))
        assert copy_id not in score.trapped
        if score.grounded:
            assert copy_id in score.cited_a_copy, "reported, and not as a failure"

    def test_a_summary_counts_a_trapped_case_once(self) -> None:
        # Two adversaries quoted in one answer is one fooled answer, not two.
        scores = [
            AnswerScore(
                case_id="a", language="ja", trap_kinds=("superseded",), trapped=("f1", "f2")
            )
        ]
        assert summarise_answers(scores, model="m").trapped == 1

    def test_a_copy_is_reported_beside_the_rates_and_not_among_them(self) -> None:
        scores = [
            AnswerScore(
                case_id="a",
                language="ja",
                trap_kinds=("near_duplicate",),
                grounded=True,
                cited_a_copy=("dup",),
            )
        ]
        summary = summarise_answers(scores, model="m")
        assert summary.trapped == 0 and summary.cited_a_copy == 1
        assert "not being fooled" in summary.describe()


class TestAModelThatIgnoresTheContract:
    def test_prose_is_a_result_not_a_failed_run(self) -> None:
        # llama3.1:8b produced 16 of these on a first fifty-case run. Folding
        # them into "failed to run" alongside a model that was not listening
        # would hide the more interesting of the two: a model that cannot
        # follow an output contract is a fact about the model, an unreachable
        # socket is a fact about the machine.
        case = _case_with(without="absent_answer")
        score = answer_case(case, Prose())
        assert score.ran, "it answered"
        assert score.unreadable, "just not in the shape it was asked for"
        assert not score.failure
        assert not score.grounded

    def test_the_summary_counts_them_and_does_not_average_them(self) -> None:
        scores = [
            AnswerScore(case_id="a", language="ja", trap_kinds=(), unreadable="not JSON"),
            AnswerScore(case_id="b", language="ja", trap_kinds=(), grounded=True),
        ]
        summary = summarise_answers(scores, model="m")
        assert summary.ran == 2 and summary.unreadable == 1 and summary.failed == 0
        # And out of the denominator. An unreadable answer says nothing about
        # grounding, so counting it as ungrounded would report a model that
        # cannot follow an output contract as one that cites badly.
        assert summary.checked == 1
        assert summary.grounded == 1
        described = summary.describe()
        assert "grounded     100%" in described
        # A count, in words. Not a percentage beside the four rates, because
        # it is not measuring the same kind of thing.
        assert "1 of those answers" in described


class TestFailureIsRecordedNotRaised:
    def test_a_provider_that_dies_does_not_end_the_run(self) -> None:
        # A model that dies on case 40 should not erase the first thirty-nine.
        cases = load_cases(CASES)[:3]
        scores = answer_cases(cases, Scripted(fail=True))
        assert len(scores) == 3
        assert all(not score.ran for score in scores)
        assert all("not running" in score.failure for score in scores)

    def test_the_summary_separates_failures_from_failures_to_ground(self) -> None:
        # A model that could not be reached and a model that made things up
        # are different results, and averaging them together would hide which
        # one you have.
        scores = [
            AnswerScore(case_id="a", language="ja", trap_kinds=(), grounded=True),
            AnswerScore(case_id="b", language="ja", trap_kinds=(), grounded=False),
            AnswerScore(case_id="c", language="ja", trap_kinds=(), failure="unreachable"),
        ]
        summary = summarise_answers(scores, model="scripted/1")
        assert summary.ran == 2 and summary.failed == 1
        assert summary.grounded == 1
        assert "50%" in summary.describe()


class TestTheOutputContractIsStatedInWords:
    def test_it_asks_for_json_in_a_rule_and_not_only_in_a_schema(self) -> None:
        # Earned, and expensively. When the JSON shape moved into
        # `# OUTPUT_SCHEMA` and no rule said "reply with JSON", llama3.1:8b
        # answered all fifty evaluation cases in fluent prose -- which verifies
        # as zero claims, and zero claims reads clean. A schema is a
        # specification; a rule is what a model follows.
        from tsumugi.application.instructions import ANSWERING

        rules = " ".join(ANSWERING["rules"])
        assert "JSON only" in rules
        assert '"claims"' in rules, "and an example, because weak models copy shapes"


class TestItIsNeverAGate:
    def test_the_summary_states_no_floor(self) -> None:
        # Deliberate. A number that depends on which model somebody pulled is
        # not a floor anybody can hold, and pretending otherwise would make
        # the deterministic floors above it look equally negotiable.
        summary = summarise_answers(
            [AnswerScore(case_id="a", language="ja", trap_kinds=(), grounded=True)],
            model="scripted/1",
        )
        described = summary.describe()
        assert "floor" not in described.lower()
        assert "scripted/1" in described, "a number without its model is not a measurement"

    def test_the_deterministic_scorer_never_needs_a_provider(self) -> None:
        # The half that gates CI must keep running with no model at all.
        import inspect

        from tsumugi.evaluation import runner

        assert "Provider" not in inspect.getsource(runner)
        assert "llm" not in inspect.getsource(runner)


class TestBothHalvesMaterialiseACaseTheSameWay:
    def test_they_share_one_preparation(self) -> None:
        # Two ways to build a case's index would be two ways for a case to
        # mean something slightly different, and the difference would show up
        # as a model looking better or worse than it is.
        import inspect

        from tsumugi.evaluation import answering, runner

        assert "prepared_case" in inspect.getsource(runner.run_case)
        assert "prepared_case" in inspect.getsource(answering.answer_case)


def _first_context_line(prompt: str) -> str:
    """A line of actual context, not a header, from a rendered package."""
    inside = False
    for line in prompt.splitlines():
        if line.startswith("# CONTEXT"):
            inside = True
            continue
        if inside and line.startswith("# "):
            break
        if inside and line.strip() and not line.startswith("["):
            return line.strip()
    pytest.skip("the package carried no context")


class TestAnUnreadableAnswerIsNotAnAnswer:
    def test_it_is_not_counted_as_answering_the_unanswerable(self) -> None:
        # It reported ten models confidently answering a question the corpus
        # cannot answer, when in fact none of them had produced anything
        # parseable at all. The worst kind of wrong number: alarming, precise
        # and about nothing.
        score = AnswerScore(
            case_id="x",
            language="en",
            trap_kinds=("absent_answer",),
            unreadable="the answer is not JSON",
        )
        assert score.expected_to_abstain
        assert not score.abstained
        # The CLI's filter excludes it; the summary's denominator does too.
        summary = summarise_answers([score], model="m")
        assert summary.abstention_cases == 0, "there was nothing to abstain with"
