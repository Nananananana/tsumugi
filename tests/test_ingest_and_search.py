"""Reading a folder, finding things in it, and going backwards.

The theme: nothing is skipped silently. A run that says "indexed 412" and
nothing else cannot be told apart from one that quietly excluded half the
folder (ADR-0005).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tsumugi.application.ingest import ingest_paths
from tsumugi.application.search import RELATIVE_MATCH_FLOOR, _content_terms, search
from tsumugi.application.trace import trace_anchor, trace_quotation
from tsumugi.domain.anchor import Anchor, ResolutionStatus
from tsumugi.domain.span import Span
from tsumugi.infrastructure.filesystem import IgnoreRules, walk
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.database import connect
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


class TestNeedles:
    """The phrase list a query becomes, at the boundary nothing was testing."""

    def test_two_words_still_make_a_phrase(self) -> None:
        """`len(words) <= 1` is the single-word shortcut, and one is the edge.

        `tools/mutate.py` moved it to `<= 2` and nothing objected: a two-word
        query silently became its first word alone, so `warranty coverage`
        would have retrieved on `warranty` and confirmed on `warranty`. Two
        words is the commonest shape of a real question after one.
        """
        from tsumugi.application.search import _needles

        assert _needles("warranty coverage") == ["warranty coverage"]
        assert _needles("warranty") == ["warranty"]
        assert _needles("") == []


class TestTheRelativeFloor:
    """ADR-0019, pinned by a test rather than only by the evaluation floors.

    `tools/mutate.py` found this: mutating the relative-floor expression --
    `r.unconfirmed or r.matched >= floor or not r.matched` -- survived every
    unit test. The logic is real and was measured (trap rate 25.8% to 3.3%),
    but the only thing holding it was `eval --tier ci`, a statistical gate.

    **A statistical gate tells you a number moved. It does not tell you which
    rule broke**, and it took four attempts to get this rule right. These tests
    say what it means, so a regression arrives named.
    """

    def test_a_weak_match_beside_a_strong_one_is_not_evidence(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        """The near-miss case, in miniature, in a query the rule can act on.

        **The first draft of this test could not reach the rule**, and finding
        out why is the useful part. `_needles` splits on whitespace, so a
        Japanese query with no spaces yields exactly one needle and `matched`
        is all-or-nothing -- 0 or the whole length, never between. The floor
        compares against 80% of the best, so with two possible values it can
        never decide anything. **The relative floor is a multi-word rule**, and
        nothing said so before this test was written.

        Here the answer matches all 28 characters of the phrase and the
        neighbour matches `coverage period`, 15 -- below 80% of 28, so it comes
        back marked rather than ranked.
        """
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "answer.md").write_text(
            "# Terms" + chr(10) * 2 + "The warranty coverage period is 24 months." + chr(10),
            encoding="utf-8",
        )
        (corpus / "neighbour.md").write_text(
            "# Returns" + chr(10) * 2 + "The coverage period for returns is 30 days." + chr(10),
            encoding="utf-8",
        )
        _ingest(corpus, store, index)

        results, _ = search("the warranty coverage period", store=store, index=index)
        by_path = {r.source_path: r for r in results}
        assert "corpus/answer.md" in by_path or "answer.md" in str(by_path), by_path
        answer = next(r for p, r in by_path.items() if "answer" in p)
        neighbour = next(r for p, r in by_path.items() if "neighbour" in p)

        assert not answer.unconfirmed, "the document that answers the question was dropped"
        assert neighbour.matched, "the fixture no longer reaches the floor at all"
        assert neighbour.matched < answer.matched * RELATIVE_MATCH_FLOOR
        assert neighbour.unconfirmed, "a much weaker match was returned as evidence"

    def test_the_only_match_there_is_still_counts(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        """Relative to the best found, not to an absolute bar.

        With one document there is no stronger match to be weak against, so a
        partial match is the best evidence the corpus has and is returned as
        such. An absolute threshold would drop it, which is the mistake
        ADR-0019 exists to avoid.
        """
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "only.md").write_text(
            "# 装備" + chr(10) * 2 + "テントの重量は 2.4kg です。" + chr(10), encoding="utf-8"
        )
        _ingest(corpus, store, index)

        results, _ = search("テントの重量は", store=store, index=index)
        assert results, "nothing came back at all"
        assert not results[0].unconfirmed, "the only evidence there was marked unconfirmed"


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


class TestAQuestionAskedInOtherWords:
    """Confirmation is a phrase match, and a paraphrase shares no phrase.

    Found by asking the demo corpus a question three ways. Only the wording
    the document itself uses returned anything -- in the library's primary
    language. The evaluation corpus could not see it, because every question
    in it was generated from the subject and attribute the document uses.
    """

    def test_content_terms_drop_the_grammar(self) -> None:
        # の and は are what change when the same question is asked
        # differently, so they are exactly what must not be matched on.
        assert _content_terms("テントの重量は") == ["テント", "重量"]
        assert _content_terms("テントはどれくらい重い") == ["テント", "重"]

    def test_it_is_script_runs_and_not_morphology(self) -> None:
        # No dictionary, no word list. A run of one script is structure the
        # string already has; a word list would need one per language.
        assert _content_terms("有給休暇の付与日数は") == ["有給休暇", "付与日数"]
        assert _content_terms("the retry policy") == ["the", "retry", "policy"]
        assert _content_terms("3日で50%") == ["3", "日", "50"]

    def test_punctuation_and_spaces_are_not_terms(self) -> None:
        assert _content_terms("テントの重量は?") == ["テント", "重量"]
        assert _content_terms("???") == []

    @pytest.mark.parametrize(
        "query",
        ["テントの重さは?", "テントはどれくらい重い?", "テントの重量について"],
    )
    def test_a_paraphrase_finds_the_answer(self, tmp_path: Path, query: str) -> None:
        root = tmp_path / "notes"
        root.mkdir()
        with (root / "gear.md").open("w", encoding="utf-8", newline="") as handle:
            handle.write("# 装備\n\nテントの重量は2.4kg、二人用。\n")
        connection = connect(tmp_path / "index.db")
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        ingest_paths(
            sorted(root.rglob("*.md")),
            root=root,
            store=store,
            index=index,
            parser_for=parser_for,
        )
        results, _ = search(query, store=store, index=index, limit=5)
        assert results and not results[0].unconfirmed
        connection.close()

    def test_a_question_using_words_the_document_lacks_still_finds_nothing(
        self, tmp_path: Path
    ) -> None:
        # The residual, and it is the honest one. "何キロ" is not in a document
        # that says "2.4kg". Reaching it means accepting half-coverage, which
        # the corpus measured at a 28.6% trap rate -- five times the ceiling.
        root = tmp_path / "notes"
        root.mkdir()
        with (root / "gear.md").open("w", encoding="utf-8", newline="") as handle:
            handle.write("# 装備\n\nテントの重量は2.4kg、二人用。\n")
        connection = connect(tmp_path / "index.db")
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        ingest_paths(
            sorted(root.rglob("*.md")),
            root=root,
            store=store,
            index=index,
            parser_for=parser_for,
        )
        results, _ = search("テントは何キロ?", store=store, index=index, limit=5)
        assert all(r.unconfirmed for r in results)
        connection.close()

    def test_coverage_is_a_fallback_and_never_a_replacement(self) -> None:
        # It runs only where the phrase rule found nothing, which today means
        # the candidate is rejected outright. So it can turn a rejection into a
        # result and never the other way round -- which is what keeps
        # ADR-0007's guarantee intact.
        import inspect

        from tsumugi.application import search as module

        source = inspect.getsource(module.search)
        assert "spans, matched = _confirm(document.content, needles)" in source
        # Coverage is reached only after the phrase rule came back empty.
        assert source.index("_confirm(document.content, needles)") < source.index(
            "_confirm_by_coverage(document.content, terms)"
        )

    def test_every_content_term_has_to_be_there(self) -> None:
        # At 1.0 the rule reads plainly. Chosen rather than measured: the
        # corpus cannot separate 0.8 from 1.0, and where evidence is absent
        # this library fails closed.
        from tsumugi.application.search import COVERAGE_THRESHOLD

        assert COVERAGE_THRESHOLD == 1.0


class TestWhatADocumentHashIsAHashOf:
    """The statement a producer at the other end of a seam needs.

    `document_hash` travels in every anchor of the published contract, and a
    sync tool handing tsumugi a corpus has to be able to compute the same
    number. Nothing said what it was a hash of until a seam test went looking,
    found the two agreed, and could not say whether that was design or luck.

    It is design, and narrower than it was assumed to be: **sha256 of the
    file's bytes with a UTF-8 BOM removed, and nothing else normalised.**
    """

    def _ingested(self, tmp_path: Path, raw: bytes) -> str:
        root = tmp_path / "notes"
        root.mkdir(parents=True)
        (root / "doc.md").write_bytes(raw)
        connection = connect(tmp_path / "index.db")
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        ingest_paths(
            sorted(root.glob("*.md")),
            root=root,
            store=store,
            index=index,
            parser_for=parser_for,
        )
        version = str(next(iter(store.all_current())).version)
        connection.close()
        return version

    def test_it_is_sha256_of_the_bytes(self, tmp_path: Path) -> None:
        raw = "テントの重量は2.4kg。\n".encode()
        assert self._ingested(tmp_path, raw) == f"sha256:{hashlib.sha256(raw).hexdigest()}"

    def test_line_endings_are_not_normalised(self, tmp_path: Path) -> None:
        # The one a producer is most likely to assume the other way. tsumugi
        # reads bytes and decodes; it does not do universal-newline
        # translation, so a CRLF file hashes as CRLF and a producer hashing
        # raw bytes agrees without normalising anything.
        raw = "テントの重量は2.4kg。\r\n二人用。\r\n".encode()
        assert self._ingested(tmp_path, raw) == f"sha256:{hashlib.sha256(raw).hexdigest()}"

    def test_a_byte_order_mark_is_removed_first(self, tmp_path: Path) -> None:
        body = "テントの重量は2.4kg。\r\n".encode()
        assert self._ingested(tmp_path, b"\xef\xbb\xbf" + body) == (
            f"sha256:{hashlib.sha256(body).hexdigest()}"
        )

    def test_so_a_bom_does_not_change_the_hash(self, tmp_path: Path) -> None:
        # Usually what you want, and a difference a byte-for-byte comparison
        # on the other side of a seam would report as a mismatch.
        body = "テントの重量は2.4kg。\n".encode()
        assert self._ingested(tmp_path, body) == self._ingested(
            tmp_path / "second", b"\xef\xbb\xbf" + body
        )


class TestTheConfirmedShare:
    """How much of the question was confirmed, not merely that something was.

    `tsumugi eval` reports that 13 unanswerable questions still return context.
    All are English, because an English question splits into sub-phrases and a
    partial phrase can confirm against a document that does not answer it. The
    share separates them -- 0.91 median where an answer exists, 0.44 where it
    does not -- and it is reported rather than acted on, because cutting at 0.5
    would take 51 of 157 correct answers with the 11 wrong ones.
    """

    def test_an_exact_question_confirms_all_of_itself(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "terms.md").write_text(
            "# Terms" + chr(10) * 2 + "The warranty coverage period is 24 months." + chr(10),
            encoding="utf-8",
        )
        _ingest(corpus, store, index)
        results, _ = search("the warranty coverage period", store=store, index=index)
        confirmed = [r for r in results if not r.unconfirmed]
        assert confirmed
        assert confirmed[0].matched == len("the warranty coverage period")

    def test_a_question_the_document_only_half_answers_says_so(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        """The shape of all 13: the phrase is there, the answer is not."""
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "terms.md").write_text(
            "# Terms" + chr(10) * 2 + "The warranty coverage period is 24 months." + chr(10),
            encoding="utf-8",
        )
        _ingest(corpus, store, index)
        question = "the warranty coverage period for accessories"
        results, _ = search(question, store=store, index=index)
        confirmed = [r for r in results if not r.unconfirmed and r.matched]
        assert confirmed, "the fixture no longer reaches a partial confirmation"
        share = confirmed[0].matched / len(question)
        assert share < 1.0, "a question the document does not answer confirmed entirely"
        assert share > 0.3, "the fixture confirms so little that it is not the case being tested"
