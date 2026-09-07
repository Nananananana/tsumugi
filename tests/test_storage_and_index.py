"""The store, the index, and the version history that ADR-0010 requires."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tsumugi.domain.anchor import Anchor, ResolutionStatus, resolve
from tsumugi.domain.span import Span
from tsumugi.errors import StorageError
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.storage.database import connect, rebuildable_writes
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

from .helpers import build_document, rewind_to_schema

JAPANESE = "# 装備\n\nテントは 2.4kg。東京の会議は明日です。\n"
ENGLISH = "# Budget\n\nThe unit is explicit at the call site.\n"


class TestTheStore:
    def test_a_new_document_is_new(self, store: SqliteDocumentStore) -> None:
        assert store.put(build_document("a.md", JAPANESE)) is True

    def test_the_same_document_twice_is_not(self, store: SqliteDocumentStore) -> None:
        document = build_document("a.md", JAPANESE)
        store.put(document)
        assert store.put(document) is False

    def test_it_round_trips_content_and_structure(self, store: SqliteDocumentStore) -> None:
        from tsumugi.domain.document import Block, Section

        original = build_document(
            "a.md",
            JAPANESE,
            sections=(Section("装備", 1, Span(0, 10), heading_span=Span(0, 4)),),
            blocks=(Block("heading", Span(0, 4), level=1),),
            metadata={"title": "装備"},
        )
        store.put(original)
        loaded = store.get(original.document_id)

        assert loaded is not None
        assert loaded.content == original.content
        assert loaded.sections == original.sections
        assert loaded.blocks == original.blocks
        assert loaded.metadata == {"title": "装備"}

    def test_a_document_whose_hash_lies_is_refused(self, store: SqliteDocumentStore) -> None:
        from tsumugi.domain.document import Document
        from tsumugi.domain.hashing import ContentHash

        lying = Document(
            document_id="doc_x",
            version=ContentHash.of("something else"),
            source_path="a.md",
            media_type="text/plain",
            content=JAPANESE,
        )
        with pytest.raises(ValueError, match="does not match"):
            store.put(lying)

    def test_iteration_is_ordered_so_a_build_can_be_reproducible(
        self, store: SqliteDocumentStore
    ) -> None:
        for path in ("c.md", "a.md", "b.md"):
            store.put(build_document(path, path))
        first = [d.document_id for d in store.all_current()]
        second = [d.document_id for d in store.all_current()]
        assert first == second == sorted(first)


class TestVersions:
    def test_editing_a_document_adds_a_revision(self, store: SqliteDocumentStore) -> None:
        first = build_document("a.md", JAPANESE)
        second = build_document("a.md", JAPANESE + "\n追記。\n")
        store.put(first)
        store.put(second)

        assert store.versions(first.document_id) == [first.version, second.version]
        assert store.current_version(first.document_id) == second.version

    def test_the_old_revision_is_still_readable(self, store: SqliteDocumentStore) -> None:
        first = build_document("a.md", JAPANESE)
        store.put(first)
        store.put(build_document("a.md", "entirely different"))

        held = store.get(first.document_id, first.version)
        assert held is not None
        assert held.content == JAPANESE

    def test_an_old_anchor_still_resolves_against_its_own_revision(
        self, store: SqliteDocumentStore
    ) -> None:
        # The whole of ADR-0010: evidence survives an edit.
        first = build_document("a.md", JAPANESE)
        store.put(first)
        anchor = Anchor.into(first, Span(2, 4))
        store.put(build_document("a.md", "entirely different"))

        held = store.get(anchor.document_id, anchor.version)
        assert held is not None
        assert resolve(anchor, held).status is ResolutionStatus.RESOLVED

    def test_the_same_anchor_is_stale_against_the_current_revision(
        self, store: SqliteDocumentStore
    ) -> None:
        first = build_document("a.md", JAPANESE)
        store.put(first)
        anchor = Anchor.into(first, Span(2, 4))
        store.put(build_document("a.md", "entirely different text here"))

        current = store.get(anchor.document_id)
        assert current is not None
        assert resolve(anchor, current).status is ResolutionStatus.STALE

    def test_count_counts_documents_not_revisions(self, store: SqliteDocumentStore) -> None:
        store.put(build_document("a.md", "one"))
        store.put(build_document("a.md", "two"))
        store.put(build_document("b.md", "three"))
        assert store.count() == 2


class TestForget:
    def test_it_removes_every_revision(self, store: SqliteDocumentStore) -> None:
        document = build_document("a.md", JAPANESE)
        store.put(document)
        store.put(build_document("a.md", JAPANESE + "more"))

        assert store.forget(document.document_id) == 2
        assert store.get(document.document_id) is None

    def test_it_leaves_nothing_recoverable_in_the_file(self, tmp_path: Path) -> None:
        # Deleting rows leaves the text in free pages. For a file holding a
        # person's notes, "removed from the table" is not removed.
        path = tmp_path / "forget.db"
        connection = connect(path)
        store = SqliteDocumentStore(connection)
        secret = "この一文は消えなければならない"
        document = build_document("a.md", f"# note\n\n{secret}\n")
        store.put(document)
        connection.commit()

        store.forget(document.document_id)
        connection.close()

        assert secret.encode("utf-8") not in path.read_bytes()


class TestTheIndex:
    def test_a_two_character_japanese_query_finds_the_document(
        self, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        # The case ADR-0007 exists for. FTS5's default tokenizer scores 0 here
        # and trigram cannot match a two-character query at all.
        document = build_document("a.md", JAPANESE)
        store.put(document)
        index.add(document)

        assert [h.document_id for h in index.search("東京")] == [document.document_id]
        assert [h.document_id for h in index.search("会議")] == [document.document_id]

    def test_english_still_works(self, store: SqliteDocumentStore, index: FtsIndex) -> None:
        document = build_document("b.md", ENGLISH)
        store.put(document)
        index.add(document)
        assert [h.document_id for h in index.search("budget")] == [document.document_id]

    def test_a_query_matching_nothing_returns_nothing(self, index: FtsIndex) -> None:
        assert index.search("存在しない語") == []

    def test_punctuation_only_queries_do_not_crash_fts5(self, index: FtsIndex) -> None:
        # An unquoted or empty phrase is an FTS5 syntax error, and a query is
        # untrusted input from a person typing.
        for query in ('"', "()", "* OR", "  ", "。、"):
            assert index.search(query) == []

    def test_reindexing_replaces_rather_than_duplicates(
        self, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        first = build_document("a.md", JAPANESE)
        index.add(first)
        index.add(build_document("a.md", JAPANESE + "\n追記。\n"))
        assert index.count() == 1

    def test_hits_are_ordered_deterministically(
        self, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        for path in ("a.md", "b.md", "c.md"):
            document = build_document(path, JAPANESE)
            store.put(document)
            index.add(document)
        assert index.search("東京") == index.search("東京")

    def test_an_index_records_how_it_was_built(
        self, connection: sqlite3.Connection, index: FtsIndex
    ) -> None:
        """Its tokenizer *and* its indexing rule.

        Terms from two tokenizers do not line up, and the failure would look
        like an empty corpus rather than a mismatch. The rule is here for the
        same reason and could not ride on the tokenizer's name: when front
        matter stopped being indexed, the terms for a given span did not
        change -- which spans exist did.
        """
        row = connection.execute("SELECT value FROM index_meta WHERE key = 'tokenizer'").fetchone()
        assert row["value"] == index._identity
        assert index._tokenizer.name in row["value"]
        # A literal, not the constant read back: the first version moved with
        # `INDEXING_RULE` and lowering it changed nothing.
        assert row["value"].endswith("+rule2"), row["value"]

    def test_searching_an_index_built_by_another_tokenizer_is_refused(
        self, connection: sqlite3.Connection, index: FtsIndex
    ) -> None:
        class Different:
            name = "mecab@1"

            def index_terms(self, text: str) -> list[str]:  # pragma: no cover - shape
                return [text]

            def query_terms(self, query: str) -> list[str]:  # pragma: no cover - shape
                return [query]

        # `StorageError`, so a caller mapping kinds is told "the index cannot
        # be used" rather than "your call was malformed".
        with pytest.raises(StorageError, match="--rebuild"):
            FtsIndex(connection, Different())  # type: ignore[arg-type]


class TestTheDatabase:
    def test_opening_a_missing_index_for_reading_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(StorageError, match="run `tsumugi ingest`"):
            connect(tmp_path / "nope.db", create=False)

    def test_an_index_from_a_newer_tsumugi_is_refused(self, tmp_path: Path) -> None:
        # Letting an older version write to a newer schema is how an index gets
        # quietly corrupted.
        path = tmp_path / "future.db"
        connection = connect(path)
        connection.execute("PRAGMA user_version = 9999")
        connection.commit()
        connection.close()

        with pytest.raises(StorageError, match="newer version"):
            connect(path)

    def test_a_schema_1_index_migrates_and_keeps_its_documents(self, tmp_path: Path) -> None:
        # Explicit migrations, and the older documents survive them -- they
        # simply have no recorded corpus root, which reads as "cannot check"
        # rather than "unchanged".
        path = tmp_path / "old.db"
        connection = connect(path)
        store = SqliteDocumentStore(connection)
        store.put(build_document("a.md", JAPANESE))
        rewind_to_schema(connection, 1)
        connection.close()

        migrated = SqliteDocumentStore(connect(path))
        held = migrated.by_path("a.md")
        assert held is not None
        assert held.content == JAPANESE
        assert migrated.corpus_root_of(held.document_id) is None

    def test_migrating_twice_is_a_no_op(self, tmp_path: Path) -> None:
        path = tmp_path / "twice.db"
        connect(path).close()
        connect(path).close()


class TestWritesThatARerunWouldRedo:
    """`rebuildable_writes` lowers `synchronous` for the length of a block.

    The reason it is a block and not a setting on the connection: the ledger
    shares this database and is **not** derived from anything. A package that
    was sent cannot be reconstructed by re-reading the corpus, so the writes
    that record one keep waiting for the disk.

    Measured over 300 documents, five interleaved rounds, best of each: 1.73 s
    at `FULL` against 1.21 s at `NORMAL`, 5.78 ms a document against 4.04.
    """

    #: `PRAGMA synchronous` answers with a number.
    FULL = 2
    NORMAL = 1

    def test_the_connection_waits_for_the_disk_by_default(
        self, connection: sqlite3.Connection
    ) -> None:
        """The positive control, and it has to come first: if the default were
        already `NORMAL`, every assertion below would pass while the block did
        nothing."""
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == self.FULL

    def test_inside_the_block_it_does_not(self, connection: sqlite3.Connection) -> None:
        with rebuildable_writes(connection):
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == self.NORMAL

    def test_it_is_put_back_afterwards(self, connection: sqlite3.Connection) -> None:
        with rebuildable_writes(connection):
            pass
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == self.FULL

    def test_it_is_put_back_when_the_block_raises(self, connection: sqlite3.Connection) -> None:
        """An ingest that fails part way through is the ordinary case -- a file
        that cannot be parsed, a `Ctrl-C`. Leaving the connection lowered would
        make every later ledger write on it quietly less durable, and nothing
        would say so.
        """
        with pytest.raises(ZeroDivisionError), rebuildable_writes(connection):
            raise ZeroDivisionError

        assert connection.execute("PRAGMA synchronous").fetchone()[0] == self.FULL

    def test_it_restores_what_it_found_rather_than_the_default(
        self, connection: sqlite3.Connection
    ) -> None:
        """A caller who had set their own value gets it back.

        Writing `FULL` unconditionally would work today and would be a trap
        the first time somebody chooses otherwise for their own reasons.
        """
        connection.execute("PRAGMA synchronous = OFF")
        with rebuildable_writes(connection):
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == self.NORMAL
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 0

    def test_what_was_written_inside_the_block_is_there(
        self, connection: sqlite3.Connection
    ) -> None:
        """Durability is not visibility. `NORMAL` changes when a commit reaches
        the platter, never whether the row is in the database."""
        store = SqliteDocumentStore(connection)
        with rebuildable_writes(connection):
            store.put(build_document("notes/a.md", "テントの重量は2.4kg。"))

        assert store.by_path("notes/a.md") is not None
        assert store.count() == 1
