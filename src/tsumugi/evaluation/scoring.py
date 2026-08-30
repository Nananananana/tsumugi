"""Six metrics, all arithmetic. No grader, no model, no rubric.

The one worth reading twice is **omission correctness**. The other five ask
whether the outcome was right; that one asks whether the *reason given* was
right, and it is the only one that can tell two very different situations
apart:

    dropped the required document, said `budget_exhausted`  -> the budget is
                                                               too small
    dropped the required document, said `below_threshold`   -> the ranker is
                                                               broken

Same outcome, two diagnoses. It exists only because
[ADR 0005](../../docs/adr/0005-selection-is-a-report.md) made every omission
carry its rule.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..domain.omission import OmissionRule
from ..domain.package import ContextPackage
from ..domain.selection import ContextItem
from .dataset import Case

__all__ = ["FLOORS", "CaseScore", "Floors", "Summary", "score_case", "summarise"]


@dataclass(frozen=True, slots=True)
class Floors:
    """A regression gate, not a target.

    Floors exist so that a change that makes retrieval worse turns the build
    red. They are deliberately looser than the current numbers: a floor set at
    today's score makes every improvement a new floor and every honest
    experiment a build failure, and tuning to reach a threshold is the failure
    `mamori`'s ADR-0023 records.

    Budget adherence and reproducibility have no floor because they are
    invariants. One failure is a bug.
    """

    #: Required facts that must be found. Currently 100%.
    evidence_recall: float = 0.95
    #: Forbidden facts that may get in. Currently 10%, and the residual is
    #: diagnosed in ``application/search.py``.
    trap_rate: float = 0.20
    #: Exclusions that must name the right rule. Currently 90%. It was
    #: structurally 0% until redundancy marking existed, which is how it came
    #: to be the number that asked for that feature.
    omission_correctness: float = 0.70

    def breached_by(self, summary: Summary) -> list[str]:
        """What is below the floor, in words a build log can print."""
        problems: list[str] = []
        recall = summary.evidence_recall
        if recall is not None and recall < self.evidence_recall:
            problems.append(
                f"evidence recall {recall:.1%} is below the floor of {self.evidence_recall:.0%}"
            )
        traps = summary.trap_rate
        if traps is not None and traps > self.trap_rate:
            problems.append(f"trap rate {traps:.1%} is above the ceiling of {self.trap_rate:.0%}")
        explained = summary.omission_correctness
        if explained is not None and explained < self.omission_correctness:
            problems.append(
                f"omission correctness {explained:.1%} is below the floor of "
                f"{self.omission_correctness:.0%}"
            )
        if summary.over_budget:
            problems.append(f"over budget: {', '.join(summary.over_budget)}")
        if summary.unreproducible:
            problems.append(f"not reproducible: {', '.join(summary.unreproducible)}")
        return problems


#: The floors CI checks.
FLOORS = Floors()


@dataclass(frozen=True, slots=True)
class CaseScore:
    """What one case says about one package."""

    case_id: str
    genre: str
    language: str
    #: ``handwritten`` or ``drafted``. Carried so the trap rate can be split
    #: by who chose the vocabulary.
    origin: str = "handwritten"

    #: Required facts that reached the package.
    found: tuple[str, ...] = ()
    #: Required facts that did not.
    missed: tuple[str, ...] = ()
    #: Forbidden facts that got in anyway.
    sprung: tuple[str, ...] = ()
    #: How many were forbidden in total, so a rate can be computed without
    #: carrying a list of the ones that behaved.
    forbidden: int = 0
    #: Missed or excluded facts whose omission named the expected rule.
    explained: tuple[str, ...] = ()
    #: ...and those whose omission named a different rule, or none at all.
    misexplained: tuple[tuple[str, str], ...] = ()

    items: int = 0
    relevant_items: int = 0
    within_budget: bool = True
    reproducible: bool = True
    #: The corpus did not hold the answer and the package sent context anyway.
    #:
    #: **Reported, not gated.** tsumugi has no way to say "nothing here answers
    #: this": a package is passages that bear on a question, and documents
    #: about the right subject do bear on it. Saying the answer is absent is a
    #: semantic judgement, and the instruction set leaves it to the model
    #: ("if the context does not answer the question, say so plainly"). The
    #: number is kept because the gap is worth watching, not because failing it
    #: is a regression.
    answered_the_unanswerable: bool = False

    @property
    def evidence_recall(self) -> float | None:
        required = len(self.found) + len(self.missed)
        return len(self.found) / required if required else None

    @property
    def evidence_precision(self) -> float | None:
        return self.relevant_items / self.items if self.items else None

    @property
    def omission_correctness(self) -> float | None:
        accounted = len(self.explained) + len(self.misexplained)
        return len(self.explained) / accounted if accounted else None

    @property
    def clean(self) -> bool:
        """Everything required present, nothing forbidden, budget kept."""
        return not self.missed and not self.sprung and self.within_budget and self.reproducible


@dataclass(slots=True)
class Summary:
    """Aggregates, and the per-genre and per-language breakdowns.

    Reported per genre and per language as well as in aggregate, because an
    aggregate hides that the ranker is excellent on Japanese prose and useless
    on source code -- and those are different problems.
    """

    cases: int = 0
    clean: int = 0
    found: int = 0
    required: int = 0
    sprung: int = 0
    forbidden: int = 0
    relevant_items: int = 0
    items: int = 0
    explained: int = 0
    accounted: int = 0
    over_budget: tuple[str, ...] = ()
    unreproducible: tuple[str, ...] = ()
    answered_the_unanswerable: tuple[str, ...] = ()
    by_genre: dict[str, tuple[int, int]] = field(default_factory=dict)
    by_language: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: Sprung and forbidden, per ``handwritten`` / ``drafted``. Trap rate is
    #: the metric worth splitting: it is the one that moved when vocabulary
    #: somebody else chose entered the corpus.
    traps_by_origin: dict[str, tuple[int, int]] = field(default_factory=dict)

    def _ratio(self, part: int, whole: int) -> float | None:
        return part / whole if whole else None

    @property
    def evidence_recall(self) -> float | None:
        return self._ratio(self.found, self.required)

    @property
    def evidence_precision(self) -> float | None:
        return self._ratio(self.relevant_items, self.items)

    @property
    def trap_rate(self) -> float | None:
        return self._ratio(self.sprung, self.forbidden)

    @property
    def omission_correctness(self) -> float | None:
        return self._ratio(self.explained, self.accounted)

    def describe(self) -> str:
        def percent(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.1%}"

        lines = [
            f"{self.cases} cases, {self.clean} clean",
            f"  evidence recall      {percent(self.evidence_recall)}"
            f"  ({self.found}/{self.required} required facts present)",
            f"  evidence precision   {percent(self.evidence_precision)}"
            f"  ({self.relevant_items}/{self.items} items are labelled relevant)",
            f"  trap rate            {percent(self.trap_rate)}"
            f"  ({self.sprung}/{self.forbidden} forbidden facts got in)",
            f"  omission correctness {percent(self.omission_correctness)}"
            f"  ({self.explained}/{self.accounted} exclusions named the right rule)",
        ]
        # Budget adherence and reproducibility are invariants, not scores. Any
        # failure is a bug, so they are named rather than averaged.
        if self.over_budget:
            lines.append(f"  OVER BUDGET: {', '.join(self.over_budget)}")
        if self.unreproducible:
            lines.append(f"  NOT REPRODUCIBLE: {', '.join(self.unreproducible)}")
        if self.answered_the_unanswerable:
            # An observation, not a failure. See CaseScore.
            lines.append(
                f"  note: {len(self.answered_the_unanswerable)} unanswerable questions "
                f"still returned context. tsumugi cannot say the corpus lacks an "
                f"answer; the instruction set asks the model to."
            )
        if not self.over_budget and not self.unreproducible:
            lines.append("  budget adherence and reproducibility: held on every case")

        if len(self.traps_by_origin) > 1:
            # Split, and only when there is something to split. A corpus
            # written by the same hand as the ranker is a mirror; a number
            # that mixes the two cannot say which half it is reporting.
            lines.append("  trap rate by who chose the vocabulary:")
            for origin, (sprung, forbidden) in sorted(self.traps_by_origin.items()):
                rate = f"{sprung / forbidden:.1%}" if forbidden else "n/a"
                lines.append(f"    {origin:<12} {rate:>6}  ({sprung}/{forbidden})")

        for label, table in (("genre", self.by_genre), ("language", self.by_language)):
            if len(table) > 1:
                lines.append(f"  by {label}:")
                for name, (clean, total) in sorted(table.items()):
                    lines.append(f"    {name:<22} {clean}/{total} clean")
        return "\n".join(lines)


def score_case(
    case: Case, package: ContextPackage, *, rebuilt: ContextPackage | None = None
) -> CaseScore:
    """Compare a package against what the case says should be in it."""
    covered = _covered_facts(case, package)

    found = tuple(f for f in case.must_include if f in covered)
    missed = tuple(f for f in case.must_include if f not in covered)
    sprung = tuple(f for f in case.must_not_include if f in covered)

    # Every trap that declares an expected rule is scored, not only the facts
    # named required or forbidden. A `superseded` document is meant to be
    # *marked* rather than kept out (ADR-0008), so it appears in neither list --
    # and skipping it was making this metric measure nothing at all.
    explained: list[str] = []
    misexplained: list[tuple[str, str]] = []
    accountable = {
        *missed,
        *(f for f in case.must_not_include if f not in covered),
        *(f for f, trap in case.traps.items() if trap.expect_omission_rule is not None),
    }
    for fact_id in sorted(accountable):
        expected = _expected_rule(case, fact_id)
        if expected is None:
            continue
        actual = _rule_that_dropped(case, package, fact_id)
        if actual == expected:
            explained.append(fact_id)
        else:
            misexplained.append((fact_id, actual or "not reported at all"))

    relevant = sum(1 for item in package.items if _overlaps_any_fact(case, item))

    return CaseScore(
        case_id=case.case_id,
        genre=case.genre,
        language=case.language,
        origin=case.origin,
        found=found,
        missed=missed,
        sprung=sprung,
        forbidden=len(case.must_not_include),
        explained=tuple(explained),
        misexplained=tuple(misexplained),
        items=len(package.items),
        relevant_items=relevant,
        within_budget=package.budget.estimate <= case.budget.limit,
        reproducible=rebuilt is None or rebuilt.package_id == package.package_id,
        answered_the_unanswerable=case.is_unanswerable and bool(package.items),
    )


def summarise(scores: Sequence[CaseScore]) -> Summary:
    summary = Summary(cases=len(scores))
    for score in scores:
        summary.clean += int(score.clean)
        summary.found += len(score.found)
        summary.required += len(score.found) + len(score.missed)
        summary.sprung += len(score.sprung)
        summary.forbidden += score.forbidden
        summary.relevant_items += score.relevant_items
        summary.items += score.items
        summary.explained += len(score.explained)
        summary.accounted += len(score.explained) + len(score.misexplained)
        if not score.within_budget:
            summary.over_budget = (*summary.over_budget, score.case_id)
        if not score.reproducible:
            summary.unreproducible = (*summary.unreproducible, score.case_id)
        if score.answered_the_unanswerable:
            summary.answered_the_unanswerable = (
                *summary.answered_the_unanswerable,
                score.case_id,
            )

        for table, key in ((summary.by_genre, score.genre), (summary.by_language, score.language)):
            clean, total = table.get(key, (0, 0))
            table[key] = (clean + int(score.clean), total + 1)

        sprung, forbidden = summary.traps_by_origin.get(score.origin, (0, 0))
        summary.traps_by_origin[score.origin] = (
            sprung + len(score.sprung),
            forbidden + score.forbidden,
        )
    return summary


def _covered_facts(case: Case, package: ContextPackage) -> set[str]:
    """Facts the package actually delivered, by span or by verbatim text.

    The second clause exists because a ``near_duplicate`` case plants the
    answer twice, in two files, character for character. When the budget binds,
    the copy can outrank the original and the original is then dropped as
    ``redundant_candidate`` -- and the reader has the fact, from a real
    document, with a valid anchor. Scoring that as *missing* would measure
    which of two identical passages won a tie.

    Exact text only. No containment, no normalisation: a superseded version
    differs in the value and a near-miss in the subject, so neither can be
    satisfied this way.
    """
    covered: set[str] = set()
    for fact_id, fact in case.facts.items():
        if any(fact.text in item.text for item in package.items):
            covered.add(fact_id)
    for item in package.items:
        document = case.document_for(item.source_path)
        if document is None:
            continue
        for fact_id, fact in case.facts.items():
            if case.fact_document.get(fact_id) != document:
                continue
            if item.anchor.span.contains(fact.span):
                covered.add(fact_id)
    return covered


def _overlaps_any_fact(case: Case, item: ContextItem) -> bool:
    document = case.document_for(item.source_path)
    if document is None:
        return False
    return any(
        item.anchor.span.overlaps(fact.span)
        for fact_id, fact in case.facts.items()
        if case.fact_document.get(fact_id) == document
    )


def _expected_rule(case: Case, fact_id: str) -> str | None:
    trap = case.trap_for(fact_id)
    if trap is not None and trap.expect_omission_rule is not None:
        return trap.expect_omission_rule.value
    return None


def _rule_that_dropped(case: Case, package: ContextPackage, fact_id: str) -> str | None:
    """The rule of the omission covering this fact, if any.

    A twin satisfies it. Where a case plants the same sentence in two files --
    which is what a ``near_duplicate`` case *is* -- the redundancy rule fires
    on whichever of the pair ranks lower, and which one that is depends on
    document length, heading repetition and other things that have nothing to
    do with the rule being tested. ADR-0015 is explicit that redundancy does
    not decide which duplicate is right, so a case that insisted on one of them
    would be asserting an outcome the design refuses to promise.

    Requiring the *pair* to be reported keeps the test and keeps it honest.

    An included item that carries ``redundant_with`` satisfies it too, and that
    is ADR-0008 rather than leniency: **redundancy is marked, never removed.**
    A duplicate that fits is sent, marked; a duplicate that does not fit is
    reported under ``redundant_candidate``. Both are the rule firing, and a
    case that accepted only the second was testing the budget, not the rule.
    """
    fact = case.facts[fact_id]
    document = case.fact_document[fact_id]
    twins = {
        other_id
        for other_id, other in case.facts.items()
        if other.text == fact.text and other_id != fact_id
    }
    wanted = {document} | {case.fact_document[twin] for twin in twins}

    for item in package.items:
        source = case.document_for(item.source_path)
        if source not in wanted or item.selection is None:
            continue
        if any(signal.startswith("redundant_with:") for signal in item.selection.signals):
            return OmissionRule.REDUNDANT_CANDIDATE.value

    for omission in package.omissions:
        source = case.document_for(omission.source_path)
        if source not in wanted:
            continue
        if source == document and omission.span.overlaps(fact.span):
            return omission.rule.value
        if source != document:
            twin = next(t for t in twins if case.fact_document[t] == source)
            if omission.span.overlaps(case.facts[twin].span):
                return omission.rule.value
    return None
