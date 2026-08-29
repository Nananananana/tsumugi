"""The `mamori` adapter: a redactor, and the ordering it forces.

tsumugi decides what is worth sending. `mamori` decides whether it may leave.
Those are different questions, and merging them produces a tool that is bad at
both — a selector that also redacts starts selecting for redactability.

Composing them naively breaks both, which is
[ADR 0009](../../../docs/adr/0009-restore-before-you-verify.md):

    anchored against    田中太郎との打ち合わせは金曜
    sent as             <PERSON_001>との打ち合わせは金曜
    quoted back         <PERSON_001>との打ち合わせ
    resolved against    田中太郎との打ち合わせは金曜   ->  no match

The model did everything right and every citation reports as unsupported.

**tsumugi holds no mapping.** It stores the scope *identifier* and asks this
port to restore. Holding the mapping would put every real value back into an
index that is already a complete plaintext copy of a corpus, for no benefit.

Optional. Nothing outside ``infrastructure/adapters/`` imports `mamori`, the
test suite runs without it installed, and an architecture test asserts both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...domain.package import Protection

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mamori import PrivacySession

__all__ = ["MamoriRedactor", "protect_package"]


class MamoriRedactor:
    """Satisfies :class:`~tsumugi.ports.redactor.Redactor`, over one session.

    One session is one conversation. The same value keeps its placeholder
    across every call, so a citation quoted in a later turn still restores from
    a value seen in an earlier one — which is the property that makes
    verification through a redactor possible at all.
    """

    def __init__(self, session: PrivacySession, *, version: str = "") -> None:
        self._session = session
        self._version = version or _installed_version()

    @property
    def name(self) -> str:
        return f"mamori@{self._version}"

    @property
    def scope(self) -> str:
        return str(self._session.scope)

    def protect(self, text: str) -> str:
        # mamori raises rather than returning the text unchanged when a
        # detector fails, which is what keeps this from being fail-open. The
        # exception is deliberately not caught.
        return str(self._session.protect(text).protected_text)

    def restore(self, text: str) -> str:
        return str(self._session.restore(text).text)

    def as_protection(self, *, reversible: bool = True) -> Protection:
        """The record a package carries so a verifier can fail loudly.

        ``reversible`` is the caller's statement about the policy in force.
        Under a policy that masks or blocks rather than pseudonymises, values
        are gone for good and the honest answer to "is this citation real?" is
        ``unverifiable`` rather than ``unsupported`` — unknown and false are
        different.
        """
        return Protection(by=self.name, scope=self.scope, reversible=reversible)


def protect_package(rendered: str, redactor: MamoriRedactor) -> str:
    """Protect a rendered package on its way out.

    Deliberately takes the *rendered* text rather than the package. A package
    is evidence anchored to a corpus; protecting it in place would leave items
    whose ``text`` no longer matched their ``text_hash``, and the contract
    refuses to build one of those. What goes to the model is protected; what
    stays here is not.
    """
    return redactor.protect(rendered)


def _installed_version() -> str:
    try:
        import mamori

        return str(getattr(mamori, "__version__", "unknown"))
    except ImportError:  # pragma: no cover - only when it is not installed
        return "unknown"
