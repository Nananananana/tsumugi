"""Ingest was quadratic, and the guard against it going quadratic again.

`FtsIndex.add` ran `DELETE FROM search WHERE document_id = ?` before inserting
a document's rows. `document_id` is an FTS5 UNINDEXED column, and FTS5 cannot
seek on one of those: the statement scanned every row in the table, for every
document, whether or not there was anything to delete. Measured directly:
3.2 ms per delete at 14,000 rows, 12.9 ms at 70,000 -- linear in the table,
so quadratic over an ingest. Ten thousand documents took ten minutes.

The fix is `search_rows`: an ordinary table mapping `document_id` to the FTS
rowids it owns, with a real index, so a document's rows are found by seek and
deleted by rowid. Schema 4 creates it and backfills it from the existing
`search` table in one pass, so an existing index is not rebuilt.

**The test that matters is the plan, not the clock.** A timing test is flaky
and a scaling test is slow; `EXPLAIN QUERY PLAN` is neither, and "no SCAN of
the search table" is exactly the property that was lost.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.helpers import build_document, rewind_to_schema, undone_migrations
from tsumugi.domain.document import Section
from tsumugi.domain.span import Span
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.storage.database import SCHEMA_VERSION, connect, empty


def _plan(connection: sqlite3.Connection, sql: str, *params: object) -> str:
    rows = connection.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return " | ".join(str(row[3]) for row in rows)


#: One literal statement per table, so no SQL is ever assembled from a string.
_COUNT = {
    "search": "SELECT COUNT(*) FROM search WHERE document_id = ?",
    "search_rows": "SELECT COUNT(*) FROM search_rows WHERE document_id = ?",
}


def _rows(connection: sqlite3.Connection, table: str, document_id: str) -> int:
    row = connection.execute(_COUNT[table], (document_id,)).fetchone()
    return int(row[0])


class TestNothingScansTheSearchTable:
    def test_finding_a_documents_rows_is_a_seek(self, connection: sqlite3.Connection) -> None:
        plan = _plan(connection, "SELECT rowid_ref FROM search_rows WHERE document_id = ?", "x")
        assert "USING" in plan and "INDEX" in plan, plan
        assert "SCAN" not in plan, plan

    def test_deleting_a_documents_rows_is_by_rowid(self, connection: sqlite3.Connection) -> None:
        """The statement `_disown` actually runs, read the way FTS5 reports it.

        FTS5 always prints a virtual table step as `SCAN search VIRTUAL TABLE
        INDEX 0:<constraints>`; what matters is what follows the colon. The
        old statement planned as `INDEX 0:` -- nothing, a full scan of every
        row -- and this one plans as `INDEX 0:=`, the `=` being FTS5's marker
        for a rowid-equality lookup. Measured: 3.2 ms per document at 14,000
        rows before, flat at ~11 ms per document for the whole ingest after.
        """
        plan = _plan(
            connection,
            "DELETE FROM search WHERE rowid IN "
            "(SELECT rowid_ref FROM search_rows WHERE document_id = ?)",
            "x",
        )
        assert "INDEX 0:=" in plan, plan
        assert "search_rows_by_document" in plan, plan

    def test_the_old_statement_really_was_a_scan(self, connection: sqlite3.Connection) -> None:
        """The positive control: the assertion above must be able to fail.

        Without this, a change in how SQLite prints plans could make the
        `INDEX 0:=` check pass on a scan, and the guard would be green over
        nothing.
        """
        plan = _plan(connection, "DELETE FROM search WHERE document_id = ?", "x")
        assert "INDEX 0:" in plan and "INDEX 0:=" not in plan, plan


class TestTheSideTableStaysTrue:
    def test_re_adding_a_document_replaces_its_rows_in_both_tables(
        self, connection: sqlite3.Connection
    ) -> None:
        index = FtsIndex(connection)
        # Two explicit sections: `build_document` does not run the markdown
        # parser, so headings in the text alone would still be one section.
        text = "alpha alpha\nbeta beta\n"
        first = build_document(
            "a.md",
            text,
            sections=(
                Section(heading="One", level=1, span=Span(0, 12)),
                Section(heading="Two", level=1, span=Span(12, len(text))),
            ),
        )
        index.add(first)
        assert _rows(connection, "search", first.document_id) == 2
        assert _rows(connection, "search_rows", first.document_id) == 2

        revised = build_document("a.md", "gamma\n")
        index.add(revised)
        assert _rows(connection, "search", revised.document_id) == 1
        assert _rows(connection, "search_rows", revised.document_id) == 1
        assert index.count() == 1, "the old rows must be gone from the FTS table itself"

    def test_removing_a_document_clears_both_tables(self, connection: sqlite3.Connection) -> None:
        index = FtsIndex(connection)
        document = build_document("b.md", "# H\n\nsome text\n")
        index.add(document)
        index.remove(document.document_id)
        assert _rows(connection, "search", document.document_id) == 0
        assert _rows(connection, "search_rows", document.document_id) == 0
        assert index.count() == 0

    def test_emptying_the_index_clears_the_side_table_too(
        self, connection: sqlite3.Connection
    ) -> None:
        """`empty()` is the third place that touches `search`, and the one a
        rebuild goes through. A side table left full after it would claim
        rowids that the next ingest reuses -- and `_disown` would then delete
        somebody else's rows."""
        index = FtsIndex(connection)
        index.add(build_document("z.md", "# Z\n\nzeta\n"))
        assert connection.execute("SELECT COUNT(*) FROM search_rows").fetchone()[0] == 1
        empty(connection)
        assert connection.execute("SELECT COUNT(*) FROM search_rows").fetchone()[0] == 0
        assert index.count() == 0

    def test_every_fts_row_is_owned_and_every_owner_row_exists(
        self, connection: sqlite3.Connection
    ) -> None:
        """The two tables describe the same rows, in both directions."""
        index = FtsIndex(connection)
        for name in ("c.md", "d.md", "e.md"):
            index.add(build_document(name, f"# {name}\n\ntext for {name}\n\n## more\n\nagain\n"))
        orphaned = connection.execute(
            "SELECT COUNT(*) FROM search WHERE rowid NOT IN (SELECT rowid_ref FROM search_rows)"
        ).fetchone()[0]
        dangling = connection.execute(
            "SELECT COUNT(*) FROM search_rows WHERE rowid_ref NOT IN (SELECT rowid FROM search)"
        ).fetchone()[0]
        assert orphaned == 0, "an FTS row nothing owns can never be deleted again"
        assert dangling == 0, "an owner row for a missing FTS row is a lie about the index"


