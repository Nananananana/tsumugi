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

The fourth is the one the deterministic suite genuinely cannot reach. tsumugi
reports that a corpus may not answer a question and does not gate on it —
`docs/evaluation-corpus.md` says so — because deciding "there is no answer
here" is the model's job. Until there was a model, that claim had no test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..application.ask import ask
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
    #: Empty unless the provider failed. A run with failures still reports; a
    #: model that died on case 40 should not erase the first thirty-nine.
    failure: str = ""
    claims: int = 0
    grounded: bool = False
    on_target: bool = False
    abstained: bool = False
    #: Trap fact ids a resolved citation landed inside.
    trapped: tuple[str, ...] = ()

    @property
    def ran(self) -> bool:
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
    grounded: int = 0
    on_target: int = 0
    trapped: int = 0
    abstention_cases: int = 0
    abstained_correctly: int = 0
    by_language: dict[str, tuple[int, int]] = field(default_factory=dict)

    def _share(self, count: int) -> str:
        return f"{count / self.ran:.0%}" if self.ran else "n/a"

    def describe(self) -> str:
        lines = [
            f"{self.ran} cases answered by {self.model}"
            + (f", {self.failed} failed to run" if self.failed else ""),
            f"  grounded    {self._share(self.grounded):>5}   every citation resolved",
            f"  on target   {self._share(self.on_target):>5}   a citation landed in a planted fact",
            f"  trapped     {self._share(self.trapped):>5}   a citation landed in a "
            f"planted adversary",
        ]
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
        except TsumugiError as error:
            return AnswerScore(
                case_id=case.case_id,
                language=case.language,
                trap_kinds=kinds,
                failure=str(error),
            )

        located = [
            location
            for claim in asked.verification.claims
            for citation in claim.citations
            for location in citation.locations
        ]
        planted, tripped = _where_the_citations_landed(case, located)

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
            trapped=tripped,
        )


def _where_the_citations_landed(
    case: Case, located: Sequence[Located]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split resolved citations into planted answers and planted adversaries.

    Matched by span overlap in the document, not by string equality: a model
    quotes a fragment, and a fragment of a planted fact is still that fact.
    """
    trap_facts = set(case.traps)
    answers: set[str] = set()
    adversaries: set[str] = set()

    for location in located:
        document = _document_of(case, location.source_path)
        if document is None:
            continue
        for fact_id, fact in case.facts.items():
            if case.fact_document.get(fact_id) != document:
                continue
            if not location.anchor.span.overlaps(fact.span):
                continue
            if fact_id in trap_facts:
                adversaries.add(fact_id)
            elif fact_id in case.must_include:
                answers.add(fact_id)

    return tuple(sorted(answers)), tuple(sorted(adversaries))


def _document_of(case: Case, source_path: str) -> str | None:
    if source_path in case.documents:
        return source_path
    tail = source_path.replace("\\", "/").rsplit("/", 1)[-1]
    for relative in case.documents:
        if relative.replace("\\", "/").rsplit("/", 1)[-1] == tail:
            return relative
    return None


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
        summary.grounded += int(score.grounded)
        summary.on_target += int(score.on_target)
        summary.trapped += int(bool(score.trapped))
        if score.expected_to_abstain:
            summary.abstention_cases += 1
            summary.abstained_correctly += int(score.abstained)
        clean, total = summary.by_language.get(score.language, (0, 0))
        summary.by_language[score.language] = (clean + int(score.grounded), total + 1)
    return summary
