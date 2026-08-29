"""The command line. The only place that wires the layers together.

Every command that touches the index prints where the index is. That is not
chatter: the index is a complete plaintext copy of whatever corpus it was built
from, and a file you do not know about is a file you cannot protect
(``docs/threat-model.md``).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

from ... import __version__
from ...application.ask import ask
from ...application.build_context import build_context
from ...application.forgetting import forget_documents
from ...application.ingest import ingest_paths
from ...application.search import search as run_search
from ...application.trace import trace_quotation
from ...application.verify import verify_answer
from ...config import TsumugiConfig
from ...domain.budget import Budget, Unit
from ...domain.package import ContextPackage
from ...errors import ConfigurationError, TsumugiError
from ...evaluation.answering import answer_cases, summarise_answers
from ...evaluation.dataset import Case, load_cases
from ...evaluation.runner import run_cases
from ...evaluation.scoring import FLOORS, summarise
from ...infrastructure.adapters.ollama import DEFAULT_MODEL, DEFAULT_URL, OllamaProvider
from ...infrastructure.cost.heuristic import ByteCost, CharacterCost, HeuristicTokenCost
from ...infrastructure.filesystem import IgnoreRules, walk
from ...infrastructure.freshness import FilesystemFreshness, remembered_roots
from ...infrastructure.index.fts import FtsIndex
from ...infrastructure.parsers import parser_for, registered_suffixes
from ...infrastructure.storage.database import SCHEMA_VERSION, connect, empty
from ...infrastructure.storage.ledger import SqliteLedger
from ...infrastructure.storage.sqlite import SqliteDocumentStore
from ...ports.cost import CostModel
from ..mcp.server import serve
from .demo import run_demo

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
    ingest.add_argument(
        "--rebuild",
        action="store_true",
        help=(
            "discard the index and read the corpus again. Needed after the tokenizer "
            "changes, because terms from two tokenizers do not line up and the failure "
            "looks like an empty corpus"
        ),
    )
    ingest.set_defaults(run=_ingest)

    find = commands.add_parser("search", help="find spans of the corpus")
    find.add_argument("query")
    find.add_argument("-n", "--limit", type=int, default=10)
    find.set_defaults(run=_search)

    context = commands.add_parser("context", help="build a ContextPackage for a question")
    context.add_argument("query")
    context.add_argument(
        "--budget",
        default="characters:4000",
        metavar="UNIT:N",
        help=(
            "tokens:8000, characters:20000 or bytes:65536. The unit is required: "
            "tokens are estimated and the estimate states its error, while "
            "characters and bytes are counted (default: characters:4000)"
        ),
    )
    context.add_argument(
        "--min-score", type=float, default=0.0, help="relevance floor for inclusion"
    )
    context.add_argument(
        "--corpus",
        type=Path,
        metavar="PATH",
        help=(
            "where the corpus lives now, if it has moved. The index remembers where "
            "each document was read from, so staleness is checked without this"
        ),
    )
    context.add_argument("--json", action="store_true", help="emit the package itself")
    context.add_argument(
        "--why", action="store_true", help="print what was left out, and under which rule"
    )
    context.set_defaults(run=_context)

    question = commands.add_parser(
        "ask",
        help="build a package, send it to a local model, and check what comes back",
        description=(
            "The only command that sends anything anywhere. It refuses a non-local "
            "endpoint unless --allow-remote says otherwise, because this index holds "
            "a copy of your corpus. Requires a running ollama; everything else in "
            "tsumugi works with no model and no network."
        ),
    )
    question.add_argument("query")
    question.add_argument(
        "--budget",
        default="tokens:4000",
        metavar="UNIT:N",
        help="tokens:4000, characters:20000 or bytes:65536 (default: tokens:4000)",
    )
    question.add_argument("--model", default=DEFAULT_MODEL, help=f"(default: {DEFAULT_MODEL})")
    question.add_argument("--url", default=DEFAULT_URL, help=f"(default: {DEFAULT_URL})")
    question.add_argument(
        "--allow-remote",
        action="store_true",
        help="send to a host that is not this machine. Says what it means.",
    )
    question.add_argument("--min-score", type=float, default=0.0, help="relevance floor")
    question.add_argument(
        "--corpus", type=Path, metavar="PATH", help="where the corpus lives now, if it has moved"
    )
    question.add_argument(
        "--show-prompt", action="store_true", help="print exactly what was sent, first"
    )
    question.set_defaults(run=_ask)

    trace = commands.add_parser("trace", help="find where a quotation came from")
    trace.add_argument("quotation")
    trace.set_defaults(run=_trace)

    verify = commands.add_parser("verify", help="resolve the citations in a model's answer")
    verify.add_argument("answer", type=Path, help="the answer, as JSON; - for stdin")
    verify.add_argument("--package", type=Path, required=True, help="the package it was built from")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(run=_verify)

    forget = commands.add_parser(
        "forget",
        help="remove documents from the index, and vacuum",
        description=(
            "The index keeps the text it anchored, so deleting a file from your corpus "
            "does not delete it from here. This does, and vacuums afterwards -- removing "
            "rows is not removing text."
        ),
    )
    forget.add_argument("paths", nargs="+", help="source paths, as `tsumugi search` prints them")
    forget.set_defaults(run=_forget)

    ledger = commands.add_parser("ledger", help="what was sent, and what the model actually used")
    ledger.add_argument(
        "--since", metavar="ISO8601", help="only entries at or after this timestamp"
    )
    ledger.add_argument("-n", "--limit", type=int, default=20)
    ledger.add_argument("--forget", action="store_true", help="delete the whole ledger")
    ledger.set_defaults(run=_ledger)

    mcp = commands.add_parser(
        "mcp",
        help="speak MCP on stdin/stdout, so an agent can use this corpus",
        description=(
            "A read-only MCP server over JSON-RPC on stdio. Exposes search, context, "
            "trace and verify. Nothing that writes to the corpus or the index is "
            "reachable from here."
        ),
    )
    mcp.set_defaults(run=_mcp)

    evaluate = commands.add_parser("eval", help="score the selection against the labelled corpus")
    evaluate.add_argument(
        "--cases", type=Path, default=Path("tests/cases"), help="where the cases live"
    )
    evaluate.add_argument("--tier", choices=("ci", "full"), help="narrow to one tier")
    evaluate.add_argument(
        "--split",
        choices=("train", "held_out"),
        help="held_out cases are not read while tuning; scoring them separately is "
        "what says whether a number is fitted to the cases it came from",
    )
    evaluate.add_argument(
        "--model",
        metavar="NAME",
        help=(
            "also put every case to a local model and report what it did with the "
            "package. Never a gate: a model is not in CI and a number that depends on "
            "which one you pulled is not a floor anybody can hold."
        ),
    )
    evaluate.add_argument(
        "--failures", action="store_true", help="name every case that is not clean"
    )
    evaluate.set_defaults(run=_eval)

    demo = commands.add_parser(
        "demo",
        help="watch the whole thing happen, in a throwaway directory",
        description=(
            "Builds a small rigged corpus, asks it something, shows what was left out "
            "and why, checks an answer's citations, and traces one back to a line. No "
            "model, no network, nothing written outside a temporary directory."
        ),
    )
    demo.add_argument(
        "--keep", action="store_true", help="leave the corpus and index behind to poke at"
    )
    demo.add_argument(
        "--model",
        metavar="NAME",
        help=(
            "also put the question to a local model at the end, e.g. "
            "qwen2.5:7b-instruct. Needs ollama running; everything else does not."
        ),
    )
    demo.set_defaults(run=_demo)

    doctor = commands.add_parser("doctor", help="what this index holds, and what it is")
    doctor.set_defaults(run=_doctor)

    return parser


#: Connections opened while serving one command, closed when it returns.
#:
#: A process that exits closes its files for you, which is why this went
#: unnoticed: every command worked. But a leaked connection holds a read lock,
#: and a read lock stops the next command's `wal_checkpoint` from truncating --
#: so `forget` would vacuum and leave the text in the write-ahead log. Found by
#: a test that runs two commands in one process.
_OPEN: list[sqlite3.Connection] = []


def _connect(path: Path, *, create: bool = True) -> sqlite3.Connection:
    connection = connect(path, create=create)
    _OPEN.append(connection)
    return connection


def _close_everything() -> None:
    while _OPEN:
        with contextlib.suppress(sqlite3.Error):  # closing twice is harmless
            _OPEN.pop().close()


def _speak_utf8() -> None:
    """Make the output streams able to carry the corpus.

    A library for Japanese notes that raises ``UnicodeEncodeError`` the moment
    somebody pipes it into a pager is not a library for Japanese notes. On
    Windows a redirected stream takes the locale codepage -- cp932, cp1252 --
    and an em dash or a kanji is enough to end the run with a traceback in
    place of an answer.

    ``errors="replace"`` is the second half and matters as much. A character
    that genuinely cannot be written should cost one glyph, not the output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _speak_utf8()
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
    finally:
        _close_everything()


