"""Running the cases: ingest, build, score, twice.

Each case gets a fresh index, so nothing leaks between them and a case that
passes because a previous one warmed something is impossible.

Every package is built **twice** and the two ids compared. Reproducibility is
an invariant rather than a metric (ADR-0003), and the cheapest place to check
it is where a package is being built anyway.

No model runs here. The fixtures were authored once and committed; CI reads
files (ADR-0013). ``answering.py`` is the opt-in half that does run one, and
it borrows ``prepared_case`` from here so that both halves measure a case
materialised exactly one way.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from ..application.build_context import build_context
from ..application.ingest import ingest_paths
from ..domain.budget import Unit
from ..domain.package import ContextPackage
from ..infrastructure.cost.heuristic import ByteCost, CharacterCost, HeuristicTokenCost
from ..infrastructure.filesystem import walk
from ..infrastructure.freshness import FilesystemFreshness
from ..infrastructure.index.fts import FtsIndex
from ..infrastructure.parsers import parser_for
from ..infrastructure.storage.database import connect
from ..infrastructure.storage.sqlite import SqliteDocumentStore
from ..ports.cost import CostModel
from .dataset import Case
from .scoring import CaseScore, score_case

__all__ = ["cost_model_for", "prepared_case", "run_case", "run_cases"]


def cost_model_for(unit: Unit) -> CostModel:
    if unit is Unit.TOKENS:
        return HeuristicTokenCost()
    if unit is Unit.BYTES:
        return ByteCost()
    return CharacterCost()


@contextmanager
def prepared_case(
    case: Case,
) -> Iterator[tuple[SqliteDocumentStore, FtsIndex, Path]]:
    """One case, ingested into a fresh index, cleaned up afterwards.

    A fresh index per case, so nothing leaks between them and a case that
    passes because a previous one warmed something is impossible.
    """
    with tempfile.TemporaryDirectory() as workspace:
        root = case.materialise(Path(workspace) / "corpus")
        connection = connect(Path(workspace) / "index.db")
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)

        found = walk(root)
        ingest_paths(found.files, root=root, store=store, index=index, parser_for=parser_for)

        # A stale_anchor case edits a document after it was indexed. Nothing
        # re-ingests: the point is that the index holds what it read and the
        # anchors into it are reported as historical (ADR-0010).
        case.apply_edits(root)

        try:
            yield store, index, root
        finally:
            # Windows will not delete a file that is still open, and the
            # workspace is about to go.
            connection.close()


def run_case(case: Case, *, candidate_limit: int = 50) -> CaseScore:
    """Build a package for one case and score it."""
    with prepared_case(case) as (store, index, root):

        def build() -> ContextPackage:
            return build_context(
                case.question,
                store=store,
                index=index,
                cost_model=cost_model_for(case.budget.unit),
                budget=case.budget,
                candidate_limit=candidate_limit,
                version="eval",
                freshness=FilesystemFreshness(root),
            )

        package = build()
        rebuilt = build()

    return score_case(case, package, rebuilt=rebuilt)


def run_cases(cases: Sequence[Case], *, candidate_limit: int = 50) -> list[CaseScore]:
    return [run_case(case, candidate_limit=candidate_limit) for case in cases]
