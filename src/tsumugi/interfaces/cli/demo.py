"""A corpus, a question, and the whole thing happening in front of you.

Runs in a throwaway directory with no model, no network and nothing installed
beyond the library. ``--model`` adds a last stage that asks a real local one,
which is opt-in precisely because everything before it does not need it.

The point is not that it works — it is that each step says what it is *for*,
because the parts of this design that matter are the ones that are easy to skip
past: what was left out, why a token count is an
estimate, and the difference between a quotation being real and a claim being
true.

The corpus is small and rigged. It holds an answer, an older version of that
answer, a verbatim copy of it, and a document about something adjacent — which
is four of the seven adversaries the evaluation corpus plants, at a size a
person can read in one screen.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from ... import __version__
from ...application.ask import ask
from ...application.build_context import build_context
from ...application.ingest import ingest_paths
from ...application.trace import trace_quotation
from ...application.verify import verify_answer
from ...domain.budget import Budget
from ...errors import TsumugiError
from ...infrastructure.adapters.ollama import OllamaProvider as OllamaProvider
from ...infrastructure.cost.heuristic import HeuristicTokenCost
from ...infrastructure.filesystem import walk
from ...infrastructure.freshness import remembered_roots
from ...infrastructure.index.fts import FtsIndex
from ...infrastructure.parsers import parser_for
from ...infrastructure.storage.database import connect
from ...infrastructure.storage.ledger import SqliteLedger
from ...infrastructure.storage.sqlite import SqliteDocumentStore

__all__ = ["CORPUS", "QUESTION", "run_demo"]

QUESTION = "テントの重量は"

#: Rigged, and readable in one screen. The answer, an older version of it, a
#: verbatim copy, and a document about something adjacent.
CORPUS: dict[str, str] = {
    "notes/2026-06-装備.md": (
        "---\ntitle: 装備メモ\n---\n\n"
        "# 装備\n\n"
        "テントの重量は2.4kg、二人用。前回より300g軽い。\n\n"
        "ガスは250gカートリッジを1本。予備は持たない。\n"
    ),
    "notes/2025-09-装備.md": (
        "# 装備（旧）\n\n"
        "テントの重量は3.1kg、二人用。\n\n"
        "※この記録は古い。2026年の改訂版を参照すること。\n"
    ),
    "notes/持ち物リスト.md": (
        "# 持ち物リスト（控え）\n\n"
        "過去の記録から転記。\n\n"
        "テントの重量は2.4kg、二人用。前回より300g軽い。\n"
    ),
    "notes/キャンプ道具.md": (
        "# キャンプ道具\n\nキャンプ用タープの重量は3.1kg、二人用。設営は二人がかり。\n"
    ),
    "notes/budget.md": (
        "# Budget\n\nThe unit is explicit at the call site: tokens, characters or bytes.\n"
    ),
}


def _rule(title: str) -> None:
    print()
    print(f"\033[1m{title}\033[0m" if _colour() else title)
    print("-" * max(40, len(title)))


def _colour() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def run_demo(*, keep: bool = False, model: str | None = None) -> int:
    """Build a corpus, ask it something, and check the answer."""
    workspace = Path(tempfile.mkdtemp(prefix="tsumugi-demo-"))
    root = workspace / "notes-corpus"
    try:
        return _run(workspace, root, model=model)
    finally:
        if keep:
            print()
            print(f"kept: {workspace}")
            print(f"  tsumugi --index {workspace / 'index.db'} search テント")
        else:
            shutil.rmtree(workspace, ignore_errors=True)


def _run(workspace: Path, root: Path, *, model: str | None = None) -> int:
    print(f"tsumugi {__version__} — a walk through, in a throwaway directory.")
    if model is None:
        print("No model. No network. Nothing installed beyond the library.")
        print("(--model qwen2.5:7b-instruct adds a last stage that asks a real one.)")
    else:
        print(f"Nothing installed beyond the library. Stage 8 will ask {model}.")

    # ---------------------------------------------------------------- ingest
    _rule("1. A small corpus")
    for relative, text in CORPUS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    (root / ".env").write_text("API_KEY=not-a-real-key\n", encoding="utf-8")

    for relative in CORPUS:
        print(f"  {relative}")
    print("  .env                       <- a file that looks like a credential store")
    print()
    print("Rigged on purpose: the answer, an older version of it, a verbatim copy,")
    print("and a document about something adjacent that shares its vocabulary.")

    connection = connect(workspace / "index.db")
    store, index = SqliteDocumentStore(connection), FtsIndex(connection)
    found = walk(root)
    report = ingest_paths(found.files, root=root, store=store, index=index, parser_for=parser_for)

    _rule("2. Reading it")
    print(f"  {report.summary()}")
    for skipped in found.skipped:
        print(f"  refused  {skipped.path}  ({skipped.reason})")
    print()
    print("The .env is refused and named. Silently skipping and silently including")
    print("are both wrong: say what was not read.")

    # --------------------------------------------------------------- context
    budget = Budget.tokens(120)
    package = build_context(
        QUESTION,
        store=store,
        index=index,
        cost_model=HeuristicTokenCost(),
        budget=budget,
        version=__version__,
        freshness=remembered_roots(store),
    )
    SqliteLedger(connection).open(package)

    _rule(f"3. A ContextPackage for {QUESTION!r}, under {budget}")
    for item in package.items:
        signals = ", ".join(item.selection.signals) if item.selection else ""
        print(f"  {item.item_id}  {item.describe()}  {item.cost} tokens")
        print(f"        {_one_line(item.text)}")
        if signals:
            print(f"        signals: {signals}")

    error = package.budget.measured_error
    print()
    print(f"  {package.budget.estimate}/{budget.limit} tokens via {package.budget.estimator}")
    if error is not None:
        print(
            f"  estimated, not counted: p50 {error['p50']:.1%}, p95 {error['p95']:.1%} "
            f"against {error['against']}"
        )
    print("  A token count with no stated error is a number pretending to be a")
    print("  measurement. Characters and bytes are counted; tokens are not.")

    # -------------------------------------------------------------- omissions
    _rule("4. And what it left out — the part worth reading")
    print(package.why_not())
    print()
    print("A model cannot see the edge of a selection. Told nothing, it answers with")
    print("the confidence of complete information over context it cannot see the")
    print("edges of, so the rendered prompt carries a NOT INCLUDED section too.")

    # ----------------------------------------------------------------- verify
    _rule("5. An answer, and its citations checked")
    quoted = package.items[0].text.strip()[:14] if package.items else "テントの重量は"
    answer = {
        "claims": [
            {"text": "テントは2.4kgである。", "citations": [quoted]},
            {"text": "テントは前回より1kg軽い。", "citations": ["前回より1kg軽い"]},
            {"text": "だいたい軽いほうだと思う。", "citations": []},
        ]
    }
    verification = verify_answer(json.dumps(answer, ensure_ascii=False), package)
    SqliteLedger(connection).close(verification)

    for claim in verification.claims:
        print(f"  {claim.support.value:<12} {claim.text}")
        for citation in claim.citations:
            if citation.resolved:
                for location in citation.locations:
                    print(f"               -> {location.describe()}")
            else:
                print(f"               x  {_one_line(citation.quotation, 50)}  (not there)")
    print()
    print(f"  {verification.summary()}")
    print()
    print("  Three outcomes, kept apart on purpose. A model that cites nothing has")
    print("  failed differently from one that cites something that does not exist.")

    # ------------------------------------------------------------------ trace
    _rule("6. Backwards, from a quotation to a line in a file")
    for found_trace in trace_quotation("テントの重量は2.4kg", store):
        print(f"  {found_trace.describe()}")
    print()
    print("  And a quotation that is not there is not nearly there:")
    if not trace_quotation("テントの重量は1.9kg", store):
        print("  'テントの重量は1.9kg'  ->  unsupported. No fuzzy match, ever.")

    # ----------------------------------------------------------------- ledger
    usage = SqliteLedger(connection).usage()
    _rule("7. What was sent, and what was used")
    print(f"  {usage.packages} package, {usage.closed} verified, {usage.omissions} left out")
    share = usage.uncited_share
    if share is not None:
        print(
            f"  Of the context sent and checked, {share:.0%} was never cited "
            f"({usage.items_sent - usage.items_cited} of {usage.items_sent} items)."
        )
    print()
    print("  Over months this is the number that says whether any of it helps.")
    print("  It holds identifiers and counts; never a question or a document.")

    if model is not None:
        _ask_a_real_model(store, index, model)

    _rule("The one thing to be clear about")
    print("A supported claim means the quoted text is where the model said it was.")
    print("It does not mean the claim is true. tsumugi does not eliminate")
    print("hallucination and will never say it does — it makes the relationship")
    print("between a sentence and its evidence checkable, which is a smaller")
    print("promise and a keepable one.")

    connection.close()
    return 0


def _ask_a_real_model(store: SqliteDocumentStore, index: FtsIndex, model: str) -> None:
    """The same corpus, the same package, and a model that was not rehearsed.

    Separate from everything above because everything above is deterministic
    and this is not. A failure here is a model that is not running, which is
    worth saying plainly rather than turning the whole demo red.
    """
    _rule(f"8. The same question, put to {model}")
    provider = OllamaProvider(model=model)
    print(f"  sending to {provider.endpoint.describe()}")
    try:
        asked = ask(
            QUESTION,
            store=store,
            index=index,
            cost_model=HeuristicTokenCost(),
            budget=Budget.tokens(400),
            provider=provider,
            version=__version__,
            freshness=remembered_roots(store),
        )
    except TsumugiError as error:
        print(f"  {error}")
        print()
        print("  Everything above still ran. That is the point of the arrangement:")
        print("  selection and verification are local and deterministic, and the")
        print("  model is one step at the end that can be absent.")
        return

    print()
    for claim in asked.verification.claims:
        print(f"  {claim.support.value:<12} {claim.text}")
    print(f"  {asked.verification.summary()}")
    print()
    print("  Nothing above changed because a model was here. The package is")
    print("  byte-for-byte the one stage 3 built — the model was asked for text,")
    print("  never for a decision, so an unsupported claim is this working.")


def _one_line(text: str, width: int = 64) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"
