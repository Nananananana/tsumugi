# 9. Restore before you verify

**Status:** accepted

## Context

tsumugi anchors evidence against original text. `mamori` replaces sensitive values
with placeholders before text leaves the machine. Both are correct, and composed
naively they break each other.

```text
  original      "田中太郎との打ち合わせは金曜"
      │
      ▼  tsumugi anchors against the ORIGINAL
  anchor        offset 0-5 = "田中太郎"
      │
      ▼  mamori protects the RENDERED package
  sent          "<PERSON_001>との打ち合わせは金曜"
      │
      ▼  the model quotes what it was given (ADR 0004)
  citation      "<PERSON_001>との打ち合わせ"
      │
      ▼  tsumugi resolves against the original
  result        ✗ no match
```

The model did everything right. It quoted the text it was shown, exactly. tsumugi
reports the claim `unsupported`.

Every citation touching a redacted value fails this way, and the failure is
indistinguishable from a fabricated quotation. An evidence system that reports
honest citations as unsupported is worse than one with no verification at all,
because it trains its user to ignore the signal — and the signal is the product.

The draft specification described the two libraries as cleanly separated
responsibilities — tsumugi decides what to send, mamori decides whether it may go
— and that framing is right. It is also why the interaction was missed: the
composition is only visible when something real is on both sides, which is the
argument for integration tests that use the actual sibling libraries even though
neither is a dependency.

## Decision

**Restoration happens before verification. Always.**

The pipeline is fixed:

```text
build → render → protect → send → receive → RESTORE → resolve → classify
```

Three things make it enforceable rather than a note in a document:

1. **The package records its protection.** `provenance.protection` names the
   redactor, the scope, and whether it is reversible. `null` means unprotected.
2. **A verifier that sees a non-null `protection` and holds no restorer refuses
   to verify**, raising rather than returning results. Fail closed: a loud failure
   beats a page of false `unsupported`.
3. **Restoration is the redactor's job, through the `Redactor` port.** tsumugi
   does not know what a placeholder is, does not parse one, and does not hold a
   mapping. It asks the port to restore and works with what comes back.

The scope is carried by the caller's `mamori` session, which is where the mapping
already lives. tsumugi stores the scope *identifier* only, never the mapping —
holding it would put every real value into tsumugi's index for no benefit.

## Consequences

Verification through a privacy boundary works, and the composition
`kiseki → tsumugi → mamori → model` is coherent end to end rather than in a
diagram.

The failure mode is loud. A caller who protects a package and then verifies
without a session gets an exception naming the scope, not silent nonsense.

`Redactor` is a port with a `mamori` adapter, so any other redactor composes the
same way, and the core still runs with none.

The property worth testing is stated exactly: *for a package verified through a
redactor, the classification of every claim equals its classification without the
redactor.* Privacy protection must not change what is supported.

**It is now tested against the real `mamori`**, not only against a fake written
to make the point — `tests/test_adapter_mamori.py`, skipped when the sibling is
not installed. A fake redactor can only show that the argument is internally
consistent; the seam exists when something real is on both sides.

## What it costs

An ordering constraint that is invisible in any single component. Someone reading
only tsumugi will not see why `provenance.protection` matters; someone reading
only `mamori` will not see it at all. This ADR is the only place the constraint
lives, which makes it load-bearing documentation — the kind that gets lost.
Encoding the refusal in code, rather than trusting the document, is the
mitigation.

A restored answer holds real values again, in memory, in the verification path. It
is not written anywhere, and the leakage test greps logs and reprs for fixture
values — but the window exists and pretending otherwise would be dishonest.

Verification is impossible when the redaction is irreversible — masked or blocked
values by `mamori`'s policy. Those claims are classified `unverifiable`, a fourth
state, rather than being forced into `unsupported`. Unknown and false are
different, and the schema has to say so.
