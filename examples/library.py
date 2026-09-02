"""Using tsumugi from Python, with the reasons rather than the steps.

    python examples/library.py

Writes only to a temporary directory. Every comment here explains *why* a line
is the way it is — the mechanics are short enough to read without help, and the
reasons are the part that is easy to get wrong.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# One import. This example used to open with fourteen lines reaching into
# `application`, `domain` and `infrastructure` -- which is how the library was
# actually usable, and how nobody should have to use it. Everything below comes
# off the top level now; `tests/test_public_surface.py` pins that list, and
# ADR-0023 says what happens before a name in it moves.
from tsumugi import (
    Budget,
    CharacterCost,
    FtsIndex,
    HeuristicTokenCost,
    SqliteDocumentStore,
    SqliteLedger,
    Support,
    build_context,
    connect,
    ingest_paths,
    parser_for,
    remembered_roots,
    trace_quotation,
    verify_answer,
    walk,
)

NOTES = {
    "design.md": (
        "# 予算\n\n"
        "予算の単位は呼び出し側で明示する。トークンは推定であり、推定器は誤差を申告する。\n"
        "文字数とバイト数は正確に数える。\n"
    ),
    "meeting.md": ("# 打ち合わせ\n\n単位の話は先週決着した。実装は来週から。\n"),
    "unrelated.md": ("# 買い物\n\n牛乳とパン。あとコーヒー豆。\n"),
}


def main() -> int:
    with tempfile.TemporaryDirectory() as workspace:
        root = Path(workspace) / "notes"
        root.mkdir()
        for name, text in NOTES.items():
            # newline="" so the bytes on disk are the bytes the offsets
            # describe. Python rewrites \n as \r\n on Windows otherwise, and
            # one byte per line makes every anchor wrong.
            with (root / name).open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)

        # -- 1. Read the corpus ------------------------------------------
        #
        # The index goes somewhere of your choosing. It is a complete
        # plaintext copy of the corpus, so put it somewhere you would put the
        # corpus -- and not inside a folder that gets synced.
        connection = connect(Path(workspace) / "index.db")
        store = SqliteDocumentStore(connection)
        index = FtsIndex(connection)

        found = walk(root)
        report = ingest_paths(
            found.files, root=root, store=store, index=index, parser_for=parser_for
        )
        print(f"ingested: {report.summary()}")
        # `found.skipped` names anything that was not read, including files
        # that look like credential stores. Worth printing in real code.

        # -- 2. Build a package ------------------------------------------
        #
        # The unit is named at the call site because it is a decision.
        # Characters and bytes are counted; tokens are estimated, and the
        # package carries the estimator's measured error so a caller knows how
        # much margin to leave.
        package = build_context(
            "予算の単位は",
            store=store,
            index=index,
            cost_model=HeuristicTokenCost(),  # or CharacterCost() for an exact unit
            budget=Budget.tokens(200),
            # Without this, a passage from a file edited since it was indexed
            # is offered as current. The index remembers where it read each
            # document, so this needs no argument.
            freshness=remembered_roots(store),
        )

        print(f"\npackage {package.package_id.short()}: {len(package.items)} items")
        for item in package.items:
            print(f"  {item.item_id}  {item.describe()}")

        # -- 3. Read what it left out ------------------------------------
        #
        # The part most people skip and should not. A relevant passage that
        # did not fit is more useful to know about than three that did.
        print()
        print(package.why_not())

        # -- 4. Send it -------------------------------------------------
        #
        # `render()` is the prompt. tsumugi does not send it: there is no
        # outbound path in the library, and if text reaches a service it is
        # because your code put it there.
        prompt = package.render()
        assert "# NOT INCLUDED" in prompt or not package.omissions

        # Optionally protect it first, if `mamori` is installed:
        #
        #     from tsumugi.infrastructure.adapters.mamori import MamoriRedactor
        #     redactor = MamoriRedactor(session)
        #     prompt = redactor.protect(prompt)
        #     package = replace(package, provenance=replace(
        #         package.provenance, protection=redactor.as_protection()))
        #
        # ...and then pass `redactor=` to verify_answer below. Restore before
        # you verify, or every honest citation reports as unsupported.

        # -- 5. Check the answer -----------------------------------------
        #
        # The model quotes; tsumugi resolves the offsets. Asking a model for
        # character positions gets you coordinates that are plausible and
        # wrong.
        answer = {
            "claims": [
                {
                    "text": "単位は呼び出し側が決める。",
                    "citations": ["予算の単位は呼び出し側で明示する"],
                },
                {"text": "推定器の誤差は無視できる。", "citations": ["誤差は無視できる"]},
                {"text": "たぶん妥当な設計だと思う。", "citations": []},
            ]
        }
        verification = verify_answer(json.dumps(answer, ensure_ascii=False), package)

        print()
        for claim in verification.claims:
            print(f"  {claim.support.value:<12} {claim.text}")
        print(f"  {verification.summary()}")

        # A supported claim means the quoted text is where the model said it
        # was. It does not mean the claim is true.
        supported = verification.with_support(Support.SUPPORTED)
        assert len(supported) == 1

        # -- 6. Record it ------------------------------------------------
        #
        # Optional, and holds no text: identifiers, offsets and counts. After
        # a few weeks it answers "how much of what I send is ever used?",
        # which nothing else can.
        ledger = SqliteLedger(connection)
        ledger.open(package)
        ledger.close(verification)
        usage = ledger.usage()
        print(f"\nledger: {usage.packages} package, {usage.closed} verified")

        # -- 7. Go backwards ---------------------------------------------
        #
        # Exact matching. A quotation that is not there is not nearly there.
        print()
        for trace in trace_quotation("予算の単位は呼び出し側で明示する", store):
            print(f"  {trace.describe()}")

        # An exact cost model, for comparison: counted rather than estimated.
        exact = CharacterCost()
        print(f"\nthe first item is exactly {exact.cost(package.items[0].text)} characters")

        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
