"""What `build_context` decides about a candidate before the budget sees it.

`python tools/mutate.py src/tsumugi/application/build_context.py` left four
survivors of seven, and the module they are in is the one that turns a search
result into evidence. Every one of them was a boolean or a default that only
matters in a case the suite had never built:

- an **unconfirmed** candidate -- the index proposed the document, no exact
  occurrence of the question was found in it. Nothing in the suite produced
  one, though the source calls it "the lexical-near-miss trap [that] sprang on
  29 of 30 cases";
- a candidate that is **unconfirmed and out of date at once**, where two
  disqualifications compete and only one of them is useful to the reader;
- a document the store no longer has but the index still proposes;
- a document that **declares its own producer**, which is the mechanism
  `docs/architecture.md` names for keeping a reading of somebody's photographs
  from becoming a fact by crossing a library boundary.

The corpus here is two documents that share the *characters* of the question
and only one of which answers it. That is the shape the index over-generates
on, and it is the only shape in which any of this is visible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tsumugi.application.build_context import build_context
from tsumugi.application.ingest import ingest_paths
from tsumugi.domain.budget import Budget
from tsumugi.domain.omission import OmissionRule
from tsumugi.domain.package import ContextPackage
from tsumugi.domain.selection import Layer
from tsumugi.infrastructure.cost.heuristic import CharacterCost
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.freshness import FilesystemFreshness
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

NL = chr(10)
QUESTION = "テントの重量は"

#: Shares `テント` with the question and answers none of it. The index proposes
#: it on shared bigrams; confirmation finds no occurrence of the whole question.
HALF = "# 設営" + NL * 2 + "テントを畳む手順はここに書く。ペグは八本。" + NL

#: Both content terms of the question, so it confirms.
WHOLE = "# 装備" + NL * 2 + "テントの重量は2.4kg、二人用。" + NL

#: A producer naming itself, which is the whole of the layer crossing.
READING = (
    "---"
    + NL
    + "layer: interpretation"
    + NL
    + "producer: kiseki.export/1"
    + NL
    + "confidence: 0.7"
    + NL
    + "---"
    + NL * 2
    + "写真から、テントの重量は2.4kg程度と読み取れる。"
    + NL
)

Corpus = tuple[Path, SqliteDocumentStore, FtsIndex]


@pytest.fixture
def corpus(tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex) -> Corpus:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "half.md").write_text(HALF, encoding="utf-8", newline="")
    (root / "whole.md").write_text(WHOLE, encoding="utf-8", newline="")
    ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)
    return root, store, index


def _build(store: SqliteDocumentStore, index: FtsIndex, **kwargs: Any) -> ContextPackage:
    kwargs.setdefault("budget", Budget.characters(2000))
    return build_context(QUESTION, store=store, index=index, cost_model=CharacterCost(), **kwargs)


class TestACandidateTheIndexProposedAndConfirmationRefused:
    def test_it_becomes_an_omission_rather_than_an_item(self, corpus: Corpus) -> None:
        """ADR-0007: the index over-generates and confirmation decides.

        Before this rule, an unconfirmed candidate became an item covering the
        head of its document, which dragged whole unrelated documents into
        packages. It is reported rather than dropped, because a package that
        silently discards what retrieval proposed reads as having considered
        everything.
        """
        _, store, index = corpus
        package = _build(store, index)

        assert [Path(item.source_path).name for item in package.items] == ["whole.md"]
        (dropped,) = package.omissions
        assert dropped.rule is OmissionRule.BELOW_THRESHOLD
        assert Path(dropped.source_path).name == "half.md"
        assert "no exact occurrence" in dropped.reason

    def test_the_reason_survives_the_file_going_out_of_date(self, corpus: Corpus) -> None:
        """Two disqualifications compete, and the first one made wins.

        `if disqualified is None and document is not None` -- mutate the `and`
        to `or` and the staleness check runs over a candidate that already had
        a reason, overwriting "no occurrence of the question was confirmed
        here" with "the file has changed since it was indexed".

        Those tell a reader to do opposite things. The second says *re-ingest
        and it will come back*; it will not, because it was never confirmed.
        A wrong reason is worse than a coarse one, and the account is this
        package's whole claim.
        """
        root, store, index = corpus
        (root / "half.md").write_text(
            "# 設営" + NL * 2 + "全面改稿。何も残っていない。" + NL,
            encoding="utf-8",
            newline="",
        )

        package = _build(store, index, freshness=FilesystemFreshness(root))

        (dropped,) = package.omissions
        assert Path(dropped.source_path).name == "half.md"
        assert dropped.rule is OmissionRule.BELOW_THRESHOLD, dropped.reason
        assert "no exact occurrence" in dropped.reason

    def test_a_stale_confirmed_candidate_still_reports_staleness(self, corpus: Corpus) -> None:
        """The positive control for the test above.

        Without it that test passes over a `disqualified` that was never None,
        and would keep passing if the staleness check were deleted outright.
        """
        root, store, index = corpus
        (root / "whole.md").write_text(
            "# 装備" + NL * 2 + "テントの重量は3.1kgに訂正。" + NL,
            encoding="utf-8",
            newline="",
        )

        package = _build(store, index, freshness=FilesystemFreshness(root))

        stale = [o for o in package.omissions if o.rule is OmissionRule.STALE_ANCHOR]
        assert [Path(o.source_path).name for o in stale] == ["whole.md"]
        assert "was true in the version that was read" in stale[0].reason


class TestWhenTheStoreAndTheIndexDisagree:
    def test_a_document_the_store_forgot_does_not_stop_the_package(self, corpus: Corpus) -> None:
        """`document is not None` guards a `resolve` that needs the text.

        Forgetting a document from the store without reindexing is what a
        half-finished `forget` leaves behind, and the index goes on proposing
        it. Resolving an anchor against nothing is not a report a reader can
        act on; it is an `AttributeError` in the middle of building a package
        that would otherwise have been fine.
        """
        _, store, index = corpus
        gone = store.by_path("half.md")
        assert gone is not None
        assert store.forget(gone.document_id)

        package = _build(store, index)

        assert [Path(item.source_path).name for item in package.items] == ["whole.md"]
        assert package.package_id


class TestADocumentThatSaysWhatItIs:
    def test_a_declared_producer_reaches_the_item(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        """`metadata.get("producer") or "tsumugi.ingest/1"`.

        Mutated to `and`, a declared producer is replaced by tsumugi's own
        name -- exactly when one is declared, and only then. The package would
        say a reading of somebody's photographs came from `tsumugi.ingest`,
        which is the laundering `docs/architecture.md` describes and the reason
        the field is read off the document rather than assigned above it.
        """
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "reading.md").write_text(READING, encoding="utf-8", newline="")
        ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)

        package = _build(store, index)
        (item,) = package.items
        assert item.provenance.layer is Layer.INTERPRETATION
        assert item.provenance.producer == "kiseki.export/1"
        assert item.provenance.confidence == pytest.approx(0.7)
        assert "interpretation" in package.render()

    def test_a_document_that_declares_no_producer_gets_tsumugis_name(self, corpus: Corpus) -> None:
        """The positive control: the default is a default, not the only value."""
        _, store, index = corpus
        (item,) = _build(store, index).items
        assert item.provenance.layer is Layer.FACT
        assert item.provenance.producer == "tsumugi.ingest/1"


class TestTheCapThePackageReports:
    def test_the_default_candidate_limit_is_the_number_the_package_names(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        """`candidate_limit: int = 50`, and the number is not private.

        It is printed into every truncated package -- "retrieval returned its
        cap of 50 candidates" -- so the default and the account have to be the
        same number. Changing one without the other makes a package state a cap
        that was not the cap applied, which is the one thing this omission
        exists to prevent.
        """
        root = tmp_path / "corpus"
        root.mkdir()
        for n in range(60):
            (root / f"note{n:02d}.md").write_text(
                f"# 記録{n}" + NL * 2 + f"テントの重量は2.4kg。第{n}稿。" + NL,
                encoding="utf-8",
                newline="",
            )
        ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)

        package = _build(store, index, budget=Budget.characters(200))

        (capped,) = [o for o in package.omissions if o.rule is OmissionRule.TRUNCATED_BY_CAP]
        assert "cap of 50 candidates" in capped.reason


#: Every content term of the question (`テント`, `重量`) and never the phrase.
#: Confirmation accepts it by *coverage* rather than by finding a run, and
#: `search` sets `matched = 0` on that path -- there is no matched run to count.
SCATTERED = "# 装備" + NL * 2 + "テントは二人用。重量については別段で触れる。" + NL


class TestTheShareOfTheQuestionThatWasConfirmed:
    """`confirmed_share` separates two things a bare `confirmed_in_text`
    cannot: a median of 0.91 where an answer exists and 0.44 where the question
    is unanswerable and a partial phrase matched anyway. It is reported and
    never thresholded, so a consumer's own cut-off is only as good as it is.
    """

    def test_a_passage_confirmed_by_coverage_carries_no_share(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        """`if result.matched and query`, and the `matched` half is the live one.

        Confirmation has two paths. One finds a run of the question and counts
        it; the other accepts a passage because every content term is present,
        and sets `matched = 0` because there is no run to count. Mutate the
        `and` to `or` and the second path emits `confirmed_share:0.00` --
        which does not mean "not measured", it means *none of the question was
        confirmed here*, on a passage that was confirmed.

        A consumer thresholding at 0.5, as the source describes, would drop it
        below every unanswerable case in the corpus. Absent is the honest
        value: this path has no share to report.
        """
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "scattered.md").write_text(SCATTERED, encoding="utf-8", newline="")
        ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)

        (item,) = _build(store, index).items
        assert item.selection is not None
        signals = list(item.selection.signals)
        assert "confirmed_in_text" in signals
        assert not [s for s in signals if s.startswith("confirmed_share:")], signals

    def test_a_passage_confirmed_by_a_run_reports_its_share(self, corpus: Corpus) -> None:
        """The positive control. Without it the test above is satisfied by a
        `confirmed_share` that is never emitted at all."""
        _, store, index = corpus
        (item,) = _build(store, index).items
        assert item.selection is not None
        (share,) = [s for s in item.selection.signals if s.startswith("confirmed_share:")]
        assert share == "confirmed_share:1.00"