# -- commands ------------------------------------------------------------


def _ingest(args: argparse.Namespace, config: TsumugiConfig) -> int:
    index_path = config.resolved_index_path()
    root = args.path.resolve()
    if not root.exists():
        print(f"tsumugi: no such path: {args.path}", file=sys.stderr)
        return 2

    print(f"index:  {index_path}")
    print(f"corpus: {root}")

    connection = _connect(index_path)
    if args.rebuild:
        # Emptied rather than deleted: the file is open, and its path is often
        # one the user named.
        print("rebuild: discarding what the index holds")
        empty(connection)
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
    connection = _connect(config.resolved_index_path(), create=False)
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


def _cost_model(unit: Unit) -> CostModel:
    """The composition root's one job for budgets."""
    if unit is Unit.TOKENS:
        return HeuristicTokenCost()
    if unit is Unit.BYTES:
        return ByteCost()
    return CharacterCost()


def _context(args: argparse.Namespace, config: TsumugiConfig) -> int:
    try:
        budget = Budget.parse(args.budget)
    except ValueError as error:
        raise ConfigurationError(str(error)) from error

    connection = _connect(config.resolved_index_path(), create=False)
    store, index = SqliteDocumentStore(connection), FtsIndex(connection)

    package = build_context(
        args.query,
        store=store,
        index=index,
        cost_model=_cost_model(budget.unit),
        budget=budget,
        candidate_limit=config.candidate_limit,
        minimum_score=args.min_score,
        version=__version__,
        # On by default. A check the caller has to remember to turn on is a
        # check that is off, and offering a passage from an edited file as
        # current is the thing ADR-0010 exists to prevent.
        freshness=(FilesystemFreshness(args.corpus) if args.corpus else remembered_roots(store)),
    )

    SqliteLedger(connection).open(package)

    if args.json:
        print(package.to_json())
        return 0 if package.items else 1

    print(package.render())
    print()
    print(f"--- {package.package_id.short()} ---")
    print(
        f"{len(package.items)} items, {package.budget.estimate}/{package.budget.budget.limit} "
        f"{package.budget.budget.unit.value} via {package.budget.estimator}"
    )
    if package.budget.measured_error is not None:
        reported = package.budget.measured_error
        # An estimate that does not say how wrong it is will mislead a caller
        # exactly once, expensively (ADR-0006).
        print(
            f"estimated, not counted: p50 {reported['p50']:.1%} "
            f"p95 {reported['p95']:.1%} against {reported['against']}"
        )
    if args.why:
        print()
        print(package.why_not())
    elif package.omissions:
        print(f"{len(package.omissions)} candidates were left out; --why to see them")

    return 0 if package.items else 1


