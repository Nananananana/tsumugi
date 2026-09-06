"""The search index: bigram terms in an FTS5 table.

The tokenizer (ADR-0007) turns text into terms before SQLite sees it, and the
FTS5 table is configured with ``unicode61``, whose only job here is to split on
the spaces the tokenizer already put in. FTS5's own tokenizers are not used,
because that is exactly what does not work for Japanese.

Queries are ``OR`` over the query's terms, ranked by bm25. That is loose on
purpose: this stage generates candidates, and confirmation against the anchored
text is what turns a candidate into a result.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Final

from ...domain.document import Document, DocumentId
from ...domain.hashing import ContentHash
from ...domain.span import Span
from ...ports.index import IndexHit
from .tokenization import BigramTokenizer

__all__ = ["FtsIndex"]

#: FTS5 refuses a query built only of punctuation, and a term that tokenizes to
#: nothing would produce an empty phrase and a syntax error. A term has to
#: carry at least one character that unicode61 will keep.
_MEANINGFUL = str.isalnum


def _usable(term: str) -> bool:
    return any(_MEANINGFUL(character) for character in term)


#: Which rule decided what a searchable unit is. Bumped when that rule
#: changes, so an index built by an older one is refused rather than half
#: believed.
#:
#: The tokenizer marker below could not carry this. The tokenizer says how a
#: span becomes terms and is unchanged; what changed at 2 is **which spans
#: exist** -- front matter stopped being indexed. An index built at rule 1
#: still holds `source_url:` lines as citable text, and nothing about its terms
#: would ever say so. Silently searching it would be the "slightly false"
#: this library exists to avoid, so it is a loud refusal and a rebuild.
#:
#: 1. every section's own text, front matter included
#: 2. front matter subtracted (2026-09-07)
INDEXING_RULE: Final = 2


def _quote(term: str) -> str:
    """A term as an FTS5 phrase. Everything is quoted, so nothing is syntax."""
    return '"' + term.replace('"', '""') + '"'


def _after_front_matter(start: int, end: int, withheld: list[Span]) -> int:
    """``start``, moved past any front matter it begins inside.

    Front matter sits at the head of a document, so a span either starts inside
    one or is clear of it; there is no case where a block has to be cut out of
    the middle. Returning ``end`` means the span was entirely front matter and
    the caller drops it.
    """
    for block in withheld:
        if block.start <= start < block.end:
            start = min(block.end, end)
    return start


def _own_spans(document: Document) -> list[Span]:
    """Each section's own text, tiling the document without overlaps.

    `Document.sections` **nests**: a level-1 heading spans everything under it,
    including its level-2 children. Indexing them all indexes the same
    paragraph once per ancestor, and the first thing that goes wrong is not a
    scoring artefact -- it is `ContextPackage` refusing to be built, because the
    same span arrives as both an item and an omission. That invariant caught
    this the first time it ran.

    So a section contributes the text between its own start and its first
    child: what a reader means by "the part under this heading", as opposed to
    "this heading and everything beneath it". The pieces tile, so every
    character is indexed exactly once.

    **Front matter is subtracted**, and that is not a tidying-up. A parser
    already lifts it into `metadata`, and the sections tile the *whole*
    document -- so a file beginning with

        ---
        source_url: https://example.com/a
        ---

    put `source_url: https://example.com/a` into the index as ordinary text,
    and a package handed it back as a **citable item**. Front matter is what a
    document says *about itself*: a provenance URL, a fetch timestamp, an id.
    None of it is evidence for anything a reader asked, and a citation
    resolving to it is a citation to bookkeeping. Reported by `sora`, whose
    corpus arrives from `musubi` with exactly that shape.
    """
    withheld = [block.span for block in document.blocks or () if block.kind == "front_matter"]
    sections = sorted(document.sections or (), key=lambda s: (s.span.start, -s.span.end))
    spans: list[Span] = []
    for index, section in enumerate(sections):
        children = [
            other.span.start
            for other in sections[index + 1 :]
            if section.span.contains(other.span) and other.span != section.span
        ]
        end = min(children) if children else section.span.end
        start = _after_front_matter(section.span.start, end, withheld)
        if end > start:
            spans.append(Span(start, end))
    if spans:
        return spans
    # No sections at all: the whole document, less any front matter at its head.
    whole = _after_front_matter(0, len(document.content), withheld)
    return [Span(whole, len(document.content))] if whole < len(document.content) else []


class FtsIndex:
    """Satisfies :class:`~tsumugi.ports.index.Index`."""

    def __init__(
        self, connection: sqlite3.Connection, tokenizer: BigramTokenizer | None = None
    ) -> None:
        self._connection = connection
        self._tokenizer = tokenizer or BigramTokenizer()
        self._record_identity()

    @property
    def name(self) -> str:
        return f"fts5+{self._tokenizer.name}"

    @property
    def _identity(self) -> str:
        """How this index was built: its tokenizer, and its indexing rule."""
        return f"{self._tokenizer.name}+rule{INDEXING_RULE}"

    def _record_identity(self) -> None:
        """Write down how this index was built.

        An index built by one tokenizer cannot be searched by another: the
        terms would simply not line up, and the failure would look like an
        empty corpus rather than a mismatch. The same is true of the rule that
        decides which spans are indexed at all -- an index that still holds
        front matter answers questions the current code would never ask it.
        """
        row = self._connection.execute(
            "SELECT value FROM index_meta WHERE key = 'tokenizer'"
        ).fetchone()
        if row is None:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO index_meta (key, value) VALUES ('tokenizer', ?)",
                    (self._identity,),
                )
        elif row["value"] != self._identity:
            raise ValueError(
                f"this index was built as {row['value']!r} and is being searched as "
                f"{self._identity!r}. Run `tsumugi ingest --rebuild` to read the "
                f"corpus again."
            )

    def add(self, document: Document) -> None:
        """One row per section, with the section's offsets into the document.

        **The unit that is scored is the unit that can be returned.** Indexing
        whole files means bm25 ranks a 12,000-character document by terms that
        may be anywhere in it, and the passage handed back is a window around
        whichever occurrence was found first. Measured: recall falls from 87.2%
        to 65.6% as documents grow to realistic size, and 68% of that loss is
        the window rather than the ranking.

        `Document` already carries sections, and a document with no headings
        has one implicit section covering all of it -- so there is no
        special case here and no new idea of what a chunk is.

        The offsets are into the **parent**, so an anchor still resolves
        against the file on disk. Most libraries chunk into new documents and
        lose the parent's coordinates; this one cannot (ADR-0010).
        """
        with self._connection:
            self._disown(document.document_id)
            for span in _own_spans(document):
                body = span.slice(document.content)
                terms = " ".join(self._tokenizer.index_terms(body))
                cursor = self._connection.execute(
                    "INSERT INTO search (terms, document_id, version, start, end) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        terms,
                        document.document_id,
                        str(document.version),
                        span.start,
                        span.end,
                    ),
                )
                # Claimed in the same transaction it was written, so the two
                # tables cannot disagree about a row that exists.
                self._connection.execute(
                    "INSERT INTO search_rows (rowid_ref, document_id) VALUES (?, ?)",
                    (cursor.lastrowid, document.document_id),
                )

    def remove(self, document_id: DocumentId) -> None:
        with self._connection:
            self._disown(document_id)

    def _disown(self, document_id: DocumentId) -> None:
        """Drop every FTS row a document owns, by rowid.

        **Never `DELETE FROM search WHERE document_id = ?`.** That column is
        UNINDEXED and FTS5 cannot seek on it, so the statement scans the whole
        table -- for every document ingested, whether or not there is anything
        to delete. Ingest was quadratic because of that one line: 3.2 ms per
        document at 14,000 rows, 12.9 ms at 70,000, ten minutes for ten
        thousand documents. `search_rows` is an ordinary table with an index,
        so this is a seek and a handful of rowid deletes.

        Must run inside the caller's transaction, so a failure between the
        two statements leaves nothing half-owned.
        """
        self._connection.execute(
            "DELETE FROM search WHERE rowid IN "
            "(SELECT rowid_ref FROM search_rows WHERE document_id = ?)",
            (document_id,),
        )
        self._connection.execute("DELETE FROM search_rows WHERE document_id = ?", (document_id,))

    def search(self, query: str, limit: int = 50) -> Sequence[IndexHit]:
        terms = [t for t in self._tokenizer.query_terms(query) if _usable(t)]
        if not terms:
            return []

        expression = " OR ".join(_quote(term) for term in dict.fromkeys(terms))
        rows = self._connection.execute(
            "SELECT document_id, version, start, end, bm25(search) AS rank FROM search "
            "WHERE search MATCH ? ORDER BY rank, document_id, start LIMIT ?",
            (expression, limit),
        ).fetchall()

        # bm25 returns a negative number, more negative being better. Flipping
        # it makes "higher is better" true everywhere above this line, which is
        # what every caller assumes.
        return [
            IndexHit(
                score=-float(row["rank"]),
                document_id=row["document_id"],
                version=ContentHash.parse(row["version"]),
                span=Span(int(row["start"]), int(row["end"])),
            )
            for row in rows
        ]

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS n FROM search").fetchone()
        return int(row["n"])
