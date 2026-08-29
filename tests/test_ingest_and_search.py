"""Reading a folder, finding things in it, and going backwards.

The theme: nothing is skipped silently. A run that says "indexed 412" and
nothing else cannot be told apart from one that quietly excluded half the
folder (ADR-0005).
"""

from __future__ import annotations

from pathlib import Path

from tsumugi.application.ingest import ingest_paths
from tsumugi.application.search import search
from tsumugi.application.trace import trace_anchor, trace_quotation
from tsumugi.domain.anchor import Anchor, ResolutionStatus
from tsumugi.domain.span import Span
from tsumugi.infrastructure.filesystem import IgnoreRules, walk
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore


def _ingest(corpus: Path, store: SqliteDocumentStore, index: FtsIndex) -> object:
    found = walk(corpus)
    report = ingest_paths(found.files, root=corpus, store=store, index=index, parser_for=parser_for)
    for entry in found.skipped:
        report.skipped.append((entry.path.as_posix(), entry.reason))
    return report


class TestWalking:
    def test_it_finds_the_documents(self, corpus: Path) -> None:
        found = walk(corpus)
        names = {p.name for p in found.files}
        assert {"mountain.md", "budget.md", "config.json"} <= names

    def test_a_credential_file_is_refused_and_named(self, corpus: Path) -> None:
        # The owner did not ask for this to be skipped and would want to know
        # that something looked like a key.
        found = walk(corpus)
        refused = [s for s in found.skipped if s.path.name == ".env"]
        assert refused and "credential" in refused[0].reason
        assert ".env" not in {p.name for p in found.files}

    def test_an_ignore_rule_is_honoured_and_reported(self, corpus: Path) -> None:
        found = walk(corpus)
        ignored = [s for s in found.skipped if s.path.name == "scratch.tmp"]
        assert ignored and ignored[0].rule == "*.tmp"

    def test_a_negation_rescues_a_file(self, tmp_path: Path) -> None:
        rules = IgnoreRules(["*.tmp", "!keep.tmp"])
        assert rules.matched_by(Path("a.tmp"), is_directory=False) == "*.tmp"
        assert rules.matched_by(Path("keep.tmp"), is_directory=False) is None

    def test_a_symlink_loop_does_not_hang_the_walk(self, tmp_path: Path) -> None:
        root = tmp_path / "loop"
        root.mkdir()
        (root / "a.md").write_text("x", encoding="utf-8")
        try:
            (root / "self").symlink_to(root, target_is_directory=True)
        except (OSError, NotImplementedError):
            return  # Windows without developer mode; the guard is still there.
        assert [p.name for p in walk(root).files] == ["a.md"]


