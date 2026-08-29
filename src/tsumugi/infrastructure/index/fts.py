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

from ...domain.document import Document, DocumentId
from ...domain.hashing import ContentHash
from ...ports.index import IndexHit
from .tokenization import BigramTokenizer

__all__ = ["FtsIndex"]

#: FTS5 refuses a query built only of punctuation, and a term that tokenizes to
#: nothing would produce an empty phrase and a syntax error. A term has to
#: carry at least one character that unicode61 will keep.
_MEANINGFUL = str.isalnum


def _usable(term: str) -> bool:
    return any(_MEANINGFUL(character) for character in term)


def _quote(term: str) -> str:
    """A term as an FTS5 phrase. Everything is quoted, so nothing is syntax."""
    return '"' + term.replace('"', '""') + '"'


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

    def _record_identity(self) -> None:
        """Write down which tokenizer built this index.

        An index built by one tokenizer cannot be searched by another: the
        terms would simply not line up, and the failure would look like an
        empty corpus rather than a mismatch.
        """
        row = self._connection.execute(
            "SELECT value FROM index_meta WHERE key = 'tokenizer'"
        ).fetchone()
        if row is None:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO index_meta (key, value) VALUES ('tokenizer', ?)",
                    (self._tokenizer.name,),
                )
        elif row["value"] != self._tokenizer.name:
            raise ValueError(
                f"this index was built by {row['value']!r} and is being searched by "
                f"{self._tokenizer.name!r}. Delete the index and run `tsumugi ingest` again."
            )

    def add(self, document: Document) -> None:
        terms = " ".join(self._tokenizer.index_terms(document.content))
        with self._connection:
            self._connection.execute(
                "DELETE FROM search WHERE document_id = ?", (document.document_id,)
            )
            self._connection.execute(
                "INSERT INTO search (terms, document_id, version) VALUES (?, ?, ?)",
                (terms, document.document_id, str(document.version)),
            )

    def remove(self, document_id: DocumentId) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM search WHERE document_id = ?", (document_id,))

    def search(self, query: str, limit: int = 50) -> Sequence[IndexHit]:
        terms = [t for t in self._tokenizer.query_terms(query) if _usable(t)]
        if not terms:
            return []

        expression = " OR ".join(_quote(term) for term in dict.fromkeys(terms))
        rows = self._connection.execute(
            "SELECT document_id, version, bm25(search) AS rank FROM search "
            "WHERE search MATCH ? ORDER BY rank, document_id LIMIT ?",
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
            )
            for row in rows
        ]

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS n FROM search").fetchone()
        return int(row["n"])
