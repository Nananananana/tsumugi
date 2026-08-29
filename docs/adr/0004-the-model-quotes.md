# 4. The model quotes; tsumugi resolves the offsets

**Status:** accepted

Borrowed, with thanks, from `mamori`'s ADR-0022, which reached the same
conclusion from the opposite direction — a detector reporting spans rather than a
verifier reading them.

## Context

The obvious design for citation is to number the context items, ask the model to
answer with `{"claim": "...", "source": {"item": 3, "start": 120, "end": 190}}`,
and check the offsets.

It does not work. Models cannot count characters. They produce offsets that are
plausible, self-consistent, in the right document, and wrong by a few characters
or a few hundred. `mamori` measured exactly this: asking a model for spans
produced coordinates that had to be discarded, and the feature only started
working once the contract changed to values.

The failure is worse here than a simple error, because it is *silent and
systematic*. An off-by-twelve offset resolves to real text, from the right
document, that says something slightly different. A verifier that trusted it would
mark a claim `supported` and point at the wrong sentence — which is a more
damaging outcome than no verification, because it looks like proof.

## Decision

**The model returns the text it relied on, quoted. tsumugi finds it.**

```json
{"claims": [{"text": "The budget unit is explicit.",
             "citations": ["The budget unit is explicit at the call site"]}]}
```

Resolution is deterministic, in `domain/`, against the text that was actually
sent, with a stated normalization tolerance: whitespace runs collapse, and Unicode
is compared after NFKC. Nothing else is tolerated — no fuzzy matching, no edit
distance, no "close enough".

A quotation that does not resolve makes the claim `unsupported`. It is not
repaired, not approximated, and not quietly dropped.

If a quotation resolves in more than one place, the claim is `supported` and every
match is reported. Ambiguity is information, not an error.

## Consequences

The model is asked for the one thing it is reliably good at — reproducing text it
was just shown — and never for the thing it cannot do.

Verification stays in `domain/` with no model in it, which is what allows the
whole verification path to be tested without one.

The prompt contract gets simpler: "quote what you used" needs no explanation of a
coordinate system, and works with any model, including small local ones.

Fabricated citations become highly visible. A model inventing a quotation produces
text that is not in the corpus, and that is exactly the signal worth having.

## What it costs

Quotations cost output tokens, in proportion to how much is cited. A model that
cites three sentences per claim writes those sentences twice.

A model that paraphrases while claiming to quote produces `unsupported` on a claim
that was, in substance, fine. This is a real false-negative rate and it is
accepted deliberately: erring towards `unsupported` is the safe direction, and the
alternative — fuzzy matching — is a slope with no natural stopping point. Any
future loosening must be measured against a labelled dataset and recorded in an
ADR that supersedes this one.

Very short quotations ("2026") resolve everywhere and carry little evidence.
Reporting every match rather than picking one keeps this visible instead of
inventing precision.
