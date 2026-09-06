"""Which indexes exist, by name, with no path anywhere in the answer.

`sora` is building profiles: one machine, several people, and switching swaps
the corpus. Its screen wants to say *"this person's record: personal (1,234
documents)"*. Until now the only way to find out whether a name worked was to
use it and read the error.

**The path is the thing being withheld**, and the first version of this leaked
it: an index that would not open reported the exception's message, and
`StorageError: no index at /home/ada/.tsumugi/personal.db` names a file. The
kind is what a caller can act on; where the file lives is the operator's.

This file also answers the three questions sora asked about profile isolation,
by testing rather than by asserting in prose:

1. does configuring ten indexes open ten files, or only the one asked for;
2. is deleting the file enough to delete a profile, or does something survive;
3. does `ledger: false` really write nothing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests.helpers import build_document
from tsumugi.application.indexes import CONTRACT, summarise_indexes
from tsumugi.config import TsumugiConfig
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.storage.database import connect
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore
from tsumugi.interfaces.cli.main import main


def _built(path: Path, documents: int) -> Path:
    connection = connect(path)
    try:
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        for number in range(documents):
            document = build_document(f"n{number}.md", f"# N{number}\n\nsome text {number}\n")
            store.put(document, corpus_root=str(path.parent))
            index.add(document)
    finally:
        connection.close()
    return path


class TestTheListing:
    def test_it_names_each_index_and_counts_what_is_in_it(self, tmp_path: Path) -> None:
        _built(tmp_path / "personal.db", 3)
        _built(tmp_path / "news.db", 7)
        config = TsumugiConfig.from_mapping(
            {"indexes": {"personal": tmp_path / "personal.db", "news": tmp_path / "news.db"}}
        )

        summaries = summarise_indexes(
            [name for name, _ in config.indexes],
            lambda name: connect(config.resolved_index_path(name), create=False),
        )
        assert [(s.name, s.documents) for s in summaries] == [("personal", 3), ("news", 7)]
        assert all(s.ingested_at for s in summaries), "an ingested index has a date"

    def test_no_named_indexes_is_an_empty_list_rather_than_an_error(self) -> None:
        assert summarise_indexes([], lambda _name: connect(":memory:")) == []

    def test_an_index_that_will_not_open_is_a_row_saying_so(self, tmp_path: Path) -> None:
        """A deleted profile must not take the working ones down with it."""
        _built(tmp_path / "here.db", 2)

        def opener(name: str) -> sqlite3.Connection:
            return connect(tmp_path / f"{name}.db", create=False)

        summaries = summarise_indexes(["here", "gone"], opener)
        assert [(s.name, s.documents) for s in summaries] == [("here", 2), ("gone", None)]
        assert summaries[1].unavailable == "StorageError"

    def test_no_path_appears_anywhere_in_the_answer(self, tmp_path: Path) -> None:
        """The constraint the whole design exists for, checked as a string.

        Not "the field looks tidy" -- the rendered JSON is searched for the
        directory the indexes actually live in.
        """
        _built(tmp_path / "here.db", 1)

        def opener(name: str) -> sqlite3.Connection:
            return connect(tmp_path / f"{name}.db", create=False)

        rendered = json.dumps([s.as_dict() for s in summarise_indexes(["here", "gone"], opener)])
        assert str(tmp_path) not in rendered, rendered
        assert "here.db" not in rendered and "gone.db" not in rendered, rendered
        assert "StorageError" in rendered, "the kind must survive, or a caller cannot act"


class TestTheCommandLine:
    def test_it_emits_the_named_contract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import os

        _built(tmp_path / "personal.db", 4)
        monkeypatch.setenv("TSUMUGI_INDEXES", f"personal={tmp_path / 'personal.db'}")
        monkeypatch.delenv("TSUMUGI_INDEX", raising=False)
        assert os.environ["TSUMUGI_INDEXES"]

        assert main(["indexes", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["contract"] == CONTRACT
        assert payload["indexes"] == [
            {
                "name": "personal",
                "documents": 4,
                "ingested_at": payload["indexes"][0]["ingested_at"],
            }
        ]

    def test_none_configured_says_how_to_configure_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("TSUMUGI_INDEXES", raising=False)
        assert main(["indexes"]) == 0
        printed = capsys.readouterr().out
        assert "no named indexes" in printed
        assert "TSUMUGI_INDEXES" in printed


class TestProfileIsolation:
    """The three questions sora asked, answered by testing rather than by prose."""

    def test_configuring_many_opens_only_the_one_asked_for(self, tmp_path: Path) -> None:
        """Ten names configured, one index used, one file opened."""
        for number in range(10):
            _built(tmp_path / f"p{number}.db", 1)
        config = TsumugiConfig.from_mapping(
            {"indexes": {f"p{number}": tmp_path / f"p{number}.db" for number in range(10)}}
        )

        from tsumugi.interfaces.mcp.server import McpServer

        server = McpServer(config)
        try:
            server._open("p3")
            assert list(server._connections) == ["p3"], server._connections
        finally:
            server.close()

    def test_deleting_the_file_deletes_the_profile(self, tmp_path: Path) -> None:
        """Store, index and ledger are one file, so one file is the whole of it.

        Checked by counting what a fresh index at the same path holds: if
        anything survived elsewhere -- a cache, a sidecar -- it would show up
        here as a document that is still known.
        """
        path = _built(tmp_path / "profile.db", 5)
        connection = connect(path)
        try:
            from tsumugi.infrastructure.storage.ledger import SqliteLedger

            assert SqliteLedger(connection).entries() == []
            assert len(list(SqliteDocumentStore(connection).all_current())) == 5
        finally:
            connection.close()

        for leftover in path.parent.glob("profile.db*"):
            leftover.unlink()

        reopened = connect(path)
        try:
            assert list(SqliteDocumentStore(reopened).all_current()) == []
            assert FtsIndex(reopened).count() == 0
            from tsumugi.infrastructure.storage.ledger import SqliteLedger

            assert SqliteLedger(reopened).entries() == []
        finally:
            reopened.close()

    def test_an_index_that_was_never_written_to_has_no_ledger_rows(self, tmp_path: Path) -> None:
        """`ledger: false` writes nothing, so another profile's ledger cannot
        grow while this one is open. The MCP half is in `test_mcp.py`; this is
        the storage-level fact it rests on."""
        from tsumugi.infrastructure.storage.ledger import SqliteLedger

        path = _built(tmp_path / "quiet.db", 2)
        connection = connect(path)
        try:
            assert SqliteLedger(connection).entries() == []
        finally:
            connection.close()


class TestTheIndexSaysHowItWasBuilt:
    def test_an_index_built_by_an_older_rule_is_refused(self, tmp_path: Path) -> None:
        """Front matter stopped being indexed, and an old index still holds it.

        The tokenizer marker could not carry this: the terms for a given span
        are unchanged, and what changed is which spans exist. An index built
        before the change would answer questions the current code would never
        ask it, and nothing about its terms would say so.
        """
        path = _built(tmp_path / "old.db", 1)
        connection = connect(path)
        try:
            with connection:
                connection.execute(
                    "UPDATE index_meta SET value = 'bigram/script-aware@2' WHERE key = 'tokenizer'"
                )
        finally:
            connection.close()

        reopened = connect(path)
        try:
            with pytest.raises(ValueError) as raised:
                FtsIndex(reopened)
            assert "--rebuild" in str(raised.value)
            assert "rule" in str(raised.value)
        finally:
            reopened.close()

    def test_a_current_index_opens_without_complaint(self, tmp_path: Path) -> None:
        """The positive control: the refusal above must be about the marker,
        not about opening an index at all."""
        path = _built(tmp_path / "current.db", 1)
        connection = connect(path)
        try:
            assert FtsIndex(connection).count() == 1
        finally:
            connection.close()
