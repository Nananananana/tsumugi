"""The command line. The only place that wires the layers together.

Every command that touches the index prints where the index is. That is not
chatter: the index is a complete plaintext copy of whatever corpus it was built
from, and a file you do not know about is a file you cannot protect
(``docs/threat-model.md``).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from ... import __version__
from ...application.ingest import ingest_paths
from ...application.search import search as run_search
from ...application.trace import trace_quotation
from ...config import TsumugiConfig
from ...errors import TsumugiError
from ...infrastructure.filesystem import IgnoreRules, walk
from ...infrastructure.index.fts import FtsIndex
from ...infrastructure.parsers import parser_for, registered_suffixes
from ...infrastructure.storage.database import SCHEMA_VERSION, connect
from ...infrastructure.storage.sqlite import SqliteDocumentStore

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tsumugi",
        description=(
            "Local-first context infrastructure. Read a folder, keep the evidence "
            "attached, and trace anything back to where it came from."
        ),
    )
    parser.add_argument("--version", action="version", version=f"tsumugi {__version__}")
    parser.add_argument(
        "--index",
        type=Path,
        metavar="PATH",
        help="where the index lives (default: ~/.tsumugi/index.db, or $TSUMUGI_INDEX)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="read a folder into the index")
    ingest.add_argument("path", type=Path, help="the folder, or a single file")
    ingest.add_argument(
        "--show-skipped", action="store_true", help="list every file that was not read"
    )
    ingest.set_defaults(run=_ingest)

    find = commands.add_parser("search", help="find spans of the corpus")
    find.add_argument("query")
    find.add_argument("-n", "--limit", type=int, default=10)
    find.set_defaults(run=_search)

    trace = commands.add_parser("trace", help="find where a quotation came from")
    trace.add_argument("quotation")
    trace.set_defaults(run=_trace)

    doctor = commands.add_parser("doctor", help="what this index holds, and what it is")
    doctor.set_defaults(run=_doctor)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        # Inside the handler: a bad setting is one of the most likely things to
        # go wrong, and it deserves the same one-line message as everything
        # else rather than a traceback.
        config = TsumugiConfig.from_env()
        if args.index is not None:
            config = replace(config, index_path=args.index)
        return int(args.run(args, config))
    except TsumugiError as error:
        print(f"tsumugi: {error}", file=sys.stderr)
        return 2
    except sqlite3.DatabaseError as error:
        print(f"tsumugi: the index could not be read: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        return 130


# -- commands ------------------------------------------------------------


def _ingest(args: argparse.Namespace, config: TsumugiConfig) -> int:
    index_path = config.resolved_index_path()
    root = args.path.resolve()
    if not root.exists():
        print(f"tsumugi: no such path: {args.path}", file=sys.stderr)
        return 2

    print(f"index:  {index_path}")
    print(f"corpus: {root}")

    connection = connect(index_path)
    store, index = SqliteDocumentStore(connection), FtsIndex(connection)

    if root.is_file():
        files, skipped = [root], []
        root = root.parent
    else:
        rules = IgnoreRules.read_from(root)
        found = walk(root, rules=rules, follow_symlinks=config.follow_symlinks)
        files, skipped = found.files, found.skipped

    report = ingest_paths(files, root=root, store=store, index=index, parser_for=parser_for)
    for entry in skipped:
        report.skipped.append((entry.path.as_posix(), f"{entry.reason} ({entry.rule})"))

    print()
    print(report.summary())

    # Credential-shaped files are named whether or not --show-skipped was
    # passed. The owner did not ask for those to be skipped and would want to
    # know that something looked like a key.
    refused = [(p, r) for p, r in report.skipped if "credential" in r]
    for path, reason in refused:
        print(f"  refused  {path}  ({reason})")

    if args.show_skipped:
        for path, reason in report.skipped:
            if (path, reason) not in refused:
                print(f"  skipped  {path}  ({reason})")
    elif len(report.skipped) > len(refused):
        print(f"  {len(report.skipped) - len(refused)} more skipped; --show-skipped to list them")

    for path, error in report.failed:
        print(f"  failed   {path}  ({error})", file=sys.stderr)

    return 1 if report.failed else 0


def _search(args: argparse.Namespace, config: TsumugiConfig) -> int:
    connection = connect(config.resolved_index_path(), create=False)
    store, index = SqliteDocumentStore(connection), FtsIndex(connection)

    results, truncated = run_search(
        args.query,
        store=store,
        index=index,
        limit=args.limit,
        candidate_limit=config.candidate_limit,
    )

    if not results:
        print("nothing found.")
        return 1

    for result in results:
        where = f"{result.source_path}"
        if result.section:
            where += f" ({result.section})"
        marker = " ~" if result.unconfirmed else "  "
        print(f"{marker}{result.score:6.2f}  {where}")
        print(f"        {_oneline(result.text)}")
        print(f"        offset {result.anchor.span.start}-{result.anchor.span.end}")
        print()

    if any(r.unconfirmed for r in results):
        print("~ = the index proposed it; no exact occurrence of the query was confirmed.")
    if truncated is not None:
        # A cap that bounds coverage is never silent. ADR-0005.
        print(f"note: {truncated.as_omission_reason()}; there may be more.")
    return 0


def _trace(args: argparse.Namespace, config: TsumugiConfig) -> int:
    connection = connect(config.resolved_index_path(), create=False)
    store = SqliteDocumentStore(connection)

    traces = trace_quotation(args.quotation, store)
    if not traces:
        print("unsupported: that text does not appear in this corpus.")
        print("A quotation either resolves or it does not. There is no fuzzy match here.")
        return 1

    for trace in traces:
        print(trace.describe())
    if len(traces) > 1:
        print(f"\n{len(traces)} occurrences. Ambiguity is reported, not resolved.")
    return 0


def _doctor(args: argparse.Namespace, config: TsumugiConfig) -> int:
    index_path = config.resolved_index_path()
    print(f"index:   {index_path}")
    if not index_path.exists():
        print("         (does not exist yet -- run `tsumugi ingest`)")
        return 1

    size = index_path.stat().st_size
    connection = connect(index_path, create=False)
    store, index = SqliteDocumentStore(connection), FtsIndex(connection)

    print(f"size:    {size / 1024:.1f} KiB")
    print(f"schema:  {SCHEMA_VERSION}")
    print(f"sqlite:  {sqlite3.sqlite_version}")
    print(f"index:   {index.name}")
    print()
    print(f"documents:  {store.count()}")
    print(f"indexed:    {index.count()}")

    stale = [d for d in store.all_current() if len(store.versions(d.document_id)) > 1]
    print(f"revised:    {len(stale)} documents have more than one version")

    print()
    print("formats:")
    for suffix, parser in sorted(registered_suffixes().items()):
        print(f"  {suffix:<12} {parser}")

    print()
    print("by construction:")
    print("  the core opens no socket        tests/test_architecture.py")
    print("  the domain imports only stdlib  tests/test_architecture.py")
    print("  an anchor slices back exactly   tests/test_anchor.py")
    print()
    print("your responsibility:")
    print("  This index is a complete plaintext copy of the corpus, and is not")
    print("  encrypted. Disk encryption is your operating system's job.")
    print("  No redaction is running: tsumugi will place a secret into a package")
    print("  if the secret is relevant. See docs/threat-model.md.")
    return 0


def _oneline(text: str, width: int = 100) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
