"""What an index actually costs, on a real corpus.

ADR-0007 chose character bigrams and said the index would be several times
larger than a naive one, and that the multiplier would be measured on real data
rather than assumed. This is that measurement.

It is a committed tool rather than a one-off script because the number has to be
re-runnable: a claim in a document that nobody can reproduce becomes a stale
claim the moment the code changes.

    python tools/measure_index.py ~/notes
    python tools/measure_index.py ../mamori ../kiseki --queries queries.txt

Reports corpus size, index size, the ratio between them, where the bytes went,
ingest throughput, and search latency. Prints a table; pass --json for a machine
to read.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Run from a checkout without installing. A measurement tool that needs an
# install step is a measurement nobody re-runs.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsumugi.application.ingest import ingest_paths
from tsumugi.application.search import search
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.index.tokenization import BigramTokenizer, is_cjk
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.database import connect
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

#: Mixed on purpose: two-character Japanese compounds are the case ADR-0007
#: exists for, and Latin queries are the case that would have worked anyway.
DEFAULT_QUERIES = (
    "設計",
    "会議",
    "予算",
    "テスト",
    "証拠",
    "検索",
    "ドキュメント",
    "budget",
    "evidence",
    "anchor",
    "context package",
    "privacy boundary",
)


@dataclass
class Measurement:
    corpus_roots: list[str] = field(default_factory=list)
    documents: int = 0
    characters: int = 0
    cjk_share: float = 0.0
    corpus_bytes: int = 0
    index_bytes: int = 0
    documents_table_bytes: int = 0
    search_table_bytes: int = 0
    ratio_index_to_corpus: float = 0.0
    ratio_search_to_corpus: float = 0.0
    terms_per_character: float = 0.0
    ingest_seconds: float = 0.0
    documents_per_second: float = 0.0
    #: Re-running ingest over an unchanged corpus. This is the number that
    #: decides when incremental ingestion is worth building: every file is
    #: still read, hashed and parsed, and only the store write is skipped.
    reingest_seconds: float = 0.0
    search_p50_ms: float = 0.0
    search_p95_ms: float = 0.0
    searches_returning_nothing: int = 0
    sqlite_version: str = ""


def _table_bytes(connection: sqlite3.Connection, prefix: str) -> int:
    """Approximate bytes held by one logical table.

    ``dbstat`` is a compile-time option and is missing from most builds, so this
    counts pages the honest cheap way: the row payload. It undercounts B-tree
    overhead, which is why the totals below are reported against the file size
    rather than derived from these.
    """
    total = 0
    names = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE ?",
        (f"{prefix}%",),
    ).fetchall()
    for (name,) in names:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info('{name}')")]
        if not columns:
            continue
        lengths = " + ".join(f'COALESCE(LENGTH(CAST("{c}" AS BLOB)), 0)' for c in columns)
        try:
            # S608: every name here comes from sqlite_master and PRAGMA
            # table_info on a database this process just created. There is no
            # caller input anywhere in this string.
            query = f'SELECT COALESCE(SUM({lengths}), 0) FROM "{name}"'  # noqa: S608
            total += int(connection.execute(query).fetchone()[0])
        except sqlite3.OperationalError:
            continue
    return total


def measure(roots: list[Path], queries: list[str]) -> Measurement:
    result = Measurement(
        corpus_roots=[str(r) for r in roots], sqlite_version=sqlite3.sqlite_version
    )

    with tempfile.TemporaryDirectory() as workspace:
        index_path = Path(workspace) / "measure.db"
        connection = connect(index_path)
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)

        started = time.perf_counter()
        for root in roots:
            found = walk(root)
            result.corpus_bytes += sum(p.stat().st_size for p in found.files)
            ingest_paths(found.files, root=root, store=store, index=index, parser_for=parser_for)
        result.ingest_seconds = time.perf_counter() - started

        # The same walk again, over an unchanged corpus.
        started = time.perf_counter()
        for root in roots:
            found = walk(root)
            ingest_paths(found.files, root=root, store=store, index=index, parser_for=parser_for)
        result.reingest_seconds = time.perf_counter() - started

        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        tokenizer = BigramTokenizer()
        terms = 0
        for document in store.all_current():
            result.documents += 1
            result.characters += len(document.content)
            result.cjk_share += sum(1 for c in document.content if is_cjk(c))
            terms += len(tokenizer.index_terms(document.content))

        result.documents_table_bytes = _table_bytes(connection, "documents")
        result.search_table_bytes = _table_bytes(connection, "search")

        latencies: list[float] = []
        for query in queries:
            begin = time.perf_counter()
            hits, _ = search(query, store=store, index=index, limit=10)
            latencies.append((time.perf_counter() - begin) * 1000)
            if not hits:
                result.searches_returning_nothing += 1

        connection.close()
        result.index_bytes = index_path.stat().st_size

    if result.characters:
        result.cjk_share /= result.characters
        result.terms_per_character = terms / result.characters
    if result.corpus_bytes:
        result.ratio_index_to_corpus = result.index_bytes / result.corpus_bytes
        result.ratio_search_to_corpus = result.search_table_bytes / result.corpus_bytes
    if result.ingest_seconds:
        result.documents_per_second = result.documents / result.ingest_seconds
    if latencies:
        latencies.sort()
        result.search_p50_ms = statistics.median(latencies)
        result.search_p95_ms = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
    return result


def _mib(value: int) -> str:
    return f"{value / 1024 / 1024:.2f} MiB"


def report(result: Measurement) -> None:
    print("corpus")
    for root in result.corpus_roots:
        print(f"  {root}")
    print(f"  {result.documents} documents, {result.characters:,} characters")
    print(f"  {_mib(result.corpus_bytes)} on disk, {result.cjk_share:.0%} CJK characters")
    print()
    print("index")
    print(f"  {_mib(result.index_bytes)} total  ({result.ratio_index_to_corpus:.2f}x the corpus)")
    print(f"  {_mib(result.documents_table_bytes)} stored text (ADR-0010)")
    print(
        f"  {_mib(result.search_table_bytes)} bigram terms ({result.ratio_search_to_corpus:.2f}x)"
    )
    print(f"  {result.terms_per_character:.2f} terms per character")
    print()
    print("speed")
    print(
        f"  ingest    {result.ingest_seconds:.2f}s "
        f"({result.documents_per_second:.0f} documents/second)"
    )
    print(f"  re-ingest {result.reingest_seconds:.2f}s (unchanged corpus)")
    print(f"  search    p50 {result.search_p50_ms:.1f} ms, p95 {result.search_p95_ms:.1f} ms")
    print(f"  {result.searches_returning_nothing} of the queries found nothing")
    print()
    print(f"sqlite {result.sqlite_version}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--queries", type=Path, help="one query per line")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    missing = [r for r in args.roots if not r.is_dir()]
    if missing:
        print(f"not a directory: {missing[0]}", file=sys.stderr)
        return 2

    queries = list(DEFAULT_QUERIES)
    if args.queries:
        queries = [q for q in args.queries.read_text(encoding="utf-8").splitlines() if q.strip()]

    result = measure([r.resolve() for r in args.roots], queries)
    if args.json:
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
