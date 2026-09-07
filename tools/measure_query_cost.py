"""Where the time in a `context` call goes, and what a change to it is worth.

    python tools/measure_query_cost.py [--documents 1000]

The scaling table in `docs/measurements.md` was taken by hand, which is why it
could say *"not yet profiled at this size"* about the thing it had just
measured. This is that harness, kept.

It builds a synthetic corpus, ingests it once, and then times `build_context`
over a fixed set of queries with the index already open — so none of it is
Python start-up or the first FTS page fault.

**The corpus is Japanese**, because that is the shape the fold costs anything
on: an English corpus takes the ASCII short-circuit and folds in no time at
all, which would make this tool report that folding is free. It is free for
English. `docs/measurements.md` already records what happens when a number is
taken on the one shape that flatters it.

It reports the same call twice: as shipped, and with the fold's fast path
disabled, so the cost of the **NFKC composition walk** is a number rather than
an argument. The second run patches `unicodedata.is_normalized` to return
`False` and puts it back, which is the smallest lever that turns exactly one
branch off.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import sys
import time
import unicodedata
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tsumugi.application import search as search_module  # noqa: E402
from tsumugi.application.build_context import build_context  # noqa: E402
from tsumugi.application.ingest import ingest_paths  # noqa: E402
from tsumugi.domain.budget import Budget  # noqa: E402
from tsumugi.infrastructure.cost.heuristic import CharacterCost  # noqa: E402
from tsumugi.infrastructure.filesystem import walk  # noqa: E402
from tsumugi.infrastructure.index.fts import FtsIndex  # noqa: E402
from tsumugi.infrastructure.parsers import parser_for  # noqa: E402
from tsumugi.infrastructure.storage.database import connect  # noqa: E402
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore  # noqa: E402

NL = chr(10)

#: About the size a real document measured at across two sibling repositories.
DOCUMENT_CHARACTERS = 6_811

#: Sentences a synthetic document is built from. Ordinary Japanese: already
#: NFKC, so it takes the fast path, which is the population being measured.
SENTENCES = [
    "テントの重量は2.4kg、二人用である。",
    "予備の電池は持たない方針にした。",
    "米と味噌は現地で調達する。",
    "集合場所は駅前の広場、七時とする。",
    "雨天のときは翌日に順延する。",
    "会計は月末にまとめて精算する。",
    "装備の点検は出発の前日に行う。",
    "連絡先は名簿の通りで変更はない。",
]

QUERIES = [
    "テントの重量は",
    "予備の電池",
    "集合場所はどこか",
    "会計の精算",
    "装備の点検",
    "雨天のとき",
    "米と味噌",
    "連絡先の変更",
]


def _document(seed: int) -> str:
    body: list[str] = []
    while sum(len(line) for line in body) < DOCUMENT_CHARACTERS:
        body.append(SENTENCES[(seed + len(body)) % len(SENTENCES)])
    return f"# 記録{seed}" + NL * 2 + "".join(body)[:DOCUMENT_CHARACTERS] + NL


def _build_corpus(root: Path, documents: int) -> None:
    root.mkdir(parents=True)
    for n in range(documents):
        (root / f"note{n:05d}.md").write_text(_document(n), encoding="utf-8", newline="")


def _time_queries(store: object, index: object, repeats: int) -> list[float]:
    timings: list[float] = []
    for round_number in range(repeats):
        for query in QUERIES:
            started = time.perf_counter()
            build_context(
                query,
                store=store,  # type: ignore[arg-type]
                index=index,  # type: ignore[arg-type]
                cost_model=CharacterCost(),
                budget=Budget.characters(4000),
            )
            elapsed = time.perf_counter() - started
            if round_number:  # the first round is the warm-up
                timings.append(elapsed * 1000)
    return timings


def _report(label: str, timings: list[float]) -> float:
    median = statistics.median(timings)
    p95 = sorted(timings)[int(len(timings) * 0.95) - 1]
    print(f"| {label:<34} | {median:8.1f} ms | {p95:8.1f} ms |")
    return median


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=1_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--keep", action="store_true", help="leave the corpus behind for a second run"
    )
    args = parser.parse_args(argv)

    workspace = ROOT / ".measure-query-cost"
    if workspace.exists():
        shutil.rmtree(workspace)
    root = workspace / "corpus"

    print(f"Building {args.documents:,} documents of {DOCUMENT_CHARACTERS:,} characters...")
    _build_corpus(root, args.documents)
    corpus_bytes = sum(f.stat().st_size for f in root.glob("*.md"))

    connection = connect(workspace / "index.db")
    store, index = SqliteDocumentStore(connection), FtsIndex(connection)

    started = time.perf_counter()
    ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)
    ingest_seconds = time.perf_counter() - started
    print(
        f"corpus {corpus_bytes / 1024 / 1024:.1f} MiB, ingest {ingest_seconds:.1f} s "
        f"({ingest_seconds / args.documents * 1000:.1f} ms a document)"
    )
    print()
    print(f"| {'`context`':<34} | {'median':>11} | {'p95':>11} |")
    print("|" + "-" * 36 + "|" + "-" * 13 + "|" + "-" * 13 + "|")

    after = _report("as shipped", _time_queries(store, index, args.repeats))

    # The same corpus with the fold's fast path removed, so the composition
    # walk's cost is measured rather than argued. Restored before returning:
    # a tool that leaves a module patched is a tool that lies to the next one.
    # `search` imports the module, not the function, so patching it here is
    # the same object the fold consults. `patch.object` puts it back.
    with patch.object(unicodedata, "is_normalized", return_value=False):
        search_module._folded.cache_clear()
        before = _report("without the fold's fast path", _time_queries(store, index, args.repeats))
    search_module._folded.cache_clear()

    print()
    saved = before - after
    print(
        f"The NFKC composition walk was **{saved:.0f} ms** of a "
        f"{before:.0f} ms call ({saved / before:.0%})."
    )

    connection.close()
    if not args.keep:
        shutil.rmtree(workspace, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
