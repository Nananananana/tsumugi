"""Checking an answer against the package it was built from.

Two things happen here and the order between them is the whole of ADR-0009.

**Restore, then verify.** If the package passed through a redactor, the model
was shown ``<PERSON_001>`` and quoted ``<PERSON_001>``, while the anchors point
at 田中太郎 in the original document. Resolving the citation without restoring
it first fails for every honest citation, and the failure looks exactly like a
fabricated quotation. An evidence system that reports true citations as
unsupported is worse than one with no verification at all, because it teaches
its user to ignore the signal.

So a verifier that sees ``provenance.protection`` and holds no restorer
**refuses**, loudly, naming the scope it would need. A loud failure beats a
page of false ``unsupported``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from ..domain.claim import VerificationReport, verify_claims
from ..domain.package import ContextPackage
from ..errors import TsumugiError
from ..ports.redactor import Redactor

__all__ = ["AnswerFormatError", "ProtectedPackageError", "parse_answer", "verify_answer"]


class AnswerFormatError(TsumugiError):
    """The answer is not in a shape this can read."""


def _unfenced(payload: str) -> str:
    """The contents of a lone markdown code fence, or the text unchanged.

    Deliberately narrow. The whole payload has to be one fenced block: a
    fence buried in prose is prose, and treating it otherwise would be the
    "find the JSON somewhere in there" behaviour this refuses to have.
    """
    text = payload.strip()
    if not text.startswith("```") or not text.endswith("```"):
        return payload
    lines = text.splitlines()
    if len(lines) < 2:
        return payload
    # The opening line is ``` or ```json; the closing line is ```. Anything
    # else on the opening line is a language tag and is ignored.
    return "\n".join(lines[1:-1])


class ProtectedPackageError(TsumugiError):
    """A protected package was handed to a verifier with no way to restore it.

    Raised rather than returning a report of ``unsupported`` claims. See
    ADR-0009: the quiet version of this failure is the damaging one.
    """


def parse_answer(payload: str) -> list[tuple[str, list[str]]]:
    """Read a model's answer into ``(claim, quotations)`` pairs.

    The shape is the one the package's ``output_schema`` asks for::

        {"claims": [{"text": "...", "citations": ["quoted text", ...]}]}

    A bare list of strings is also accepted and treated as uncited claims,
    because that is what a model does when it ignores the schema, and telling
    the caller "every claim is uncited" is more useful than refusing to parse.

    **One tolerance: a markdown code fence.** Models wrap JSON in ```` ```json ````
    constantly, and it is asked for in the instructions not to. Unwrapping a
    fence is reading a syntax, not guessing at an intent -- the same class of
    tolerance as NFKC in :mod:`tsumugi.domain.matching`, and it stops exactly
    where that one does.

    Nothing else is inferred. Hunting for the first ``{`` in a page of prose,
    or extracting quotations by looking for quote marks, would be guessing at
    what the model meant -- and a verifier that guesses is not a verifier. An
    answer that is not in the requested shape is a *result*, and reported as
    one.
    """
    try:
        data: Any = json.loads(_unfenced(payload))
    except json.JSONDecodeError as error:
        raise AnswerFormatError(
            f"the answer is not JSON ({error}). Expected "
            '{"claims": [{"text": "...", "citations": ["..."]}]}'
        ) from error

    if isinstance(data, dict):
        raw = data.get("claims")
        if raw is None:
            raise AnswerFormatError("the answer has no 'claims' key")
    elif isinstance(data, list):
        raw = data
    else:
        raise AnswerFormatError(f"expected an object or a list, got {type(data).__name__}")

    claims: list[tuple[str, list[str]]] = []
    for position, entry in enumerate(raw, start=1):
        if isinstance(entry, str):
            claims.append((entry, []))
            continue
        if not isinstance(entry, dict):
            raise AnswerFormatError(f"claim {position} is a {type(entry).__name__}, not an object")
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AnswerFormatError(f"claim {position} has no text")
        citations = entry.get("citations") or []
        if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
            raise AnswerFormatError(f"claim {position} has citations that are not strings")
        claims.append((text, list(citations)))

    return claims


def verify_answer(
    answer: str | Sequence[tuple[str, Sequence[str]]],
    package: ContextPackage,
    *,
    redactor: Redactor | None = None,
) -> VerificationReport:
    """Resolve every citation in ``answer`` against ``package``.

    ``redactor`` is required when the package records a protection, and is
    ignored otherwise.
    """
    claims = parse_answer(answer) if isinstance(answer, str) else [(t, list(q)) for t, q in answer]

    protection = package.provenance.protection
    if protection is not None:
        if not protection.reversible:
            # Nothing can restore a masked or blocked value. The claims are
            # unverifiable, which is a different answer from unsupported --
            # unknown and false are not the same, and the schema says so.
            return verify_claims(
                claims,
                package.items,
                package_id=str(package.package_id),
                unverifiable_because=(
                    f"{protection.by} redacted this package irreversibly, so the text "
                    f"the model was shown cannot be mapped back to the corpus"
                ),
            )
        if redactor is None:
            raise ProtectedPackageError(
                f"this package was protected by {protection.by} in scope "
                f"{protection.scope!r} and no restorer was given. Verifying it as-is "
                f"would report every honest citation as unsupported, so it is refused "
                f"instead. Pass the session that protected it (ADR-0009)."
            )
        # Restore, THEN verify. The one ordering constraint that is invisible
        # from inside either library on its own.
        claims = [(text, [redactor.restore(q) for q in quotations]) for text, quotations in claims]

    return verify_claims(claims, package.items, package_id=str(package.package_id))
