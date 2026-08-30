# ADR-0020: A protection is irreversible until it says otherwise

*Accepted 2026-08-30. Decides a question raised from `akashi`, which had the
opposite default.*

## The question

`Protection.reversible` defaulted to `True` here and to `False` in `akashi`.
The field is **required on the wire**, so no emitted document was ever
ambiguous and nothing was broken. But the contract belongs to tsumugi, so the
disagreement is tsumugi's to settle.

## What the field decides

Not a label. It changes what verification does
([ADR 0009](0009-restore-before-you-verify.md)):

| `reversible` | `verify_answer` |
|---|---|
| `True` | restore first, then resolve. A citation that does not resolve is **unsupported** |
| `False` | resolve nothing. Every claim is **unverifiable**, with the reason |

## The decision

**`False`.** A protection is irreversible unless something says otherwise.

The argument is the asymmetry of being wrong, and it is not close:

- **Defaulting `True` when the truth is `False`** reports honest citations as
  *unsupported*. The model quoted a placeholder, nothing can map it back, and
  the report says the model made it up. That is a **false accusation, and it is
  silent** — the output looks exactly like a correctly-caught fabrication.
- **Defaulting `False` when the truth is `True`** reports everything as
  *unverifiable*, with the reason attached. Useless, obvious, and fixed by
  passing the right value.

`unsupported` and `unverifiable` are separate outcomes precisely because
**unknown and false are different** (ADR-0009), and a default that turns
unknown into false spends the distinction the moment nobody sets the field.

This also happens to be the family's rule elsewhere — `iriguchi`'s ADR-0002
fails closed — which is a corroboration rather than the reason.

### And a fact that settles it independently

`mamori`'s own default policy is `Action.BLOCK`, with `SECRET` mapped to
`BLOCK` even under the permissive categories. **A blocked value is gone.** So
tsumugi's `True` default was not merely riskier in principle; it was wrong for
the redactor the port was designed against, in that redactor's default
configuration.

### So the adapter stops defaulting at all

`MamoriRedactor.as_protection()` now **derives** the answer from the session's
policy: a protection is reversible only when no action in force can destroy a
value — every category, every rule and the fallback resolve to `ANONYMIZE` or
`ALLOW`. A caller who knows better can still pass the flag explicitly.

Derivation beats a good default. It is also conservative in the right
direction: a policy that *could* mask something is reported irreversible even
if this particular text contained nothing to mask, because the alternative is
deciding after the fact that the accusation was fine.

## What it costs

**Existing callers who relied on the default get a different verdict.**
Anything constructing `Protection(by=..., scope=...)` without the third
argument now produces packages that verify as `unverifiable` rather than
resolving. That is the intended correction and it will look like a regression
to whoever hits it. Nothing on the wire changes, because the field was always
required there.

**The lenient reader changes too.** `ContextPackage.from_json` filled a missing
`reversible` with `True`; it now fills `False`. Such a document is already
non-conforming — the schema requires the field — so this only decides how
loudly a malformed input fails, and loudly is the answer.

**Derivation reads a sibling's internals.** `MamoriRedactor` now looks at
`session.policy`, which is more of mamori's shape than the adapter needed
before. It is confined to the one file allowed to know mamori exists, and it
degrades to `False` if the attribute is not there — but it is coupling, and a
mamori that renames that attribute breaks it. Fail-closed on the way down, at
least: the fallback is the safe verdict rather than the convenient one.

## Amendment: the same failure through the type system

`akashi` hit this from the other side. Its reader coerced the field with
`bool()`, so the string `"false"` — what a producer that stringified its JSON
sends — read as **true**, and a package that cannot be restored was audited as
one that can.

tsumugi had it too. `verify` branches on `not protection.reversible`, and
`"false"`, `"0"` and `"no"` are all truthy, so all three took the restore path:
honest citations reported as fabrications, quietly. Same failure as the wrong
default, arriving through the type system instead.

`Protection` now refuses anything that is not a real `bool`, including `0` and
`1` — which would land on the right branch by accident. A producer sending
those is not conforming, the schema says boolean, and accepting them teaches
that the field is loosely typed, which is how `"false"` arrives next. A
malformed value now fails loudly at construction rather than choosing the
dangerous branch silently.

## What was not decided

Whether a partially-reversible protection should exist — a package where some
values can be restored and others cannot. It would be more accurate and would
turn one boolean into a per-entity report, and nothing has needed it. Today's
answer is that a package which cannot fully restore is not restorable.