def _ask(args: argparse.Namespace, config: TsumugiConfig) -> int:
    try:
        budget = Budget.parse(args.budget)
    except ValueError as error:
        raise ConfigurationError(str(error)) from error

    provider = OllamaProvider(model=args.model, url=args.url, allow_remote=args.allow_remote)
    connection = _connect(config.resolved_index_path(), create=False)
    store, index = SqliteDocumentStore(connection), FtsIndex(connection)

    # Said before anything is sent, not after. A person who did not mean to
    # reach a remote host should find that out while they can still stop it.
    print(f"sending to {provider.name} at {provider.endpoint.describe()}", file=sys.stderr)

    asked = ask(
        args.query,
        store=store,
        index=index,
        cost_model=_cost_model(budget.unit),
        budget=budget,
        provider=provider,
        ledger=SqliteLedger(connection),
        candidate_limit=config.candidate_limit,
        minimum_score=args.min_score,
        version=__version__,
        freshness=(FilesystemFreshness(args.corpus) if args.corpus else remembered_roots(store)),
    )

    if args.show_prompt:
        print(asked.prompt)
        print()

    print(asked.answer_text() or asked.answer)
    print()
    print(f"--- {asked.package.package_id.short()} ---")
    for claim in asked.verification.claims:
        print(f"  {claim.support.value:<12} {claim.text}")
    print(f"  {asked.verification.summary()}")
    # Deliberately not phrased as a pass. A supported claim means the quotation
    # was where the model said it was, which is a smaller thing than true.
    if not asked.trustworthy:
        print("  not every claim is supported by the context it was given.")

    return 0 if asked.trustworthy else 1


