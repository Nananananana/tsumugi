"""Closing the loop: a folder, a question, and an answer you can check.

    ollama pull qwen2.5:7b-instruct
    python examples/ask.py                    # or: python examples/ask.py llama3.1:8b

Writes only to a temporary directory, and sends only to a model on this
machine. If ollama is not running this prints why and exits 0 — everything
except the last step works without one, and an example that died at the
import would suggest otherwise.

[`library.py`](library.py) is the same pipeline with the model step left out.
Read that one first if you want to see what tsumugi does on its own; this one
is about the two brackets around the sending, which are the parts that go
wrong quietly.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsumugi.application.ask import ask
from tsumugi.application.ingest import ingest_paths
from tsumugi.domain.budget import Budget
from tsumugi.errors import TsumugiError
from tsumugi.infrastructure.adapters.ollama import DEFAULT_MODEL, OllamaProvider
from tsumugi.infrastructure.cost.heuristic import HeuristicTokenCost
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.freshness import remembered_roots
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.database import connect
from tsumugi.infrastructure.storage.ledger import SqliteLedger
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

NOTES = {
    "gear-2026-06.md": ("# 装備メモ（6月）\n\nテントの重量は3.1kg、二人用。ポールが重い。\n"),
    "gear-2026-08.md": ("# 装備メモ（8月）\n\nテントの重量は2.4kg、二人用。前回より700g軽い。\n"),
    "shopping.md": "# 買い物\n\n牛乳とパン。あとコーヒー豆。\n",
}
QUESTION = "テントの重量は?"


def main(model: str = DEFAULT_MODEL) -> int:
    with tempfile.TemporaryDirectory() as workspace:
        root = Path(workspace) / "notes"
        root.mkdir()
        for name, text in NOTES.items():
            # newline="" so the bytes on disk are the bytes the offsets
            # describe. Python rewrites \n as \r\n on Windows otherwise.
            with (root / name).open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)

        connection = connect(Path(workspace) / "index.db")
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)

        # The provider is constructed before anything is built, and it refuses
        # a host that is not this machine right here -- so a mistyped URL costs
        # you nothing and reveals nothing. `allow_remote=True` overrides it,
        # and has to be typed.
        provider = OllamaProvider(model=model)
        print(f"sending to {provider.name} at {provider.endpoint.describe()}")

        try:
            asked = ask(
                QUESTION,
                store=store,
                index=index,
                cost_model=HeuristicTokenCost(),
                budget=Budget.tokens(2000),
                provider=provider,
                # Optional. Holds identifiers and counts, never text, and
                # after a few weeks answers "how much of what I send is ever
                # used?" -- which nothing else can.
                ledger=SqliteLedger(connection),
                # On by default in the CLI for a reason: without it, a passage
                # from a file edited since it was indexed is offered as
                # current.
                freshness=remembered_roots(store),
            )
        except TsumugiError as error:
            print(f"\n{error}")
            print("\nEverything up to the sending works without a model. See library.py.")
            connection.close()
            return 0

        # -- What came back ----------------------------------------------
        print(f"\n{asked.answer_text()}")

        # -- And whether it is anchored ----------------------------------
        #
        # `supported` means the quoted text is where the model said it was.
        # It does not mean the claim is true. Nothing here eliminates
        # hallucination; it makes the relationship between a sentence and its
        # evidence checkable, which is a smaller promise and a keepable one.
        print()
        for claim in asked.verification.claims:
            print(f"  {claim.support.value:<12} {claim.text}")
            for citation in claim.citations:
                for location in citation.locations:
                    print(f"               -> {location.describe()}")
        print(f"  {asked.verification.summary()}")

        # The corpus contains an older weight as well as the current one. If
        # the model quoted the June note, the citation still resolves -- being
        # fooled by a superseded fact and inventing one are different
        # failures, and only the second is something verification can catch.
        print()
        print(
            f"  package {asked.package.package_id.short()}, "
            f"{len(asked.package.items)} items sent, "
            f"{len(asked.package.omissions)} left out"
        )
        if not asked.trustworthy:
            print("  not every claim is supported by the context it was given.")

        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:2]))
