"""A query must cost what the answer costs, not what the corpus costs.

`build_context` computed its `corpus_state` -- one hash saying which corpus a
package was built against -- as `corpus_state([d.version for d in
store.all_current()])`. That rehydrated **every document in the store**,
parsing each one's structure and metadata JSON, to read one column. At 10,000
documents it was 68% of a `context` call. `remembered_roots` did the same and
then asked the store for each document's root by id, one query per document.

Both are one narrow query now: 2,452 ms to 593 ms at 10,000 documents.

The tests below count statements rather than time them. What went wrong was
work proportional to the corpus, and a count says that directly where a clock
only says "slow today".

**The corpus these build is unrelated to the question, and that is the whole
design.** A corpus where every document matches makes every document a
candidate, and reading a candidate is work the *answer* costs -- bounded by
`candidate_limit`, not by how much is stored. The first version of this file
filled the corpus with copies of the answer, measured exactly that bound, and
reported it as an N+1.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from tests.helpers import build_document
from tsumugi.application.build_context import build_context
from tsumugi.domain.budget import Budget
from tsumugi.domain.document import Document
from tsumugi.infrastructure.cost.heuristic import CharacterCost
from tsumugi.infrastructure.freshness import remembered_roots
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

QUESTION = "how heavy is the tent"


class Counted:
    """Counts the statements a connection is asked to run."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.statements: list[str] = []
        self._connection = connection

    def __enter__(self) -> Counted:
        self._connection.set_trace_callback(self.statements.append)
        return self

    def __exit__(self, *_: object) -> None:
        self._connection.set_trace_callback(None)

    def selects_from_documents(self) -> int:
        return sum(
            1
            for statement in self.statements
            if "FROM documents" in " ".join(statement.split())
            and statement.lstrip().upper().startswith("SELECT")
        )


def _answerable(store: SqliteDocumentStore, index: FtsIndex) -> None:
    """The one document the question is about."""
    document = build_document("gear.md", "# Gear" + "\n\n" + "The tent weighs 2.4kg." + "\n")
    store.put(document, corpus_root="/corpus")
    index.add(document)


def _unrelated(store: SqliteDocumentStore, index: FtsIndex, count: int, *, offset: int = 0) -> None:
    """``count`` documents that share no term with the question."""
    for number in range(offset, offset + count):
        document = build_document(
            f"other{number:03d}.md",
            f"# Unrelated {number}" + "\n\n" + "Harbour timetables and shipping crates." + "\n",
        )
        store.put(document, corpus_root="/corpus")
        index.add(document)


def _build(store: SqliteDocumentStore, index: FtsIndex) -> None:
    build_context(
        QUESTION,
        store=store,
        index=index,
        cost_model=CharacterCost(),
        budget=Budget.parse("characters:400"),
        freshness=remembered_roots(store),
    )


class TestTheCostDoesNotFollowTheCorpus:
    def test_a_build_reads_the_documents_it_answers_with_and_a_fixed_few_more(
        self, connection: sqlite3.Connection
    ) -> None:
        """One document answers the question; twenty more are in the store."""
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        _answerable(store, index)
        _unrelated(store, index, 20)

        with Counted(connection) as counted:
            _build(store, index)

        # A ceiling that catches "per document", not a pin on the exact number
        # of statements a build runs. Anything near twenty is reading a corpus.
        assert counted.selects_from_documents() < 10, counted.statements

    def test_the_count_does_not_grow_when_the_corpus_does(
        self, connection: sqlite3.Connection
    ) -> None:
        """Same question, same answer, eight times the corpus, same reads.

        This is the property an N+1 breaks and a ceiling can miss.
        """
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        _answerable(store, index)
        _unrelated(store, index, 5)

        def reads() -> int:
            with Counted(connection) as counted:
                _build(store, index)
            return counted.selects_from_documents()

        small = reads()
        _unrelated(store, index, 40, offset=5)
        assert reads() == small, "a build is reading something per document"


class TestTheNarrowQueriesAnswerTheSameThing:
    def test_current_versions_matches_the_documents_held(
        self, connection: sqlite3.Connection
    ) -> None:
        """Cheaper, and the same answer -- checked against the slow path it
        replaced, so an optimisation cannot quietly change what a package says
        about the corpus it was built against."""
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        _answerable(store, index)
        _unrelated(store, index, 6)
        assert sorted(str(v) for v in store.current_versions()) == sorted(
            str(document.version) for document in store.all_current()
        )

    def test_a_revision_replaces_its_version_rather_than_adding_one(
        self, connection: sqlite3.Connection
    ) -> None:
        """`is_current = 1` is doing real work in that query."""
        store = SqliteDocumentStore(connection)
        store.put(build_document("a.md", "first"), corpus_root="/corpus")
        store.put(build_document("a.md", "second"), corpus_root="/corpus")
        held = next(iter(store.all_current()))
        assert store.current_versions() == [held.version], "the surviving version is the new one"

    def test_current_roots_matches_asking_one_at_a_time(
        self, connection: sqlite3.Connection
    ) -> None:
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        _answerable(store, index)
        _unrelated(store, index, 6)
        assert dict(store.current_roots()) == {
            document.document_id: root
            for document in store.all_current()
            if (root := store.corpus_root_of(document.document_id)) is not None
        }

    def test_a_document_with_no_recorded_root_is_absent_rather_than_none(
        self, connection: sqlite3.Connection
    ) -> None:
        """Absent means "cannot check"; a `None` value would have to be
        filtered by every reader, and one of them would forget."""
        store = SqliteDocumentStore(connection)
        store.put(build_document("rootless.md", "text"), corpus_root=None)
        assert store.current_roots() == {}


class TestNothingWalksTheWholeCorpus:
    """The regression a statement count cannot see.

    `all_current()` is **one** statement that returns every row and builds a
    `Document` from each -- parsing two JSON columns per document. Reverting
    `corpus_state` to it left every test above green, because the cost is in
    the rows and the objects, not in the number of statements.

    So this counts the call itself. `build_context` has no business walking a
    corpus to answer one question, and saying that directly is both the
    cheapest check and the clearest one.
    """

    def test_a_build_never_calls_all_current(
        self, connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        _answerable(store, index)
        _unrelated(store, index, 10)

        walked: list[str] = []
        original = SqliteDocumentStore.all_current

        def counted(self: SqliteDocumentStore) -> Iterator[Document]:
            walked.append("all_current")
            return original(self)

        monkeypatch.setattr(SqliteDocumentStore, "all_current", counted)
        _build(store, index)
        assert walked == [], "a build walked the corpus to answer one question"

    def test_the_counter_would_have_noticed(
        self, connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The positive control. Without it, a broken monkeypatch would make
        the test above pass over nothing at all -- which is how the statement
        counters came to be trusted for something they could not see."""
        store = SqliteDocumentStore(connection)
        walked: list[str] = []
        original = SqliteDocumentStore.all_current

        def counted(self: SqliteDocumentStore) -> Iterator[Document]:
            walked.append("all_current")
            return original(self)

        monkeypatch.setattr(SqliteDocumentStore, "all_current", counted)
        list(store.all_current())
        assert walked == ["all_current"]