def _trace(args: argparse.Namespace, config: TsumugiConfig) -> int:
    connection = _connect(config.resolved_index_path(), create=False)
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


def _verify(args: argparse.Namespace, config: TsumugiConfig) -> int:
    try:
        package = ContextPackage.from_json(args.package.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise ConfigurationError(f"cannot read the package: {error}") from error

    try:
        answer = (
            sys.stdin.read() if str(args.answer) == "-" else args.answer.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError) as error:
        # A model's answer arrives through whatever wrote the file, and on
        # Windows that is often not UTF-8. One sentence beats a traceback.
        raise ConfigurationError(f"cannot read the answer: {error}") from error

    report = verify_answer(answer, package)

    # Closing needs the index the package was built against, and a caller may
    # be verifying a package built somewhere else entirely. That is legitimate,
    # so a missing ledger entry is not an error.
    index_path = config.resolved_index_path()
    if index_path.exists():
        SqliteLedger(_connect(index_path, create=False)).close(report)

    if args.json:
        print(
            json.dumps(
                {
                    "package_id": report.package_id,
                    "counts": report.counts,
                    "claims": [
                        {
                            "text": claim.text,
                            "support": claim.support.value,
                            "citations": [
                                {
                                    "quotation": citation.quotation,
                                    "locations": [
                                        {
                                            "item_id": location.item_id,
                                            "source_path": location.source_path,
                                            "start": location.anchor.span.start,
                                            "end": location.anchor.span.end,
                                        }
                                        for location in citation.locations
                                    ],
                                }
                                for citation in claim.citations
                            ],
                        }
                        for claim in report.claims
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if report.clean else 1

    for claim in report.claims:
        print(f"{claim.support.value:<13} {_oneline(claim.text, 76)}")
        for citation in claim.citations:
            if not citation.resolved:
                print(f"              x  {_oneline(citation.quotation, 60)}")
                print("                 not found in the text that was sent")
                continue
            for location in citation.locations:
                print(f"              -> {location.describe()}")
            if citation.ambiguous:
                # Ambiguity is information, not an error.
                print(f"                 {len(citation.locations)} occurrences, all reported")
        if claim.unverifiable_because:
            print(f"                 {claim.unverifiable_because}")

    print()
    print(report.summary())
    print()
    # The sentence that has to be printed every time. The failure mode of an
    # evidence system is that people stop reading "evidence" and start reading
    # it as "correct".
    print("A supported claim means the quoted text is where the model said it was.")
    print("It does not mean the claim is true.")
    return 0 if report.clean else 1


def _mcp(args: argparse.Namespace, config: TsumugiConfig) -> int:
    # Nothing is printed here. stdout carries JSON-RPC responses and nothing
    # else; a stray line corrupts the stream and the client sees a parse error
    # it cannot attribute.
    return serve(config)


def _eval(args: argparse.Namespace, config: TsumugiConfig) -> int:
    if not args.cases.is_dir():
        raise ConfigurationError(
            f"no cases at {args.cases}. Generate them with "
            f"`python tools/generate_cases.py --out {args.cases}`."
        )

    cases = load_cases(args.cases, tier=args.tier, split=args.split)
    if not cases:
        raise ConfigurationError("no cases matched those filters")

    # Uses its own throwaway index per case, not the caller's corpus.
    scores = run_cases(cases, candidate_limit=config.candidate_limit)
    summary = summarise(scores)
    print(summary.describe())

    if args.failures:
        print()
        for score in scores:
            if score.clean:
                continue
            print(f"  {score.case_id}")
            if score.missed:
                print(f"    missing:  {', '.join(score.missed)}")
            if score.sprung:
                print(f"    trapped:  {', '.join(score.sprung)}")
            for fact_id, actual in score.misexplained:
                print(f"    {fact_id}: dropped under {actual}, not the expected rule")

    breached = FLOORS.breached_by(summary)
    print()
    if breached:
        for problem in breached:
            print(f"BELOW FLOOR: {problem}")
    else:
        print(
            f"floors held: recall >= {FLOORS.evidence_recall:.0%}, "
            f"traps <= {FLOORS.trap_rate:.0%}, "
            f"reasons >= {FLOORS.omission_correctness:.0%}, "
            f"budget and reproducibility exact"
        )

    if args.model:
        _answer_report(cases, args.model, config)

    print()
    print("The corpus is generated and tidier than anything anyone writes.")
    if not args.model:
        print("Nothing here measures whether an answer built from a package is correct.")
        print("`--model NAME` does, against a local one, and is never a gate.")
    # Floors, not perfection. A gate set at today's score makes every honest
    # experiment a build failure and turns tuning into threshold-chasing.
    return 1 if breached else 0


def _forget(args: argparse.Namespace, config: TsumugiConfig) -> int:
    connection = _connect(config.resolved_index_path(), create=False)
    store, index = SqliteDocumentStore(connection), FtsIndex(connection)

    removed, missing = forget_documents(args.paths, store=store, index=index)
    for gone in removed:
        print(f"forgotten  {gone.source_path}  ({gone.versions} revisions)")
    for path in missing:
        # Not an error: asking to forget something already gone is reasonable.
        print(f"not held   {path}")

    if removed:
        print()
        print(f"{len(removed)} documents removed and the index vacuumed.")
        print("Anything already sent to a model is not covered by this.")
    return 0 if removed else 1


def _answer_report(cases: Sequence[object], model: str, config: TsumugiConfig) -> None:
    """The opt-in half: what a real model did with each package.

    Printed after the deterministic scores and never folded into them. The
    numbers above are a property of this code; the numbers below are a property
    of this code *and* whichever model happened to be pulled, and mixing the
    two would make the first kind unfalsifiable.
    """
    provider = OllamaProvider(model=model)
    print()
    print(f"--- and what {provider.name} did with them, at {provider.endpoint.describe()} ---")
    scores = answer_cases(
        cast("Sequence[Case]", cases), provider, candidate_limit=config.candidate_limit
    )
    print(summarise_answers(scores, model=provider.name).describe())

    wrong = [s for s in scores if s.ran and not s.abstained_correctly and s.expected_to_abstain]
    if wrong:
        # The one the deterministic suite cannot reach. tsumugi reports that a
        # corpus may not answer a question and deliberately does not gate on
        # it, because that call is the model's -- so this is where the cost of
        # that decision shows up, or does not.
        print()
        print(f"  answered anyway where the corpus has no answer: {len(wrong)}")
        for score in wrong[:5]:
            print(f"    {score.case_id}")


def _demo(args: argparse.Namespace, config: TsumugiConfig) -> int:
    # Deliberately ignores the configured index: a demo must not touch, or even
    # open, whatever real corpus the reader has.
    return run_demo(keep=args.keep, model=args.model)


def _ledger(args: argparse.Namespace, config: TsumugiConfig) -> int:
    connection = _connect(config.resolved_index_path(), create=False)
    ledger = SqliteLedger(connection)

    if args.forget:
        removed = ledger.forget()
        print(f"deleted {removed} entries. The ledger is derived data; this costs history.")
        return 0

    entries = ledger.entries(since=args.since, limit=args.limit)
    if not entries:
        print("the ledger is empty. It fills as you run `tsumugi context`.")
        return 1

    for entry in entries:
        mark = "closed" if entry.closed else "open  "
        used = "" if entry.cited_items is None else f", {entry.cited_items} cited"
        print(
            f"{mark} {entry.created_at[:19]}  {entry.package_id[7:19]}  "
            f"{entry.items} items{used}, {entry.omissions} omitted, "
            f"{entry.estimate}/{entry.limit} {entry.unit}"
        )

    usage = ledger.usage(since=args.since)
    print()
    print(
        f"{usage.packages} packages, {usage.closed} verified, "
        f"{usage.omissions} candidates left out "
        f"({usage.budget_exhausted} of them for budget)"
    )

    share = usage.uncited_share
    if share is None:
        # Reporting 100% unused for a ledger nobody closed would be a lie about
        # the tool rather than about the corpus.
        print("Nothing has been verified yet, so nothing can be said about what was used.")
        print("Run `tsumugi verify` on an answer to close an entry.")
    else:
        print(
            f"Of the context that was sent and checked, {share:.0%} was never cited "
            f"({usage.items_sent - usage.items_cited} of {usage.items_sent} items)."
        )
    return 0


def _doctor(args: argparse.Namespace, config: TsumugiConfig) -> int:
    index_path = config.resolved_index_path()
    print(f"index:   {index_path}")
    if not index_path.exists():
        print("         (does not exist yet -- run `tsumugi ingest`)")
        return 1

    size = index_path.stat().st_size
    connection = _connect(index_path, create=False)
    store, index = SqliteDocumentStore(connection), FtsIndex(connection)

    print(f"size:    {size / 1024:.1f} KiB")
    print(f"schema:  {SCHEMA_VERSION}")
    print(f"sqlite:  {sqlite3.sqlite_version}")
    print(f"index:   {index.name}")
    print()
    print(f"documents:  {store.count()}")
    print(f"indexed:    {index.count()}")

    revised = [d for d in store.all_current() if len(store.versions(d.document_id)) > 1]
    print(f"revised:    {len(revised)} documents have more than one version")

    # Now that the index remembers its corpus, drift is checkable rather than
    # only mentionable.
    checker = remembered_roots(store)
    unrecorded = [d for d in store.all_current() if store.corpus_root_of(d.document_id) is None]
    drifted = [
        d
        for d in store.all_current()
        if store.corpus_root_of(d.document_id) is not None and not checker.is_current(d)
    ]
    print(f"drifted:    {len(drifted)} files have changed since they were indexed")
    for document in drifted[:5]:
        print(f"            {document.source_path}")
    if len(drifted) > 5:
        print(f"            ...and {len(drifted) - 5} more")
    if unrecorded:
        # "Cannot check" is a different answer from "unchanged", and saying so
        # is the difference between a report and a reassurance.
        print(
            f"unchecked:  {len(unrecorded)} documents predate schema 2 and do not "
            f"record where they came from"
        )

    for root in store.corpus_roots():
        print(f"corpus:     {root}")

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
    entries = SqliteLedger(connection).usage()
    print(f"ledger:     {entries.packages} packages recorded, {entries.closed} verified")
    print("            identifiers and counts only; no query or document text")
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
