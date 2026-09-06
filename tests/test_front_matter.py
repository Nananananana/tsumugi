"""Front matter is what a document says about itself, not what it says.

A file that begins

    ---
    source_url: https://example.com/a
    fetched_at: 2026-09-07
    ---

used to put `source_url: https://example.com/a` into the index as ordinary
text, and a package handed it back as a **citable item**. The parser already
lifts front matter into `metadata`; the sections then tiled the whole document
and the index took the lot.

Reported by `sora`, whose corpus arrives from `musubi` with exactly that
shape, and whose one stated failure mode was "a `source_url:` line appearing
in a quotation".

**The positive control is half of every test here.** "Nothing came back" is
also what a broken index looks like, so each test that asserts front matter is
absent also asserts the body is present.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.helpers import build_document
from tsumugi.application.build_context import build_context
from tsumugi.application.ingest import ingest_paths
from tsumugi.application.search import search
from tsumugi.domain.budget import Budget
from tsumugi.domain.document import Block
from tsumugi.domain.span import Span
from tsumugi.infrastructure.cost.heuristic import CharacterCost
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.index.fts import FtsIndex, _own_spans
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

NL = chr(10)
BODY = "The harbour reopened after the storm on Tuesday."
WITH_FRONT_MATTER = (
    "---"
    + NL
    + "title: Harbour reopens"
    + NL
    + "source_url: https://example.com/a"
    + NL
    + "fetched_at: 2026-09-07"
    + NL
    + "---"
    + NL * 2
    + BODY
    + NL
)
#: The same file with a heading after the front matter, so the preamble section
#: and the heading section are both exercised.
WITH_HEADING = (
    "---"
    + NL
    + "source_url: https://example.com/b"
    + NL
    + "---"
    + NL * 2
    + "# Harbour"
    + NL * 2
    + BODY
    + NL
)


@pytest.fixture
def ingested(
    tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
) -> tuple[SqliteDocumentStore, FtsIndex]:
    root = tmp_path / "feed"
    root.mkdir()
    # `newline=""` so the file holds the bytes written. Without it Windows
    # translates every newline to CRLF, the stored document stops matching the
    # constant above, and an offset assertion fails for a reason that has
    # nothing to do with front matter.
    (root / "a.md").write_text(WITH_FRONT_MATTER, encoding="utf-8", newline="")
    (root / "b.md").write_text(WITH_HEADING, encoding="utf-8", newline="")
    ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)
    return store, index


def _confirmed_texts(question: str, pair: tuple[SqliteDocumentStore, FtsIndex]) -> list[str]:
    store, index = pair
    package = build_context(
        question,
        store=store,
        index=index,
        cost_model=CharacterCost(),
        budget=Budget.parse("characters:800"),
    )
    return [item.text for item in package.items]


class TestFrontMatterIsNotEvidence:
    @pytest.mark.parametrize("question", ["source_url example.com", "fetched_at 2026-09-07"])
    def test_a_front_matter_line_is_never_a_citable_item(
        self, question: str, ingested: tuple[SqliteDocumentStore, FtsIndex]
    ) -> None:
        assert _confirmed_texts(question, ingested) == []

    def test_the_body_is_still_found(self, ingested: tuple[SqliteDocumentStore, FtsIndex]) -> None:
        """The positive control. Without it, "nothing came back" above would
        also pass on an index that holds nothing at all."""
        found = _confirmed_texts("harbour reopened storm", ingested)
        assert found, "the body must be retrievable, or the assertions above prove nothing"
        assert any(BODY in text for text in found)

    def test_no_returned_passage_contains_a_front_matter_line(
        self, ingested: tuple[SqliteDocumentStore, FtsIndex]
    ) -> None:
        """Search is exploratory and shows unconfirmed hits, so it is the
        wider net: nothing it returns may carry the URL either."""
        store, index = ingested
        results, _ = search("harbour reopened storm", store=store, index=index)
        assert results, "the corpus must answer, or this checks nothing"
        for result in results:
            assert "source_url" not in result.text, result.text
            assert "fetched_at" not in result.text, result.text

    def test_an_anchor_starts_after_the_front_matter(
        self, ingested: tuple[SqliteDocumentStore, FtsIndex]
    ) -> None:
        """The offsets move too, not just the terms.

        An anchor pointing into the front matter would resolve to it, and
        `trace` would send a reader there.
        """
        store, index = ingested
        results, _ = search("harbour reopened storm", store=store, index=index)
        assert results, "the corpus must answer, or this checks nothing"
        for result in results:
            document = store.get(result.anchor.document_id)
            assert document is not None
            closing = document.content.index("---", 3) + len("---")
            assert result.anchor.span.start >= closing, (
                f"{result.source_path} anchored at {result.anchor.span.start}, "
                f"inside front matter ending at {closing}"
            )


class TestWhatIsStillTrue:
    def test_front_matter_still_becomes_metadata(
        self, ingested: tuple[SqliteDocumentStore, FtsIndex]
    ) -> None:
        """Excluded from the *body*, not discarded. `layer` and `observed_at`
        are read from here and decide what a passage is."""
        store, _index = ingested
        document = store.by_path("a.md")
        assert document is not None
        assert document.metadata["source_url"] == "https://example.com/a"
        assert document.metadata["title"] == "Harbour reopens"

    def test_the_document_still_holds_its_whole_text(
        self, ingested: tuple[SqliteDocumentStore, FtsIndex]
    ) -> None:
        """The store keeps the file as it was read. Only the *index* narrows,
        so an anchor into a document still resolves against the real bytes."""
        store, _index = ingested
        document = store.by_path("a.md")
        assert document is not None
        assert document.content == WITH_FRONT_MATTER

    def test_a_document_with_no_front_matter_is_untouched(
        self, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        plain = build_document("plain.md", "# Title" + NL * 2 + BODY + NL)
        store.put(plain, corpus_root="/corpus")
        index.add(plain)
        results, _ = search("harbour reopened storm", store=store, index=index)
        assert any(BODY in result.text for result in results)


class TestTheIndexSaysItChanged:
    def test_the_rule_number_is_pinned(self, connection: sqlite3.Connection) -> None:
        """Deliberately a literal.

        The first version of this read `INDEXING_RULE` back out of the module,
        so the assertion moved with the constant and lowering it changed
        nothing. Bumping the rule is meant to cost an edit here -- that is the
        moment somebody decides existing indexes must be rebuilt.
        """
        index = FtsIndex(connection)
        row = connection.execute("SELECT value FROM index_meta WHERE key = 'tokenizer'").fetchone()
        assert row["value"].endswith("+rule2"), row["value"]
        assert index._identity.endswith("+rule2")


class TestTheSpanRuleDirectly:
    """`_own_spans`, on shapes the markdown parser does not currently produce.

    The markdown parser always emits at least one section, so the no-sections
    branch never runs for the only parser that produces front matter today --
    deleting the trim there left every test above green. A future parser that
    emits blocks without sections would hit it, so it is exercised here rather
    than left as defensive code nobody has run.
    """

    def test_a_document_with_front_matter_and_no_sections_is_trimmed(self) -> None:
        content = "---" + NL + "source_url: https://example.com/c" + NL + "---" + NL * 2 + BODY
        fence = content.index("---", 3) + len("---")
        document = build_document(
            "no-sections.md",
            content,
            sections=(),
            blocks=(Block(kind="front_matter", span=Span(0, fence)),),
        )
        assert _own_spans(document) == [Span(fence, len(content))]

    def test_a_document_that_is_nothing_but_front_matter_is_not_indexed(self) -> None:
        """Indexing it would put a bare metadata block in the corpus as text."""
        content = "---" + NL + "source_url: https://example.com/d" + NL + "---" + NL
        document = build_document(
            "only-front-matter.md",
            content,
            sections=(),
            blocks=(Block(kind="front_matter", span=Span(0, len(content))),),
        )
        assert _own_spans(document) == []

    def test_a_document_with_no_front_matter_keeps_its_whole_span(self) -> None:
        """The positive control: the trim must not fire when there is nothing
        to trim, or an ordinary document would lose its first characters."""
        document = build_document("plain.md", BODY, sections=(), blocks=())
        assert _own_spans(document) == [Span(0, len(BODY))]