class TestIngest:
    def test_a_first_run_adds_everything(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        report = _ingest(corpus, store, index)
        assert len(report.added) == 3  # type: ignore[attr-defined]
        assert store.count() == 3

    def test_a_second_run_changes_nothing(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        _ingest(corpus, store, index)
        again = _ingest(corpus, store, index)
        assert again.added == []  # type: ignore[attr-defined]
        assert len(again.unchanged) == 3  # type: ignore[attr-defined]

    def test_an_edit_is_reported_as_a_revision_not_as_new(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        _ingest(corpus, store, index)
        note = corpus / "notes" / "budget.md"
        note.write_text(note.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")

        again = _ingest(corpus, store, index)
        assert len(again.revised) == 1  # type: ignore[attr-defined]
        assert again.added == []  # type: ignore[attr-defined]

    def test_source_paths_are_relative_to_the_corpus(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        # So that an index built on one machine describes the same corpus on
        # another, and a document id survives the folder moving.
        _ingest(corpus, store, index)
        paths = {d.source_path for d in store.all_current()}
        assert paths == {"notes/mountain.md", "notes/budget.md", "notes/config.json"}

    def test_a_binary_file_is_reported_as_failed_not_ignored(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        (corpus / "notes" / "broken.txt").write_bytes(b"\x00\x01\x02binary")
        report = _ingest(corpus, store, index)
        assert any("binary" in reason for _, reason in report.failed)  # type: ignore[attr-defined]

    def test_a_byte_order_mark_does_not_shift_every_offset(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        (corpus / "notes" / "bom.md").write_bytes("# Title\n\nbody\n".encode("utf-8-sig"))
        _ingest(corpus, store, index)
        document = store.by_path("notes/bom.md")
        assert document is not None
        assert document.content.startswith("# Title")

    def test_an_unparseable_json_file_is_named(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        (corpus / "notes" / "bad.json").write_text("{oh no", encoding="utf-8")
        report = _ingest(corpus, store, index)
        assert any("bad.json" in path for path, _ in report.failed)  # type: ignore[attr-defined]


class TestSearch:
    def test_a_two_character_japanese_query_finds_the_right_section(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        _ingest(corpus, store, index)
        results, _ = search("東京", store=store, index=index)
        assert results
        assert results[0].source_path == "notes/mountain.md"
        assert results[0].section == "燃料"

    def test_every_result_carries_a_resolvable_anchor(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        # The invariant that makes a result evidence rather than a snippet.
        from tsumugi.domain.anchor import resolve

        _ingest(corpus, store, index)
        results, _ = search("テント", store=store, index=index)
        assert results
        for result in results:
            document = store.get(result.anchor.document_id, result.anchor.version)
            assert document is not None
            assert resolve(result.anchor, document).status is ResolutionStatus.RESOLVED
            assert result.text == result.anchor.span.slice(document.content)

    def test_a_cap_that_bound_the_search_is_reported(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        # A silent cap reads as "everything was considered". ADR-0005.
        _ingest(corpus, store, index)
        _, truncated = search("テント", store=store, index=index, candidate_limit=1)
        assert truncated is not None
        assert "cap of 1" in truncated.as_omission_reason()

    def test_results_are_stable_across_runs(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        _ingest(corpus, store, index)
        first, _ = search("テント", store=store, index=index)
        second, _ = search("テント", store=store, index=index)
        assert [r.anchor for r in first] == [r.anchor for r in second]

    def test_nothing_relevant_returns_nothing(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        _ingest(corpus, store, index)
        results, _ = search("量子色力学", store=store, index=index)
        assert results == []


class TestTrace:
    def test_a_present_quotation_resolves(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        _ingest(corpus, store, index)
        traces = trace_quotation("テントは 2.4kg", store)
        assert len(traces) == 1
        assert traces[0].status is ResolutionStatus.RESOLVED
        assert traces[0].source_path == "notes/mountain.md"
        assert traces[0].section == "装備"

    def test_an_absent_quotation_resolves_to_nothing(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        # No fuzzy match. Reporting a near miss as found is the failure this
        # library exists to prevent (ADR-0004).
        _ingest(corpus, store, index)
        assert trace_quotation("テントは 3.9kg", store) == []

    def test_every_occurrence_is_reported_rather_than_one_chosen(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        # Ambiguity is information.
        (corpus / "notes" / "again.md").write_text("# また\n\nテントは 2.4kg。\n", encoding="utf-8")
        _ingest(corpus, store, index)
        traces = trace_quotation("テントは 2.4kg", store)
        assert {t.source_path for t in traces} == {"notes/mountain.md", "notes/again.md"}

    def test_it_reports_a_line_number(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        _ingest(corpus, store, index)
        trace = trace_quotation("The unit is explicit", store)[0]
        assert trace.line == 3

    def test_it_stops_at_the_limit_rather_than_returning_the_corpus(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        # A quotation common enough to be everywhere would otherwise walk the
        # whole store. The cap is a cap, not a ranking: the first N found.
        for n in range(4):
            (corpus / "notes" / f"copy-{n}.md").write_text(
                "# コピー\n\nテントは 2.4kg。\n", encoding="utf-8"
            )
        _ingest(corpus, store, index)
        assert len(trace_quotation("テントは 2.4kg", store, limit=2)) == 2


class TestTraceAnAnchor:
    """`trace_anchor` is what turns "the file changed" into a stale answer.

    A package holds anchors, not quotations, so this is the path taken when
    somebody asks where an *item* came from after the corpus moved on.
    """

    def test_it_resolves_against_the_version_it_was_taken_from(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        _ingest(corpus, store, index)
        anchor = trace_quotation("テントは 2.4kg", store)[0].resolution.anchor
        traced = trace_anchor(anchor, store)
        assert traced is not None
        assert traced.status is ResolutionStatus.RESOLVED
        assert traced.source_path == "notes/mountain.md"
        assert traced.current_version == "", "nothing has changed, so nothing to report"

    def test_an_edited_document_is_stale_and_says_so(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        # The whole reason for the fallback. Returning nothing here would turn
        # "this passage was true in the version I read" into "I have never
        # heard of it" (ADR-0010).
        _ingest(corpus, store, index)
        anchor = trace_quotation("テントは 2.4kg", store)[0].resolution.anchor

        (corpus / "notes" / "mountain.md").write_text(
            "# 装備\n\n前置きが増えた。\nテントは 2.4kg。\n", encoding="utf-8"
        )
        _ingest(corpus, store, index)

        traced = trace_anchor(anchor, store)
        assert traced is not None
        assert traced.current_version, "and it names the version that superseded it"
        assert traced.current_version != str(anchor.version)

    def test_an_unknown_document_is_none_rather_than_a_guess(
        self, store: SqliteDocumentStore
    ) -> None:
        from tsumugi.domain.hashing import ContentHash

        anchor = Anchor(
            document_id="doc_nothing",
            span=Span(0, 4),
            text_hash=ContentHash.of("text"),
            version=ContentHash.of("text"),
        )
        assert trace_anchor(anchor, store) is None

    def test_an_anchor_from_a_version_the_store_lost_falls_back(
        self, corpus: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        # The fallback exists for a store that no longer holds the revision an
        # anchor was taken from -- after a rebuild, or a forget. Returning
        # nothing would turn "this passage was true in a version I no longer
        # have" into "I have never heard of it", and the second is a lie.
        _ingest(corpus, store, index)
        found = trace_quotation("テントは 2.4kg", store)[0].resolution.anchor
        from tsumugi.domain.hashing import ContentHash

        never_stored = Anchor(
            document_id=found.document_id,
            span=found.span,
            text_hash=found.text_hash,
            version=ContentHash.of("a revision this store never saw"),
        )
        traced = trace_anchor(never_stored, store)
        assert traced is not None, "the document is still known, only that revision is not"
        described = traced.describe()
        assert traced.status.value in described
        if traced.status is not ResolutionStatus.RESOLVED:
            # A one-line description that said only "stale" would leave a
            # reader to guess between "moved" and "gone".
            assert "--" in described, "the detail, not just the verdict"
