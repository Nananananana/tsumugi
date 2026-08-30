"""The whole loop, in one call: build, send, check, record.

Everything here already existed separately. What this adds is the *order*, and
the order is the design:

    build → (protect) → send → (restore) → verify → record

**The prompt is the package.** ``asked.prompt`` is ``asked.package.render()``,
and with a redactor it is that text with values replaced -- nothing is ever
added. It was not always: for a while this appended an output contract on the
way out, which meant the package -- the thing the ledger records, `--json`
publishes, and a reader is invited to inspect before anything is sent --
described slightly less than what went. The instruction set is a parameter now
instead ([instructions.py](instructions.py)).

Two of those brackets are the ones that go wrong when somebody wires this up
themselves. Protection has to happen on the rendered text and never on the
package, or items stop matching their hashes. Restoration has to happen before
verification, or every honest citation reports as unsupported
([ADR 0009](../../docs/adr/0009-restore-before-you-verify.md)).

A model appears in exactly one step. It does not rank, does not choose what is
sent, and does not resolve a citation -- so the worst it can do is write a
claim that verification then reports as unsupported, which is the system
working rather than failing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..domain.budget import Budget
from ..domain.claim import VerificationReport
from ..domain.package import ContextPackage, Protection
from ..ports.cost import CostModel
from ..ports.freshness import FreshnessCheck
from ..ports.index import Index
from ..ports.ledger import LedgerStore
from ..ports.llm import LLMProvider
from ..ports.redactor import Redactor
from ..ports.store import DocumentStore
from ..version import __version__
from .build_context import build_context
from .instructions import ANSWER_SCHEMA, ANSWERING
from .verify import verify_answer

__all__ = ["Asked", "ask"]


@dataclass(frozen=True, slots=True)
class Asked:
    """What happened: what was sent, what came back, and what checked out."""

    package: ContextPackage
    prompt: str
    answer: str
    verification: VerificationReport
    provider: str

    def answer_text(self) -> str:
        """The claims as prose.

        The model answered in JSON because verification needs to know which
        quotation belongs to which statement. Nobody wants to read JSON, and
        the statements in order are the answer.
        """
        return "\n".join(claim.text for claim in self.verification.claims)

    @property
    def trustworthy(self) -> bool:
        """At least one claim, and every quotation where the model said it was.

        Named carefully, twice over. It does not mean the answer is right; and
        an answer that asserts nothing is not trustworthy either, because
        ``all()`` over an empty list is true and a model that says nothing
        would otherwise pass.
        """
        return self.verification.clean


def ask(
    question: str,
    *,
    store: DocumentStore,
    index: Index,
    cost_model: CostModel,
    budget: Budget,
    provider: LLMProvider,
    redactor: Redactor | None = None,
    ledger: LedgerStore | None = None,
    freshness: FreshnessCheck | None = None,
    candidate_limit: int = 50,
    minimum_score: float = 0.0,
    version: str = __version__,
) -> Asked:
    """Build context for ``question``, ask ``provider``, and check the answer."""
    package = build_context(
        question,
        store=store,
        index=index,
        cost_model=cost_model,
        budget=budget,
        candidate_limit=candidate_limit,
        minimum_score=minimum_score,
        version=version,
        freshness=freshness,
        # A machine is going to check this answer, so it has to be machine
        # readable. Passed to the builder rather than appended afterwards, so
        # the package and the prompt cannot disagree.
        instructions=ANSWERING,
        output_schema=ANSWER_SCHEMA,
    )

    prompt = package.render()
    if redactor is not None:
        # Protect first, then record. A redactor knows whether a value can be
        # restored only after it has seen the text: mamori reports it per
        # protection, and asking beforehand gets a pessimistic guess from its
        # policy instead (ADR-0020). The record is only useful if it is true.
        #
        # The rendered text, never the package. Redacting the package would
        # leave items whose text no longer matched their text_hash, and the
        # contract refuses to build one.
        prompt = redactor.protect(prompt)
        package = replace(
            package,
            provenance=replace(package.provenance, protection=_protection_of(redactor)),
        )

    if ledger is not None:
        # After the protection is recorded, so the ledger holds the package
        # that was actually sent.
        ledger.open(package)

    answer = provider.generate(prompt)

    # verify_answer restores first when the package records a protection, and
    # refuses loudly if it cannot.
    verification = verify_answer(answer, package, redactor=redactor)

    if ledger is not None:
        ledger.close(verification)

    return Asked(
        package=package,
        prompt=prompt,
        answer=answer,
        verification=verification,
        provider=provider.name,
    )


def _protection_of(redactor: Redactor) -> Protection:
    """The record a package carries so a verifier can fail loudly.

    Asked of the redactor when it can answer, because only the redactor knows
    whether its own policy is reversible -- a masked value is gone for good,
    and a package that claimed otherwise would send a verifier looking for
    something that cannot be found.
    """
    as_protection = getattr(redactor, "as_protection", None)
    if callable(as_protection):
        found = as_protection()
        if isinstance(found, Protection):
            return found
    return Protection(by=redactor.name, scope=redactor.scope)
