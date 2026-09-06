# ADR-0027: A package records what tsumugi built, not what happened to it afterwards

*Accepted 2026-09-06. Answers a question from `sora`, the orchestration layer,
about `provenance.protection` when protection happens outside tsumugi.*

## The question

`ask()` accepts a `Redactor`. When one is given, the rendered text is protected
before it is sent, and the package's `provenance.protection` records that it
was ([ADR-0021](0021-the-ledger-records-that-a-package-was-protected-not-how.md)).
`package_id` is computed over the package, including that field.

`sora` does not call `ask`. It holds the model and the device, calls `context`
and `render`, and then runs the prompt through `mamori serve` — a loopback HTTP
process it never imports. Only *after* the answer comes back does it hold
mamori's `protection-scope/1` record.

If sora writes that record into `provenance.protection`, `package_id` stops
describing the package. If it does not, the package says nothing was protected
when something was. Sora asked which is right, and whether tsumugi should offer
a path that attaches protection afterwards and recomputes the id.

## The decision

**A package is tsumugi's record of what tsumugi built. Nothing writes into it
afterwards, and there is no recomputation path.** A consumer that protects the
rendered prompt keeps that record *beside* the package — in `akashi`'s
`protection_by`, in its own job record — and never inside it.

Sora's implementation is the correct one.

## Why

`package_id` exists so that a package can be checked: two builds of the same
question over the same corpus with the same settings produce the same id
([ADR-0003](0003-a-package-is-reproducible.md)), and a package whose id does not
match its content has been altered. **That property only holds if the content
the id covers is content tsumugi wrote.** The moment a downstream party may edit
a field and recompute, an id proves that *somebody* computed it over *something*,
and the check is worth nothing.

A recomputation path would make this worse rather than better. It would give
the edit a blessed shape, so that a package could pass every structural check
and still describe a prompt that was never rendered from it. The failure mode
this library is built around is *slightly false* — a record that is almost what
happened — and a blessed edit is exactly that.

The `ask()` case is not a counterexample. There, tsumugi *is* the party doing
the protecting: it holds the redactor, applies it to the text it rendered, and
records that it did, in the package it is still building. The record is of
tsumugi's own action, written before the id is computed. Sora's case is a
different party acting on tsumugi's output after the fact, and the record of
that belongs to that party.

## What this means in practice

- `provenance.protection` is empty in every package a consumer builds through
  `context` + `render`. That is correct: tsumugi protected nothing.
- The protection record travels with the package, not in it. `akashi` already
  has `protection_by` and `restored_by` in its provenance for this reason.
- A ledger entry in tsumugi says a package was built; whether the prompt was
  then protected is not tsumugi's to know. A consumer that wants one record of
  both keeps its own ledger and passes `ledger: false`.

## What it costs

**Two records where there could be one.** A consumer reading a package alone
cannot tell whether the prompt was protected; it has to look beside it. That is
the price of an id that means something, and it is paid once at design time
rather than every time an id fails to match.

**`ask()` and `context`+`render` produce packages that differ in one field for
the same protected exchange.** A reader comparing the two must know why. This
document is why, and `docs/mcp.md` says it where a consumer will find it.
