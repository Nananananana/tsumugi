# ADR-0021: The ledger records *that* a package was protected, not how

*Accepted 2026-08-30. Answers the question `proposals/0003` left open, now that
`mamori.protection-scope/1` exists.*

## The question

`mamori`'s ADR-0032 decided that a protection record **inherits the sensitivity
of the text it describes**, and named the failure it guards against: a record
holds no values, therefore looks harmless, therefore flows into manifests, audit
logs and headers.

tsumugi's ledger is a durable log beside the index. It holds `query_hash`,
`document_id`, offsets, scores and counts — [ADR 0011](0011-record-what-was-sent-and-what-was-used.md)'s
rule is *identifiers, offsets and counts; never text*. So it is clear of that
failure today, and the obvious next move once a protection record exists is to
put one in it.

ADR-0032 also says how to carry its reasoning elsewhere:

> that prohibition list is what this criterion produced **in this domain**, and
> is not part of the criterion. Borrow the criterion; redraw the prohibitions.

So the prohibitions get redrawn here rather than copied.

## Redrawing them for a ledger

The criterion: **a record may state only what is derivable from the thing it
describes.** A ledger entry describes *one package that was sent*.

That gives the line directly.

**Whether the package was protected is a property of the package.** It is
derivable from the thing the entry describes, and it answers a question the
ledger is for: *am I sending protected or unprotected packages, over months?*

**A scope identifies a session, which is not the package.** `scope` is
`session-` plus twelve hex from a uuid4 — mamori is careful that nothing in it
derives from the document, and refuses at protect time if a detected value
appears in a caller-supplied one. So it is not sensitive *in itself*, and it
would pass ADR-0011's letter without argument.

It fails on a different axis, and the axis is what a ledger *is*. Every field
in the ledger today is derivable from the index the ledger sits beside — and
that index is already a complete plaintext copy of the corpus
([ADR 0010](0010-the-index-stores-the-text.md)). The ledger therefore adds no
reach that its neighbour does not already have. **A scope would be the first
field that points somewhere else**: at mamori's mapping, which is the one thing
tsumugi deliberately never holds.

One scope in one package is a join key for one conversation, and it lives as
long as that package. The same scope in a log that accumulates for months is a
durable index of *every session that ever protected anything out of this
corpus*, sitting next to the corpus. That is a different object, and nothing
the ledger is for needs it.

**The lists are refused outright.** `placeholders`, `protected` and `masked`
carry kinds and counts. A ledger holding those becomes a document that says
*this corpus yielded four NATIONAL_ID values in March*, which is a fact about
the corpus produced by a detector and kept for months. It is mamori's named
failure with the serial numbers filed off, and the fact that no value appears
is exactly why it would get past a reviewer.

## The decision

The ledger gains **one boolean**: `protected`.

Not the scope. Not the mode. Not the kinds, and not the counts. A reader who
needs those has the package, and the package is where they are.

## What it costs

**It cannot answer "which session protected this".** Correlating a ledger entry
with a mamori session now requires the package, which is the intended shape:
the join key exists for as long as somebody keeps the document that needs it,
and not longer. Anyone who genuinely needs the durable correlation has to build
it deliberately, somewhere they have argued for.

**It is a boolean about a thing with more than two states.** A package can be
protected reversibly, protected irreversibly, or protected in `mixed` mode
where some values were substituted with plausible ones and others with tokens.
The ledger flattens all of that. That is the point — and it means the ledger
cannot be used to find "packages whose citations can no longer be checked",
which is a real question with a real answer in the package's own
`provenance.protection.reversible`.

**One more column on a table that is meant to stay boring.** Every field added
to a durable log is a field somebody will later want to join on. This one is
justified above; the next one needs its own paragraph, and this ADR is the
precedent that says so.

## What was not decided

Whether tsumugi should *consume* `mamori.protection-scope/1` at all — read the
record rather than construct its own `Protection`. It would remove a small
duplication, and it would import a contract with a consumer invariant attached:
**a consumer that understands only `placeholders` must refuse `surrogate` and
`mixed`**, because reading the placeholder list and concluding the substitutions
are fully enumerated is the quiet failure that contract exists to prevent.

tsumugi is safe from that today by never reading the list: it delegates
restoration to the session, so `mixed` mode round-trips without tsumugi knowing
what a placeholder looks like. `tests/test_adapter_mamori.py` now pins that.
Consuming the record would trade a property that holds by construction for one
that holds by discipline, and nothing needs it yet.
