"""The measurement the deterministic suite has always disclaimed.

`tsumugi eval` ends with a sentence it has printed since the corpus existed:

    Nothing here measures whether an answer built from a package is correct.

That was true and had to stay printed, because the pipeline stopped at a
rendered prompt. Now it does not, so the gap is measurable — and the honest
thing is to measure it rather than delete the sentence.

**This is never a gate.** It needs a model running, the model is not in CI, and
a number that depends on which model somebody happened to pull is not a floor
anybody can hold. It is reported and dated, like every other measurement in
this project ([docs/measurements.md](../../../docs/measurements.md)).

What it can say, and the four questions are deliberately separate:

``grounded``    every citation resolved. Says nothing about whether the answer
                is right — a model can quote perfectly and reason badly.
``on target``   a resolved citation landed inside a *planted* fact. This is the
                one that says retrieval and the answer agreed.
``trapped``     a resolved citation landed inside a planted **adversary** —
                the superseded version, the near-duplicate, the lexical
                near-miss. The package is allowed to carry these; being fooled
                by one is a different failure from selecting one.
``abstained``   every claim came back uncited. On an ``absent_answer`` case
                that is the correct answer; anywhere else it is a model
                refusing work it was given the evidence for.

A fifth outcome is reported separately and is not a rate: ``unreadable``, for
an answer that was not in the requested shape at all. That is a *result* --
llama3.1:8b produced 16 of them on a first 50-case run -- and folding it into
"failed to run" alongside a model that was not listening would hide the more
interesting of the two. A model that cannot follow the output contract is
telling you something about the model; a socket that refused is telling you
about the machine.

The fourth is the one the deterministic suite genuinely cannot reach. tsumugi
reports that a corpus may not answer a question and does not gate on it —
`docs/evaluation-corpus.md` says so — because deciding "there is no answer
here" is the model's job. Until there was a model, that claim had no test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..application.ask import ask
from ..application.verify import AnswerFormatError
from ..domain.claim import Located
from ..errors import TsumugiError
from ..ports.llm import LLMProvider
from .dataset import Case
from .runner import cost_model_for, prepared_case

__all__ = ["AnswerScore", "AnswerSummary", "answer_case", "answer_cases", "summarise_answers"]


@dataclass(frozen=True, slots=True)
class AnswerScore:
    """What one model did with one package."""

    case_id: str
    language: str
    trap_kinds: tuple[str, ...]
    #: Empty unless the provider could not be reached. A run with failures
    #: still reports; a model that died on case 40 should not erase the first
    #: thirty-nine.
    failure: str = ""
    #: The model answered, and not in the shape it was asked for. Kept apart
    #: from ``failure``: one is a fact about the model, the other about the
    #: machine.
    unreadable: str = ""
    claims: int = 0
    grounded: bool = False
    on_target: bool = False
    abstained: bool = False
    #: Trap fact ids a resolved citation landed inside, where the trap says
    #: something *other* than the answer.
    trapped: tuple[str, ...] = ()
    #: Trap fact ids that are copies of the answer. Reported, never counted as
    #: being fooled: quoting a copy is quoting the answer.
    cited_a_copy: tuple[str, ...] = ()
    #: At least one claim cited a planted adversary *and* the answer in the
    #: same breath. That is the model comparing them, which is what the
    #: instruction set asks for when two passages disagree.
    contrasted: bool = False

    @property
    def ran(self) -> bool:
        """It answered. Whether the answer was usable is ``unreadable``."""
        return not self.failure

    @property
    def expected_to_abstain(self) -> bool:
        return "absent_answer" in self.trap_kinds

    @property
    def abstained_correctly(self) -> bool:
        """Abstaining is right on an ``absent_answer`` case and wrong elsewhere."""
        return self.abstained == self.expected_to_abstain


@dataclass(slots=True)
class AnswerSummary:
    """The four rates, over however many cases ran."""

    model: str
    ran: int = 0
    failed: int = 0
    unreadable: int = 0
    grounded: int = 0
    on_target: int = 0
    trapped: int = 0
    contrasted: int = 0
    cited_a_copy: int = 0
    abstention_cases: int = 0
    abstained_correctly: int = 0
    by_language: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def checked(self) -> int:
        """Answers that could be checked at all.

        The denominator for every rate. An unreadable answer says nothing
        about grounding, and dividing by it would report a model that cannot
        follow an output contract as a model that cites badly -- two different
        problems with two different fixes.
        """
        return self.ran - self.unreadable

    def _share(self, count: int) -> str:
        return f"{count / self.checked:.0%}" if self.checked else "n/a"

    def describe(self) -> str:
        lines = [
            f"{self.checked} of {self.ran} answers checkable, by {self.model}"
            + (f", {self.failed} failed to run" if self.failed else ""),
            f"  grounded    {self._share(self.grounded):>5}   every citation resolved",
            f"  on target   {self._share(self.on_target):>5}   a citation landed in a planted fact",
            f"  trapped     {self._share(self.trapped):>5}   a claim cited an outdated "
            f"passage as its answer",
        ]
        if self.contrasted:
            lines.append(
                f"  contrasted  {self._share(self.contrasted):>5}   a claim cited a "
                f"disagreeing passage beside the answer, which is what was asked for"
            )
        if self.cited_a_copy:
            # Reported and not counted above. A near-duplicate carries the
            # answer's own content, so quoting it is quoting the answer.
            lines.append(
                f"  ({self.cited_a_copy} cited a verbatim copy of the answer, which is "
                f"not being fooled)"
            )
        if self.unreadable:
            # Reported as a count, not a rate. It is a fact about the model's
            # ability to follow an output contract, not about retrieval, and
            # averaging it in with the rest would say neither thing clearly.
            lines.append(
                f"  {self.unreadable} of those answers were not in the requested shape "
                f"and could not be checked at all"
            )
        if self.abstention_cases:
            share = self.abstained_correctly / self.abstention_cases
            lines.append(
                f"  abstention  {share:>5.0%}   of {self.abstention_cases} cases where the "
                f"corpus has no answer"
            )
        for language, (clean, total) in sorted(self.by_language.items()):
            lines.append(f"    {language:<8} {clean}/{total} grounded")
        return "\n".join(lines)


def answer_case(case: Case, provider: LLMProvider, *, candidate_limit: int = 50) -> AnswerScore:
    """Build this case's package, put the question to ``provider``, and score it.

    Uses the shipped ``ask`` rather than a rehearsal of it, so that what is
    measured is the prompt people actually send — including the output contract,
    which is the part a real model turned out to read differently from its
    author.
    """
    kinds = tuple(sorted({trap.kind for trap in case.traps.values()}))
    with prepared_case(case) as (store, index, _root):
        try:
            asked = ask(
                case.question,
                store=store,
                index=index,
                cost_model=cost_model_for(case.budget.unit),
                budget=case.budget,
                provider=provider,
                candidate_limit=candidate_limit,
                version="eval",
            )
        except AnswerFormatError as error:
            # The model answered; it just did not answer in the shape it was
            # asked for. A result, not a failed run.
            return AnswerScore(
                case_id=case.case_id,
                language=case.language,
                trap_kinds=kinds,
                unreadable=str(error),
            )
        except TsumugiError as error:
            return AnswerScore(
                case_id=case.case_id,
                language=case.language,
                trap_kinds=kinds,
                failure=str(error),
            )

        # Per claim, not per answer. Citing the superseded passage *beside*
        # the current one is a comparison; citing it *instead* is being fooled.
        # An answer-level count cannot tell those apart, and the instruction
        # set now asks for exactly the first -- so measured at the answer
        # level, following the instruction reads as failing.
        planted: set[str] = set()
        tripped: set[str] = set()
        copies: set[str] = set()
        contrasted = False
        for claim in asked.verification.claims:
            located = [location for citation in claim.citations for location in citation.locations]
            answers, adversaries, duplicates = _where_the_citations_landed(case, located)
            planted.update(answers)
            copies.update(duplicates)
            if adversaries and answers:
                contrasted = True
            elif adversaries:
                tripped.update(adversaries)

        return AnswerScore(
            case_id=case.case_id,
            language=case.language,
            trap_kinds=kinds,
            claims=len(asked.verification.claims),
            grounded=asked.trustworthy,
            on_target=bool(planted),
            # Every claim uncited. Not "some", because a model that cites one
            # of three statements has not abstained -- it has answered and been
            # lazy, and calling that abstention would flatter it.
            abstained=bool(asked.verification.claims)
            and all(not claim.citations for claim in asked.verification.claims),
            trapped=tuple(sorted(tripped)),
            cited_a_copy=tuple(sorted(copies)),
            contrasted=contrasted,
        )


#: Trap kinds that carry the answer's own content. Citing one of these is
#: citing the answer, so it is reported and never counted as being fooled.
_COPIES: frozenset[str] = frozenset({"near_duplicate"})


def _where_the_citations_landed(
    case: Case, located: Sequence[Located]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Split resolved citations into answers, adversaries, and copies.

    Matched by span overlap in the document, not by string equality: a model
    quotes a fragment, and a fragment of a planted fact is still that fact.
    """
    answers: set[str] = set()
    adversaries: set[str] = set()
    copies: set[str] = set()

    for location in located:
        document = case.document_for(location.source_path)
        if document is None:
            continue
        for fact_id, fact in case.facts.items():
            if case.fact_document.get(fact_id) != document:
                continue
            if not location.anchor.span.overlaps(fact.span):
                continue
            trap = case.traps.get(fact_id)
            if trap is not None:
                (copies if trap.kind in _COPIES else adversaries).add(fact_id)
            elif fact_id in case.must_include:
                answers.add(fact_id)

    return tuple(sorted(answers)), tuple(sorted(adversaries)), tuple(sorted(copies))


def answer_cases(
    cases: Sequence[Case], provider: LLMProvider, *, candidate_limit: int = 50
) -> list[AnswerScore]:
    return [answer_case(case, provider, candidate_limit=candidate_limit) for case in cases]


def summarise_answers(scores: Sequence[AnswerScore], *, model: str) -> AnswerSummary:
    summary = AnswerSummary(model=model)
    for score in scores:
        if not score.ran:
            summary.failed += 1
            continue
        summary.ran += 1
        if score.unreadable:
            summary.unreadable += 1
        summary.grounded += int(score.grounded)
        summary.on_target += int(score.on_target)
        summary.trapped += int(bool(score.trapped))
        summary.contrasted += int(score.contrasted)
        summary.cited_a_copy += int(bool(score.cited_a_copy))
        if score.unreadable:
            # Counted, and then out of every rate. It is not an abstention and
            # it is not an ungrounded answer; it is an answer nobody can check.
            continue
        if score.expected_to_abstain:
            summary.abstention_cases += 1
            summary.abstained_correctly += int(score.abstained)
        clean, total = summary.by_language.get(score.language, (0, 0))
        summary.by_language[score.language] = (clean + int(score.grounded), total + 1)
    return summary
