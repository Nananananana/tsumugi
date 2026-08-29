"""ADR-0009 against the real thing.

Every other test of restore-before-verify uses a fake redactor written to make
the point, which means it tests that the argument is internally consistent. The
seam only exists when something real is on both sides, and this is the file
that puts it there.

Skipped when `mamori` is not installed, because the core works without it and
that is a hard constraint rather than a preference.
"""

from __future__ import annotations

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
        assert "田中" not in repr(redactor.as_protection())

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
                tsumugi_version="test", protection=redactor.as_protection()
            )
        )
        with pytest.raises(ProtectedPackageError, match=redactor.scope):
            verify_answer([("x", ["anything"])], package)

    def test_a_citation_of_redacted_text_resolves_after_restoring(
        self, redactor: MamoriRedactor
    ) -> None:
        package = a_package(
            provenance=PackageProvenance(
                tsumugi_version="test", protection=redactor.as_protection()
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
                tsumugi_version="test", protection=redactor.as_protection()
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
                tsumugi_version="test", protection=redactor.as_protection()
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
                tsumugi_version="test", protection=redactor.as_protection()
            )
        )
        recorded = json.loads(package.to_json())["provenance"]["protection"]
        assert recorded["by"].startswith("mamori@")
        assert recorded["scope"] == redactor.scope
        assert recorded["reversible"] is True
