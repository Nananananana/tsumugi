# ADR-0022: An unconfirmed candidate is an omission, not an item

*Accepted 2026-08-31. Answers the question left behind when `proposals/0003`'s
embedding item was withdrawn.*

## The question

May a package carry an item that nothing lexical confirms — proposed by
similarity, marked as unconfirmed, and left for the reader to weigh?

`proposals/0003` proposed exactly that, and cited
[ADR 0005](0005-selection-is-a-report.md): *selection is a report, not a
promise*. A package already says why each item is there. An item confirmed by
nothing could say that too, in the JSON, the rendered prompt and the CLI, and
its anchor would still be exact because the span still comes from stored text.

The question became urgent when the embedding measurement retired the item that
raised it. Similarity ranks the answer first in 15 of 23 lexical misses; **0 of
23 survive confirmation unchanged**, because unconfirmed results never enter a
package. So "carry them marked" is the only version of that feature that
recovers anything at all.

## The decision

**No.** A candidate that nothing lexical confirms is reported as an
**omission**, with its anchor, and never as an item.

That is what the code already does — `build_context` drops it with the reason
*an unconfirmed candidate has no established relevance*. This ADR is not a
change. It is the decision the behaviour was waiting for, and it says why the
alternative was refused, because otherwise the next person to read
`proposals/0003` will implement the proposal it contains.

## Why marking is not enough, and it is not a matter of taste

Both routes tell the reader the same fact. They differ in **what happens when
the reader ignores the telling**, and that asymmetry is the whole argument:

- ignoring an **omission** loses information — the reader does not learn about
  a passage that might have mattered;
- ignoring a **mark** on an item adds information that is false — the reader
  treats an unsupported passage as evidence, and it was in the evidence list.

A context package exists to be read by a model. The measured fact about models
reading these packages is that **they differ wildly in how much of the
structure they honour**: on the same fifty cases and the same prompt,
`qwen2.5:14b` answered all fifty and `llama3.1:8b` produced nothing readable in
any of them. A design whose safety depends on the reader noticing a label is a
design resting on the one component this project has measured to be
unreliable — and has no ability to fix.

There is also a number. Keeping unconfirmed candidates out of packages moved
the trap rate from **96.7% to 36.7%**. That was measured for *exclusion*.
Whether marking would preserve any of it is unknown, unmeasurable from here,
and a property of the reader rather than of this library. Trading a measured
36.7% for an unmeasured promise is the trade this project keeps refusing.

## What this does not say

It does not say similarity is useless. An omission carries an anchor —
`$defs/omission` requires one — so a package can report *"a passage ranks high
for your question and shares none of its words: here it is"* without that
passage entering the evidence. A reader who wants it can fetch it deliberately.
**That is the same fact, in the shape whose failure mode is losing information
rather than inventing it.**

It also does not settle whether an embedding candidate source should exist. It
settles what such a source would be allowed to produce, which is the part that
had to be decided before anything was built.

## The constraint that decided the rest

While answering this, the frozen contract was asked whether it *could* carry a
marked item. It cannot, and the reason is worse than a missing field.

`properties.contract.description` promises:

> Frozen at version 1. **A field may be added**; none will be removed or change
> meaning.

Every object in the schema sets `additionalProperties: false`. Measured against
the published schema and the shipped example package:

| change | result |
|---|---|
| a new top-level field | **rejected** — `Additional properties are not allowed` |
| a new field on an item | **rejected** |
| a new value in the omission `rule` enum | **rejected** — not one of the six |

**The compatibility promise is false in all three directions, and has been
since the freeze.** A consumer validating against the schema tsumugi publishes
rejects any package using the extension the same document told them to expect.

This cannot be repaired by relaxing the schema now: consumers who vendored the
strict copy — which `docs/context-package.md` tells them to do — still hold it.
The promise was unkeepable the moment it was frozen.

So the wording is corrected rather than the schema: **v1 is closed.** Adding
anything means `tsumugi.context-package/2`, and `SUPPORTED_CONTRACTS` already
has the machinery to accept both. That is a higher bar than "add a field", and
it is the honest one — it was always the actual bar; only the description said
otherwise.

This makes the decision above less costly than it looked. Carrying a marked
item was never a small change to v1. It was a new contract version.

## What it costs

**A real capability is refused.** The 15 of 23 paraphrase cases similarity
finds stay out of packages. The residual `proposals/0003` named remains, and
the honest description of tsumugi is that it does not retrieve what it cannot
confirm — in any language, including the two where confirmation is weakest.

**Omissions grow.** Every high-scoring unconfirmed candidate now has a standing
reason to appear in `omissions`, which is a list consumers read. A package that
reports twenty omissions for two items is technically complete and practically
noise; if that happens, the cap belongs in the omission list rather than in the
rule.

**The correction is an admission in public.** `docs/context-package.md` and the
schema have told consumers for the whole life of the contract that fields may
be added. Anyone who built on that sentence built on nothing. Saying so is the
cost of having frozen a contract without testing its own compatibility claim —
and the test that would have caught it is three lines long, which is the part
worth remembering.
