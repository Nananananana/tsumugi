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

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, ClassVar

from ...domain.package import Protection
from ...errors import ConfigurationError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mamori import PrivacySession

__all__ = ["MamoriRedactor", "open_session", "protect_package"]


@contextmanager
def open_session(scope: str | None = None) -> Iterator[MamoriRedactor]:
    """A redactor for the length of one conversation.

    Here rather than at the call site because ``import mamori`` belongs in this
    file and nowhere else -- an architecture test asserts it, and it caught the
    first attempt to put this in the CLI.

    Raises :class:`~tsumugi.errors.ConfigurationError` when mamori is missing.
    A caller who asked for protection and silently got none would have been
    told the opposite of what happened.
    """
    try:
        import mamori as _mamori
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise ConfigurationError(
            "protection needs mamori, which is not installed. `pip install mamori`, "
            "or go without -- but then the prompt goes as it is."
        ) from error

    with _mamori.PrivacySession(scope=scope) as session:
        yield MamoriRedactor(session)


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

    #: Whether everything protected in this session so far can be restored.
    #: ``None`` until something has been. mamori answers this per protection --
    #: it knows which entities it anonymised and which it masked -- so the
    #: honest source is what it did, not what its policy might have done.
    _observed: bool | None = None

    def protect(self, text: str) -> str:
        # mamori raises rather than returning the text unchanged when a
        # detector fails, which is what keeps this from being fail-open. The
        # exception is deliberately not caught.
        result = self._session.protect(text)
        # Conjunction across the session: one masked value makes the whole
        # package unrestorable, and a later call that happened to mask nothing
        # does not undo that.
        observed = bool(getattr(result, "reversible", False))
        self._observed = observed if self._observed is None else (self._observed and observed)
        return str(result.protected_text)

    def restore(self, text: str) -> str:
        return str(self._session.restore(text).text)

    #: Actions that leave a value recoverable. Everything else destroys it.
    _RESTORABLE: ClassVar[frozenset[str]] = frozenset({"anonymize", "allow"})

    def policy_is_reversible(self) -> bool:
        """Whether *every* action this session could take leaves a way back.

        The estimate used before anything has been protected. It is
        pessimistic by construction -- mamori's own ``PrivacyPolicy`` falls
        back to ``Action.BLOCK`` and maps ``SECRET`` to it, so almost any real
        policy answers ``False`` here even when the text at hand holds nothing
        to mask.

        That is why it is the *fallback* and not the answer: what mamori
        actually did is better evidence than what it might have done, and it
        reports that per protection.

        Fails closed if mamori's shape moves: no policy, no claim of
        reversibility.
        """
        policy = getattr(self._session, "policy", None)
        if policy is None:  # pragma: no cover - depends on mamori's shape
            return False

        actions = [getattr(policy, "default_action", None)]
        actions.extend((getattr(policy, "category_defaults", None) or {}).values())
        for rule in getattr(policy, "rules", ()) or ():
            actions.append(getattr(rule, "action", None))

        return all(
            action is not None and str(getattr(action, "value", action)) in self._RESTORABLE
            for action in actions
        )

    def as_protection(self, *, reversible: bool | None = None) -> Protection:
        """The record a package carries so a verifier can fail loudly.

        Whether the protection can be undone decides what verification *does*:
        a reversible one restores and then resolves, so an unresolved citation
        is ``unsupported``; an irreversible one resolves nothing and every
        claim is ``unverifiable``. Unknown and false are different, and
        ADR-0009 exists to keep them apart.

        So it is observed rather than defaulted, in three steps, each falling
        back to something safer: what a caller states, then what mamori
        reported about the text this session actually protected, then what its
        policy could do -- which is nearly always ``False``.

        **Call it after protecting**, or the answer is the pessimistic
        estimate. `ask` does (ADR-0020).
        """
        if reversible is not None:
            derived = reversible
        elif self._observed is not None:
            derived = self._observed
        else:
            derived = self.policy_is_reversible()
        return Protection(by=self.name, scope=self.scope, reversible=derived)


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
