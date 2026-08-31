"""ADR-0009 against the real thing.

Every other test of restore-before-verify uses a fake redactor written to make
the point, which means it tests that the argument is internally consistent. The
seam only exists when something real is on both sides, and this is the file
that puts it there.

Skipped when `mamori` is not installed, because the core works without it and
that is a hard constraint rather than a preference.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

mamori = pytest.importorskip(
    "mamori", reason="the sibling adapters are optional; the core runs without them"
)

from tsumugi.application.verify import (  # noqa: E402
    ProtectedPackageError,
    verify_answer,
)
from tsumugi.domain.anchor import Anchor  # noqa: E402
from tsumugi.domain.budget import Budget  # noqa: E402
from tsumugi.domain.claim import Support  # noqa: E402
from tsumugi.domain.package import (  # noqa: E402
    BudgetReport,
    ContextPackage,
    PackageProvenance,
)
from tsumugi.domain.selection import ContextItem  # noqa: E402
from tsumugi.domain.span import Span  # noqa: E402
from tsumugi.infrastructure.adapters.mamori import (  # noqa: E402
    MamoriRedactor,
    protect_package,
)
from tsumugi.ports.llm import Endpoint  # noqa: E402
from tsumugi.ports.redactor import Redactor  # noqa: E402

from .helpers import build_document  # noqa: E402

#: Invented, as everything committed here must be. It carries the two shapes
#: mamori is surest about -- a Japanese name with an honorific, and an address.
TEXT = (
    "# 打ち合わせ\n\n"
    "田中太郎さんとの打ち合わせは金曜の午後。連絡は tanaka@example.com まで。\n\n"
    "予算の単位は呼び出し側で明示する。トークンは推定である。\n"
)
DOCUMENT = build_document("notes/meeting.md", TEXT)

#: The passage the package carries: the whole sentence, starting at the name.
#: Computed rather than written down, because a hand-counted offset that clipped
#: the name made a test fail for a reason that had nothing to do with its
#: subject.
BODY = Span(TEXT.index("田中太郎"), TEXT.index("まで。") + 3)


@pytest.fixture
def session() -> object:
    with mamori.PrivacySession() as opened:
        yield opened


@pytest.fixture
def redactor(session: object) -> MamoriRedactor:
    return MamoriRedactor(session)  # type: ignore[arg-type]


def a_package(**overrides: object) -> ContextPackage:
    items = (
        ContextItem(
            item_id="itm_001",
            text=BODY.slice(TEXT),
            anchor=Anchor.into(DOCUMENT, BODY),
            source_path=DOCUMENT.source_path,
            cost=len(BODY),
        ),
    )
    defaults: dict[str, object] = {
        "query": "打ち合わせはいつか",
        "items": items,
        "omissions": (),
        "budget": BudgetReport(Budget.characters(1000), len(BODY), "characters@1"),
        "provenance": PackageProvenance(tsumugi_version="test"),
    }
    defaults.update(overrides)
    return ContextPackage(**defaults)  # type: ignore[arg-type]


class TestTheAdapter:
    def test_it_satisfies_the_port(self, redactor: MamoriRedactor) -> None:
        assert isinstance(redactor, Redactor)

    def test_it_names_the_version_it_is_talking_to(self, redactor: MamoriRedactor) -> None:
        assert redactor.name.startswith("mamori@")
        assert redactor.name != "mamori@unknown"

    def test_it_carries_the_scope_but_not_the_mapping(self, redactor: MamoriRedactor) -> None:
        # tsumugi stores the identifier only. Holding the mapping would put
        # every real value back into an index that is already a complete
        # plaintext copy of a corpus.
        assert redactor.scope
        assert "田中" not in repr(redactor.as_protection(reversible=True))

    def test_protecting_removes_the_values(self, redactor: MamoriRedactor) -> None:
        protected = redactor.protect(TEXT)
        assert "田中太郎" not in protected
        assert "tanaka@example.com" not in protected

    def test_and_restoring_puts_them_back(self, redactor: MamoriRedactor) -> None:
        assert redactor.restore(redactor.protect(TEXT)) == TEXT


class TestRestoreBeforeVerify:
    """The property ADR-0009 exists for, against the real redactor."""

    def test_a_protected_package_refuses_to_verify_without_a_restorer(
        self, redactor: MamoriRedactor
    ) -> None:
        # Verifying as-is would report every honest citation as unsupported,
        # and the failure would look exactly like a hallucination.
        package = a_package(
            provenance=PackageProvenance(
                tsumugi_version="test", protection=redactor.as_protection(reversible=True)
            )
        )
        with pytest.raises(ProtectedPackageError, match=redactor.scope):
            verify_answer([("x", ["anything"])], package)

    def test_a_citation_of_redacted_text_resolves_after_restoring(
        self, redactor: MamoriRedactor
    ) -> None:
        package = a_package(
            provenance=PackageProvenance(
                tsumugi_version="test", protection=redactor.as_protection(reversible=True)
            )
        )
        # What the model was actually shown, and what it would quote back.
        shown = protect_package(package.items[0].text, redactor)
        quotation = shown[:20]
        assert "田中" not in quotation

        report = verify_answer([("a claim", [quotation])], package, redactor=redactor)
        assert report.claims[0].support is Support.SUPPORTED

    def test_and_it_anchors_back_into_the_real_document(self, redactor: MamoriRedactor) -> None:
        # The whole point: a citation of redacted text becomes an anchor that
        # `trace` can follow to a line in a file holding the real value.
        package = a_package(
            provenance=PackageProvenance(
                tsumugi_version="test", protection=redactor.as_protection(reversible=True)
            )
        )
        shown = protect_package(package.items[0].text, redactor)
        report = verify_answer([("x", [shown[:20]])], package, redactor=redactor)

        location = report.claims[0].citations[0].locations[0]
        assert location.anchor.span.slice(DOCUMENT.content) in DOCUMENT.content
        assert "田中太郎" in DOCUMENT.content

    def test_protection_does_not_change_a_single_classification(
        self, redactor: MamoriRedactor
    ) -> None:
        # The property that matters, and the one the fake redactor could only
        # gesture at: privacy protection must not change what is supported.
        answer = [
            ("real", [BODY.slice(TEXT)[:20]]),
            ("invented", ["この文はどこにも存在しない"]),
            ("bare", []),
        ]
        plain = verify_answer(answer, a_package())

        protected_package = a_package(
            provenance=PackageProvenance(
                tsumugi_version="test", protection=redactor.as_protection(reversible=True)
            )
        )
        through = verify_answer(
            [(text, [redactor.protect(q) for q in quotes]) for text, quotes in answer],
            protected_package,
            redactor=redactor,
        )

        assert [c.support for c in plain.claims] == [c.support for c in through.claims]
        assert plain.counts == through.counts

    def test_an_irreversible_protection_is_unverifiable_not_unsupported(
        self, redactor: MamoriRedactor
    ) -> None:
        # Under a policy that masks or blocks, the values are gone for good.
        # Unknown and false are different, and reporting the second would
        # teach a user to ignore the signal.
        package = a_package(
            provenance=PackageProvenance(
                tsumugi_version="test", protection=redactor.as_protection(reversible=False)
            )
        )
        report = verify_answer([("x", [BODY.slice(TEXT)[:20]])], package)
        assert report.claims[0].support is Support.UNVERIFIABLE


class TestTheBoundaryHolds:
    def test_the_package_itself_is_never_redacted_in_place(self, redactor: MamoriRedactor) -> None:
        # A package is evidence anchored to a corpus. Protecting it in place
        # would leave items whose text no longer matched their text_hash, and
        # the contract refuses to build one of those. What goes to the model is
        # protected; what stays here is not.
        package = a_package()
        protect_package(package.render(), redactor)
        assert "田中太郎" in package.items[0].text

    def test_a_package_records_which_redactor_and_scope(self, redactor: MamoriRedactor) -> None:
        import json

        package = a_package(
            provenance=PackageProvenance(
                tsumugi_version="test", protection=redactor.as_protection(reversible=True)
            )
        )
        recorded = json.loads(package.to_json())["provenance"]["protection"]
        assert recorded["by"].startswith("mamori@")
        assert recorded["scope"] == redactor.scope
        assert recorded["reversible"] is True


class TestTheWholeLoopThroughProtection:
    """`ask` with a redactor, against real mamori and a scripted model.

    The model is scripted rather than real: what is being tested is the
    bracketing, and a real model would make the test slow, flaky and dependent
    on a machine having pulled something. What is *not* faked is the redactor,
    because ADR-0009 is a claim about a real placeholder scheme surviving a
    round trip, and a fake one is written by whoever wants the claim to hold.
    """

    def _corpus(self, tmp_path: Path) -> tuple[object, object, object]:
        from tsumugi.application.ingest import ingest_paths
        from tsumugi.infrastructure.index.fts import FtsIndex
        from tsumugi.infrastructure.parsers import parser_for
        from tsumugi.infrastructure.storage.database import connect
        from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

        root = tmp_path / "notes"
        root.mkdir()
        with (root / "meeting.md").open("w", encoding="utf-8", newline="") as handle:
            handle.write(TEXT)
        connection = connect(tmp_path / "index.db")
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        ingest_paths(
            sorted(root.rglob("*.md")),
            root=root,
            store=store,
            index=index,
            parser_for=parser_for,
        )
        return store, index, connection

    def test_a_citation_quoting_a_placeholder_resolves_to_the_real_text(
        self, tmp_path: Path, session: object
    ) -> None:
        import json

        from tsumugi.application.ask import ask
        from tsumugi.infrastructure.cost.heuristic import CharacterCost

        store, index, connection = self._corpus(tmp_path)
        redactor = MamoriRedactor(session)  # type: ignore[arg-type]

        class Quoting:
            """Answers with a sentence from whatever prompt it is given."""

            def __init__(self) -> None:
                self.prompt = ""

            @property
            def name(self) -> str:
                return "quoting/1"

            @property
            def endpoint(self) -> Endpoint:
                return Endpoint(url="memory://quoting", is_local=True)

            def generate(self, prompt: str) -> str:
                self.prompt = prompt
                # The line of context, protected, exactly as the model sees it.
                line = next(
                    text
                    for text in prompt.splitlines()
                    if "打ち合わせは金曜" in text and not text.startswith("[")
                )
                return json.dumps(
                    {"claims": [{"text": "金曜である。", "citations": [line.strip()]}]},
                    ensure_ascii=False,
                )

        provider = Quoting()
        asked = ask(
            "打ち合わせは金曜",
            store=store,  # type: ignore[arg-type]
            index=index,  # type: ignore[arg-type]
            cost_model=CharacterCost(),
            budget=Budget.characters(4000),
            provider=provider,
            redactor=redactor,
        )

        # The prompt really was protected: the name is not in it.
        assert "田中太郎" not in provider.prompt
        # And the citation, quoted as a placeholder, resolved to the real text.
        assert asked.trustworthy, asked.verification.summary()
        assert asked.verification.claims[0].support is Support.SUPPORTED
        connection.close()  # type: ignore[attr-defined]

    def test_the_package_records_the_scope_and_never_the_mapping(
        self, tmp_path: Path, session: object
    ) -> None:
        # tsumugi holds the scope identifier so a verifier can say *which*
        # session would be needed. Holding the mapping would put every real
        # value back into an index that is already a plaintext copy.
        from tsumugi.application.ask import ask
        from tsumugi.infrastructure.cost.heuristic import CharacterCost

        store, index, connection = self._corpus(tmp_path)
        redactor = MamoriRedactor(session)  # type: ignore[arg-type]

        class Silent:
            @property
            def name(self) -> str:
                return "silent/1"

            @property
            def endpoint(self) -> Endpoint:
                return Endpoint(url="memory://silent", is_local=True)

            def generate(self, prompt: str) -> str:
                return '{"claims": [{"text": "no comment", "citations": []}]}'

        asked = ask(
            "打ち合わせは金曜",
            store=store,  # type: ignore[arg-type]
            index=index,  # type: ignore[arg-type]
            cost_model=CharacterCost(),
            budget=Budget.characters(4000),
            provider=Silent(),
            redactor=redactor,
        )
        protection = asked.package.provenance.protection
        assert protection is not None
        assert protection.scope == redactor.scope
        serialised = asked.package.to_json()
        # An item is the sentence around the match, so the address in the next
        # sentence may or may not be carried. The name is in the same sentence
        # as the fact, and it is the point: the package holds the real text and
        # is never redacted.
        assert "田中太郎" in serialised
        connection.close()  # type: ignore[attr-defined]


class TestReversibilityIsObservedNotAssumed:
    """ADR-0020: mamori knows, so tsumugi asks instead of defaulting.

    The default used to be `True`, which for this redactor in its own default
    configuration was simply false -- `PrivacyPolicy` falls back to
    `Action.BLOCK`, and a blocked value is gone.
    """

    def test_a_fresh_redactor_has_not_seen_anything_yet(self, session: object) -> None:
        # Nothing protected, so nothing observed, so the pessimistic estimate
        # from the policy. It is almost always False, which is the point.
        redactor = MamoriRedactor(session)  # type: ignore[arg-type]
        assert redactor.as_protection().reversible == redactor.policy_is_reversible()

    def test_it_reports_what_mamori_actually_did(self, session: object) -> None:
        # mamori answers this per protection: it knows which entities it
        # anonymised and which it masked. That is better evidence than what
        # its policy might have done to some other text.
        redactor = MamoriRedactor(session)  # type: ignore[arg-type]
        redactor.protect(TEXT)
        observed = redactor.as_protection().reversible
        assert isinstance(observed, bool)
        # And it matches what the session reported for that same text.
        assert observed == bool(session.protect(TEXT).reversible)  # type: ignore[attr-defined]

    def test_a_caller_who_knows_still_wins(self, session: object) -> None:
        redactor = MamoriRedactor(session)  # type: ignore[arg-type]
        redactor.protect(TEXT)
        assert redactor.as_protection(reversible=False).reversible is False

    def test_one_irreversible_protection_taints_the_session(self, session: object) -> None:
        # A conjunction, not a last-write-wins. One masked value makes the
        # package unrestorable, and a later call that masked nothing does not
        # undo that.
        redactor = MamoriRedactor(session)  # type: ignore[arg-type]
        redactor._observed = False
        redactor.protect("nothing sensitive here at all")
        assert redactor.as_protection().reversible is False


class TestSurrogateModeRoundTrips:
    """ADR-0009's property under `mixed` mode, where nothing looks like a token.

    `mamori.protection-scope/1` names the failure a consumer of a protection
    record has to avoid: reading `placeholders` and concluding the
    substitutions are fully enumerated. In `surrogate` and `mixed` modes a
    value is replaced by a *plausible* one -- `田中太郎` becomes `山田一郎`,
    not `<PERSON_001>` -- so a placeholder list under-reports, and an
    implementation that restored by scanning for `<...>` would silently leave
    a real-looking fake in the text it called restored.

    tsumugi is safe from this by construction rather than by care: it never
    reads the list and never looks for a token, it hands the text back to the
    session. These tests are what stop that becoming an accident.
    """

    def _mixed_session(self) -> Any:
        return mamori.PrivacySession(surrogate_types=frozenset({"PERSON"}))

    def test_a_surrogate_is_not_a_token(self) -> None:
        with self._mixed_session() as session:
            protected = MamoriRedactor(session).protect(TEXT)
        # The name is gone and nothing marks where it was.
        assert "田中太郎" not in protected
        assert "<PERSON" not in protected

    def test_restoring_still_returns_the_original(self) -> None:
        with self._mixed_session() as session:
            redactor = MamoriRedactor(session)
            assert redactor.restore(redactor.protect(TEXT)) == TEXT

    def test_a_quoted_fragment_restores(self) -> None:
        # What a model actually returns: part of what it was shown, not all
        # of it. This is the shape ADR-0009 is about.
        with self._mixed_session() as session:
            redactor = MamoriRedactor(session)
            protected = redactor.protect(TEXT)
            fragment = protected[: protected.index("さん") + 2]
            assert "田中太郎" in redactor.restore(fragment)

    def test_the_adapter_never_looks_for_a_placeholder(self) -> None:
        # The property that makes the three above hold for a reason rather
        # than by luck. If restoration ever starts parsing tokens, it acquires
        # the failure mode this class exists to document.
        import inspect

        from tsumugi.infrastructure.adapters import mamori as adapter

        source = inspect.getsource(adapter.MamoriRedactor.restore)
        assert "<" not in source and "placeholder" not in source.lower()
