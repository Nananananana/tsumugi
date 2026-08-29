"""The whole loop, in one call: build, send, check, record.

Everything here already existed separately. What this adds is the *order*, and
the order is the design:

    build → (protect) → send → (restore) → verify → record

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
from .build_context import build_context
from .verify import verify_answer

__all__ = ["Asked", "ask"]

#: Appended when a model is going to answer, because the answer has to be
#: machine-readable for verification to run at all. Kept here rather than in
#: the package's instructions: a package is not built for one consumer, and a
#: reader pasting it into a chat window does not need this.
#:
#: Longer than it looks like it needs to be, and every extra line was earned.
#: The first version said "citations": ["text quoted exactly from the
#: context"], and qwen2.5:14b answered a Japanese question perfectly and cited
#: ``notes/持ち物リスト.md (持ち物リスト（控え）)`` -- the header line above the
#: passage. Which is what "citation" means everywhere else: name the source.
#: Every claim reported unsupported, and the answer was right.
#:
#: So the contract now says what a citation is *not*, and shows the shape of
#: the thing it must not be. Verification caught it, which is the arrangement
#: working -- but a checker that always fails is a checker nobody keeps.
_OUTPUT_CONTRACT = """
Answer as JSON, and nothing else:

{"claims": [{"text": "one statement", "citations": ["text copied from a passage"]}]}

A citation is a **span of text copied out of a passage below**, character for
character. It is not a filename, not a heading, and not a `[c1]` label. Copy
from the lines *underneath* a header, never the header itself:

    [c1] notes/gear.md (Gear)        <- never cite this line
    The tent weighs 2.4kg.           <- cite from this one, e.g. "weighs 2.4kg"

Do not report character positions. Do not paraphrase inside a citation; a
citation that is nearly right is wrong. If the context does not answer the
question, say so in a claim with no citations.
"""


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
        """Every claim supported.

        Named carefully. It means every quotation was where the model said it
        was -- not that the answer is right.
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
    version: str = "",
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
    )

    if redactor is not None:
        # Recorded on the package; applied to the rendered text below.
        # Redacting the package itself would leave items whose text no longer
        # matched their text_hash, and the contract refuses to build one.
        package = replace(
            package,
            provenance=replace(package.provenance, protection=_protection_of(redactor)),
        )

    if ledger is not None:
        ledger.open(package)

    prompt = package.render() + _OUTPUT_CONTRACT
    if redactor is not None:
        prompt = redactor.protect(prompt)

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
