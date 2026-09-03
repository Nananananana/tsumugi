"""What `import tsumugi` gives you, and that it is enough to do the job.

v1.0's second promise is that the public surface stops moving without notice.
Writing that down turned out to need a correction first: the promise named
`build_context`, `search`, `verify` and `ask`, and **none of the four were
exported**. `import tsumugi` could read the contract and nothing else.

Finding that took running it rather than reading it. Six imports were missing,
and each one only appeared after the previous was fixed:

    ingest_paths()   missing keyword-only argument 'parser_for'
    build_context()  missing keyword-only argument 'cost_model'
    cost_model_for() returns a *name*; the model itself lives in infrastructure

The last is the one a reader would not have predicted. `cost_model_for` is
public-looking and returns `"characters"`, a string, because -- as its own
docstring says -- "wiring lives in the interfaces layer". The composition root
was the only place that knew how to turn a `Unit` into a `CostModel`, so the
library's central function required an object the library's public surface
could not produce.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest

import tsumugi

#: Every name `import tsumugi` publishes, pinned.
#:
#: Not a style rule. This is the mechanism behind v1.0's second promise: a
#: rename or a removal has to edit this list, which puts it in a diff and in a
#: review, instead of arriving in a release note nobody wrote. Adding a name is
#: a one-line change here and is backwards compatible; taking one away is the
#: event the promise is about, and `docs/adr/0023` says what has to happen
#: around it.
PUBLIC_SURFACE = frozenset(
    {
        "SqliteLedger",
        "Support",
        "remembered_roots",
        "trace_quotation",
        "walk",
        "ORDERINGS",
        "by_score",
        "maximal_marginal_relevance",
        "as_documents",
        "texts_from",
        "Anchor",
        "AnswerFormatError",
        "Asked",
        "Block",
        "Budget",
        "ByteCost",
        "CONTRACT_SCHEMA_NAME",
        "CharacterCost",
        "ConfigurationError",
        "ContentHash",
        "Document",
        "FtsIndex",
        "HeuristicTokenCost",
        "IngestReport",
        "IngestionError",
        "ProtectedPackageError",
        "Resolution",
        "ResolutionStatus",
        "SearchResult",
        "Section",
        "Span",
        "SqliteDocumentStore",
        "StorageError",
        "TsumugiError",
        "Unit",
        "UnsupportedContractError",
        "__version__",
        "ask",
        "build_context",
        "connect",
        "contract_schema",
        "contract_schema_text",
        "cost_model_for",
        "ingest_paths",
        "parse_answer",
        "parser_for",
        "register_block_kind",
        "resolve",
        "search",
        "verify_answer",
    }
)


#: The CLI verbs, pinned for the same reason and by the same argument: a script
#: someone wrote is broken by a rename exactly as surely as an import is, and
#: the CLI is the surface most people actually meet.
CLI_VERBS = frozenset(
    {
        "ask",
        "context",
        "demo",
        "doctor",
        "eval",
        "forget",
        "ingest",
        "ledger",
        "mcp",
        "search",
        "trace",
        "verify",
    }
)


def test_the_cli_verbs_are_what_they_were() -> None:
    """Read off the parser, not off a list in the docs.

    `AGENTS.md` also lists the verbs, and a list that is maintained by hand
    beside a list that is generated is two lists -- this repository deleted
    three stale test counts on exactly that argument.
    """
    from tsumugi.interfaces.cli.main import build_parser

    subparsers = [
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(subparsers) == 1, "the parser shape changed under this test"
    assert set(subparsers[0].choices) == CLI_VERBS


def test_the_public_surface_is_what_it_was() -> None:
    """`__all__` matches the pin, and every name in it actually resolves.

    Two halves, and the second is the one that has failed before elsewhere: a
    name in `__all__` that does not exist makes `from tsumugi import *` raise
    and every other import look fine.
    """
    assert set(tsumugi.__all__) == PUBLIC_SURFACE, {
        "added": sorted(set(tsumugi.__all__) - PUBLIC_SURFACE),
        "removed": sorted(PUBLIC_SURFACE - set(tsumugi.__all__)),
    }
    missing = sorted(name for name in tsumugi.__all__ if not hasattr(tsumugi, name))
    assert not missing, f"exported but not importable: {missing}"


def test_the_whole_job_can_be_done_from_the_top_level(tmp_path: Path) -> None:
    """Ingest, build a package, and read why something was left out.

    Deliberately written the way a reader would write it -- one `import
    tsumugi` and nothing deeper -- because that is the only version of this
    test that can fail. Written against the deep imports it would have passed
    before any of them were exported, and proved nothing.
    """
    (tmp_path / "notes.md").write_text("# 装備\n\nテントは 2.4kg です。\n", encoding="utf-8")

    connection = tsumugi.connect(tmp_path / "index.db")
    try:
        store = tsumugi.SqliteDocumentStore(connection)
        index = tsumugi.FtsIndex(connection)
        tsumugi.ingest_paths(
            [tmp_path / "notes.md"],
            root=tmp_path,
            store=store,
            index=index,
            parser_for=tsumugi.parser_for,
        )

        package = tsumugi.build_context(
            "テントは",
            store=store,
            index=index,
            budget=tsumugi.Budget.characters(400),
            cost_model=tsumugi.CharacterCost(),
        )
    finally:
        connection.close()

    assert package.contract == "tsumugi.context-package/1"
    assert package.items, "the shallow path built an empty package"
    assert "2.4kg" in package.items[0].text


def test_a_question_nothing_confirms_says_so_rather_than_returning_nothing(
    tmp_path: Path,
) -> None:
    """An empty package explains itself, through the same shallow path.

    The first run of the flow above used a paraphrase and returned no items.
    That is correct -- it is the residual `proposals/0003` names -- and the
    point is that the package said which rule dropped the candidate rather than
    coming back empty and silent. A consumer who only has the top level can
    still find that out.
    """
    (tmp_path / "notes.md").write_text("# 装備\n\nテントは 2.4kg です。\n", encoding="utf-8")

    connection = tsumugi.connect(tmp_path / "index.db")
    try:
        store = tsumugi.SqliteDocumentStore(connection)
        index = tsumugi.FtsIndex(connection)
        tsumugi.ingest_paths(
            [tmp_path / "notes.md"],
            root=tmp_path,
            store=store,
            index=index,
            parser_for=tsumugi.parser_for,
        )
        package = tsumugi.build_context(
            "テントの重さは",
            store=store,
            index=index,
            budget=tsumugi.Budget.characters(400),
            cost_model=tsumugi.CharacterCost(),
        )
    finally:
        connection.close()

    assert not package.items
    assert package.omissions, "an empty package with no omissions says nothing at all"
    assert package.omissions[0].reason


def test_connect_hands_back_something_the_caller_must_close(tmp_path: Path) -> None:
    """`connect` returns a raw connection, and the caller owns it.

    Recorded because it is the one rough edge the walk-through hit that is not
    fixed: the first version of that script leaked the connection and Windows
    refused to delete the directory. The CLI has a registry that closes
    everything at exit; a library caller has `try/finally` and this sentence.
    """
    connection = tsumugi.connect(tmp_path / "index.db")
    assert isinstance(connection, sqlite3.Connection)
    connection.close()
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
