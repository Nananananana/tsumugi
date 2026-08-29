"""A case: a small corpus, a question, and the evidence that answers it.

The corpus is generated; the labels are computed; **nothing labels the ideal
output** (ADR-0013). There is no single correct structured prompt for a
question, so scoring distance to a chosen one measures conformity and punishes
any improvement that looks different.

On disk::

    cases/ja-0142-mountaineering/
    ├── case.json
    └── corpus/
        ├── 2025-04-12-装備メモ.md      # carries {{F:...}} markup
        └── ...

Loading strips the markup, computes the fact spans, and materialises a clean
corpus that ``tsumugi ingest`` can read.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.budget import Budget, Unit
from ..domain.omission import OmissionRule
from .markup import PlantedFact, strip_markup

__all__ = ["Case", "Trap", "load_case", "load_cases"]

#: The kinds of adversary a case may plant. The genres are decoration; a
#: generated corpus of relevant documents measures nothing, because every
#: retriever passes it.
TRAP_KINDS = frozenset(
    {
        "lexical_near_miss",
        "near_duplicate",
        "superseded",
        "budget_squeeze",
        "absent_answer",
        "mixed_script",
        "stale_anchor",
    }
)


@dataclass(frozen=True, slots=True)
class Trap:
    """A planted adversary, and the rule that should catch it."""

    kind: str
    #: The omission rule the package is expected to name. ``None`` where the
    #: trap only has to stay out and the route does not matter.
    expect_omission_rule: OmissionRule | None = None

    def __post_init__(self) -> None:
        if self.kind not in TRAP_KINDS:
            raise ValueError(
                f"unknown trap kind {self.kind!r}; expected one of {sorted(TRAP_KINDS)}"
            )


@dataclass(frozen=True, slots=True)
class Case:
    """One evaluation case, loaded and checked."""

    case_id: str
    genre: str
    language: str
    question: str
    budget: Budget
    #: Facts that must reach the package.
    must_include: tuple[str, ...]
    #: Facts that must not.
    must_not_include: tuple[str, ...]
    traps: Mapping[str, Trap]
    #: ``train`` or ``held_out``. Held-out cases are not read while tuning.
    split: str
    #: ``ci`` (fast, every commit) or ``full`` (nightly, and before a release).
    tier: str
    #: ``relative path -> stripped text``.
    documents: Mapping[str, str] = field(repr=False, default_factory=dict)
    #: Every planted fact, across every document.
    facts: Mapping[str, PlantedFact] = field(repr=False, default_factory=dict)
    #: ``fact id -> the document it was planted in``.
    fact_document: Mapping[str, str] = field(repr=False, default_factory=dict)
    #: ``relative path -> replacement text``, applied **after** ingest and
    #: before the package is built. This is how a ``stale_anchor`` case makes a
    #: document change under the index, which is the only way to exercise
    #: ADR-0010 end to end.
    edit_after_ingest: Mapping[str, str] = field(repr=False, default_factory=dict)

    def document_for(self, source_path: str) -> str | None:
        """Which of this case's documents a package's ``source_path`` names.

        One rule, in one place. It existed twice with two different answers --
        one matched any suffix, so ``other.md`` matched a document called
        ``her.md`` -- which is the kind of divergence that shows up much later
        as a model scoring better or worse than it did.

        Matching is on a path boundary: a package built from a temporary
        workspace carries an absolute or root-relative path, and the case knows
        only its own keys.
        """
        if source_path in self.documents:
            return source_path
        normalised = source_path.replace("\\", "/")
        for key in self.documents:
            wanted = key.replace("\\", "/")
            if normalised == wanted or normalised.endswith("/" + wanted):
                return key
        return None

    def __post_init__(self) -> None:
        if self.split not in {"train", "held_out"}:
            raise ValueError(f"{self.case_id}: unknown split {self.split!r}")
        if self.tier not in {"ci", "full"}:
            raise ValueError(f"{self.case_id}: unknown tier {self.tier!r}")
        if not self.question.strip():
            raise ValueError(f"{self.case_id}: no question")

        named = set(self.must_include) | set(self.must_not_include) | set(self.traps)
        missing = sorted(named - set(self.facts))
        if missing:
            raise ValueError(
                f"{self.case_id}: names facts that are not planted anywhere: {', '.join(missing)}"
            )
        both = set(self.must_include) & set(self.must_not_include)
        if both:
            raise ValueError(
                f"{self.case_id}: {', '.join(sorted(both))} is both required and forbidden"
            )
        # A case has to assert *something*. Requiring a fact is one way; so is
        # forbidding one, and so is expecting a named rule for an exclusion --
        # which is how a `budget_squeeze` case works, where the answer is meant
        # to be reported rather than included. An earlier version of this rule
        # only knew about the first two and rejected every squeeze case, which
        # is the oracle catching a rule rather than a case.
        asserts_something = (
            bool(self.must_include)
            or bool(self.must_not_include)
            or any(trap.expect_omission_rule is not None for trap in self.traps.values())
            or self.is_unanswerable
        )
        if not asserts_something:
            raise ValueError(
                f"{self.case_id}: asserts nothing -- no required fact, no forbidden one, "
                f"no expected omission rule, and not an absent_answer case"
            )

    def materialise(self, into: Path) -> Path:
        """Write the stripped corpus somewhere ``ingest`` can read it.

                ``newline=""`` is load-bearing, not tidiness. Python translates ``
        ``
                to ``

        `` on write under Windows, while the spans were computed over
                the text as read. One byte per line of drift is enough to make every
                offset wrong, and the failure looks like a retrieval bug rather than a
                line-ending bug -- which is exactly how long it takes to find.
        """
        root = into / self.case_id
        if root.exists():
            shutil.rmtree(root)
        for relative, text in sorted(self.documents.items()):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        return root

    def trap_for(self, fact_id: str) -> Trap | None:
        return self.traps.get(fact_id)

    @property
    def is_unanswerable(self) -> bool:
        """The corpus does not hold the answer, and the package should say so
        rather than assembling something plausible."""
        return any(trap.kind == "absent_answer" for trap in self.traps.values())

    def apply_edits(self, root: Path) -> None:
        """Rewrite the documents a ``stale_anchor`` case edits."""
        for relative, text in sorted(self.edit_after_ingest.items()):
            with (root / relative).open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)


def load_case(directory: Path) -> Case:
    """Read one case, stripping the markup and computing the spans."""
    manifest_path = directory / "case.json"
    if not manifest_path.is_file():
        raise ValueError(f"{directory}: no case.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    documents: dict[str, str] = {}
    facts: dict[str, PlantedFact] = {}
    fact_document: dict[str, str] = {}

    corpus = directory / "corpus"
    if not corpus.is_dir():
        raise ValueError(f"{directory}: no corpus/ directory")

    for path in sorted(corpus.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(corpus).as_posix()
        plain, planted = strip_markup(path.read_text(encoding="utf-8"))
        documents[relative] = plain
        for fact_id, fact in planted.items():
            if fact_id in facts:
                raise ValueError(
                    f"{directory.name}: fact {fact_id!r} is planted in both "
                    f"{fact_document[fact_id]} and {relative}"
                )
            facts[fact_id] = fact
            fact_document[fact_id] = relative

    budget = manifest.get("budget") or {"unit": "characters", "limit": 2000}
    traps = {
        fact_id: Trap(
            kind=raw["kind"],
            expect_omission_rule=(
                OmissionRule.parse(raw["expect_omission_rule"])
                if raw.get("expect_omission_rule")
                else None
            ),
        )
        for fact_id, raw in (manifest.get("traps") or {}).items()
    }

    return Case(
        case_id=manifest.get("case_id", directory.name),
        genre=manifest.get("genre", "unknown"),
        language=manifest.get("language", "unknown"),
        question=manifest["question"],
        budget=Budget(Unit(budget["unit"]), budget["limit"]),
        must_include=tuple(manifest.get("must_include", ())),
        must_not_include=tuple(manifest.get("must_not_include", ())),
        traps=traps,
        split=manifest.get("split", "train"),
        tier=manifest.get("tier", "full"),
        documents=documents,
        facts=facts,
        fact_document=fact_document,
        edit_after_ingest=manifest.get("edit_after_ingest") or {},
    )


def load_cases(root: Path, *, tier: str | None = None, split: str | None = None) -> list[Case]:
    """Every case under ``root``, optionally narrowed to a tier or a split."""
    cases = [load_case(directory) for directory in _case_directories(root)]
    if tier is not None:
        cases = [case for case in cases if case.tier == tier]
    if split is not None:
        cases = [case for case in cases if case.split == split]
    # Sorted, so a run over the same directory is the same run.
    return sorted(cases, key=lambda case: case.case_id)


def _case_directories(root: Path) -> Iterator[Path]:
    if (root / "case.json").is_file():
        yield root
        return
    for directory in sorted(root.iterdir()):
        if directory.is_dir() and (directory / "case.json").is_file():
            yield directory