class TestEveryMigrationCanBeUndone:
    def test_the_rewind_helper_covers_every_migration(self) -> None:
        """A migration added without its undo breaks every test that builds an
        old database, all at once and for a reason none of them names. This is
        the one place that failure is allowed to surface."""
        from tsumugi.infrastructure.storage.database import _MIGRATIONS

        assert undone_migrations() == set(_MIGRATIONS) - {1}, (
            "tests/helpers.py `_UNDO` needs an entry for every migration above 1"
        )


class TestTheMigrationBackfills:
    def test_a_schema_3_index_gains_the_side_table_without_a_rebuild(self, tmp_path: Path) -> None:
        """Rows that predate the side table are claimed by it, once, on open.

        Built by hand at schema 3: a `search` table with rows and no
        `search_rows`, `user_version` set back. Opening it must leave every
        existing row findable by rowid through the side table -- otherwise a
        user's existing index would be the one that stays quadratic.
        """
        path = tmp_path / "old.db"
        assert SCHEMA_VERSION >= 4

        connection = connect(path)
        rewind_to_schema(connection, 3)
        with connection:
            connection.executemany(
                "INSERT INTO search (terms, document_id, version, start, end) VALUES (?,?,?,?,?)",
                [
                    ("alpha", "doc_old_1", "v", 0, 5),
                    ("beta", "doc_old_1", "v", 5, 9),
                    ("gamma", "doc_old_2", "v", 0, 5),
                ],
            )
        connection.close()

        reopened = connect(path)
        try:
            assert reopened.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert _rows(reopened, "search_rows", "doc_old_1") == 2
            assert _rows(reopened, "search_rows", "doc_old_2") == 1
            # And the claimed rows are the real ones: deleting through the side
            # table removes them from the FTS table.
            FtsIndex(reopened).remove("doc_old_1")
            assert _rows(reopened, "search", "doc_old_1") == 0
            assert _rows(reopened, "search", "doc_old_2") == 1
        finally:
            reopened.close()


class TestTheCodePathItselfNeverScans:
    def test_add_issues_a_rowid_delete_and_never_the_scanning_one(
        self, connection: sqlite3.Connection
    ) -> None:
        """Pins what `add` *runs*, not what a statement written here would do.

        The plan tests above prove a rowid-IN delete is a seek; they would
        stay green if `_disown` quietly went back to `DELETE FROM search WHERE
        document_id = ?`, because they never call it. This records every
        statement SQLite executes during one `add` and reads them.
        """
        statements: list[str] = []
        connection.set_trace_callback(statements.append)
        try:
            FtsIndex(connection).add(build_document("t.md", "# T\n\ntraced text\n"))
        finally:
            connection.set_trace_callback(None)

        deletes = [
            " ".join(s.split())
            for s in statements
            if " ".join(s.split()).upper().startswith("DELETE FROM SEARCH WHERE")
        ]
        assert deletes, "add must delete before it inserts, or a re-add duplicates rows"
        # The scan is the *outer* WHERE on the FTS table. The subquery's
        # `WHERE document_id` on search_rows is the seek, and is fine.
        for statement in deletes:
            assert not statement.startswith("DELETE FROM search WHERE document_id"), statement
        assert any("WHERE rowid IN" in s for s in deletes), deletes
