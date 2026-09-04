# ADR-0026: A lead is offered only when there is nothing to confuse it with

*Accepted 2026-09-05. Revisits the part of
[ADR-0022](0022-an-unconfirmed-candidate-is-an-omission-not-an-item.md) that
was never measured.*

## The question

The commonest complaint about this library is that it says nothing. A reader
asks a question, confirmation supports no passage, the evidence list is empty,
and they are told *no confirmed evidence*. That is true. Told it for every
question they ask, they stop asking, and a retrieval library nobody queries has
no properties worth defending.

ADR-0022 refused to put unconfirmed candidates in packages, and the refusal
holds. But it settled the question **across all cases**, where an unconfirmed
passage sits beside confirmed ones. It never asked the narrower question that
produces the complaint: what should happen when the evidence list is *empty*,
and the alternative to an unconfirmed passage is not weaker evidence but
silence?

Those are different trades. Only one of them had a number.

## The decision

**A package's best-ranked unconfirmed candidate may be resolved back into a
passage and offered as a `Lead` — by default only when the package has no
items, and never inside the package.**

A `Lead` is not a `ContextItem` and cannot become one. It carries no
`text_hash`, so nothing can verify against it; the property that would make it
verifiable is the confirmed relationship to the question it does not have.
`leads_from()` is a separate call on the public surface, and the frozen
`tsumugi.context-package/1` document is byte-for-byte what it was.

Nothing new is retrieved. An omission has always carried a `document_id` and a
`span` — ADR-0022 says in as many words that a reader who wants the passage can
fetch it deliberately. **Nothing offered them a way to.** This is that way.

## The measurement that decided the shape

`tools/measure_empty_packages.py`, over the 180 labelled cases that name a
required fact. 23 of them (12.8%) produce a package with no items at all.
Resolving their omissions back into passages:

| offered | holds the required fact | holds a forbidden one |
|---|---|---|
| all | 65.2% | 65.2% |
| best 3 | 65.2% | 65.2% |
| best 2 | 65.2% | 56.5% |
| **best 1** | **43.5%** | **21.7%** |

**Handing over every near miss is a coin flip** — as likely to offer the
passage the case forbids as the one it requires. That is the 96.7% trap rate
ADR-0022 measured, arriving through a different door, and it is why the naive
version of this feature is the wrong one.

The ranking does real work in the first position and almost none after it: the
second lead buys 21.7 points of risk and no recall whatsoever. So the default
is one.

## Why this is not the thing ADR-0022 refused

ADR-0022's argument is an asymmetry, and it survives intact here:

> ignoring an **omission** loses information; ignoring a **mark** on an item
> adds information that is false.

That argument is about a mark on an item **in an evidence list**. Its force
comes from the reader having real evidence to confuse the marked passage with.
With an empty evidence list there is nothing to be confused with — the reader
is not weighing a supported passage against an unsupported one, they are
weighing an unsupported passage against nothing at all.

`only_when_empty=True` is the default for exactly that reason, and it is the
whole of the difference. A caller may pass `False`; they have to say so.

The second half of ADR-0022's argument — that safety must not rest on a reader
honouring a label — is why the exit code stays **1** when only leads were
offered. A script cannot mistake a lead for an answer, whatever the model
reading the text does with it.

## What it costs

**A fifth of the time, a reader is handed a misleading passage.** 21.7% on this
corpus. The wording is blunt about it and the exit code does not move, and
neither of those makes the passage less misleading to a person who skims. This
is a real cost paid for a real gain, not a cost argued away.

**The number is from this corpus**, which this project wrote, and the one time
its vocabulary changed hands the trap rate moved 6.0% → 25.8% with no code
change. 43.5/21.7 should be read as *the ranking is worth roughly two to one in
the first position*, not as two decimal places.

**It is a second thing a reader must understand.** Packages had items and
omissions; there is now a third category that appears only sometimes. The CLI
prints it under a heading that says NOT confirmed, and that is one more piece of
structure to get right in every renderer downstream.

**`--json` does not carry leads, and that will surprise somebody.** The
contract is closed (ADR-0022), so the alternative was
`tsumugi.context-package/2` for a convenience. A consumer who wants leads calls
`leads_from`, which is why it is exported.

## What this does not say

It does not reopen whether an unconfirmed candidate may be an item. It may not.

It does not say the 12.8% empty rate is acceptable. Reducing it by confirming
more paraphrases is the better fix and remains open; this makes the failure
useful rather than making it rarer.
