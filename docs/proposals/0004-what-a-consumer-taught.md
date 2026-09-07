# 0004: What a consumer taught

*This is a proposal: it revises the roadmap. It is not evidence that anything
in it exists.* What is true now lives in `docs/`; what was decided lives in
`docs/adr/`.

`0003` revised `0002` from what running the library taught. This revises `0003`
from what **being used** taught: `sora`, the family's orchestration layer,
integrated against tsumugi over four exchanges and found things no amount of
self-testing had.

## What a consumer found that the suite could not

Four defects, and the pattern is worth naming before the list. **Every one of
them was invisible from inside**: the corpus has no front matter with URLs in
it, no Korean question phrased with a particle the document lacks, no ten
thousand documents, and no second reader of an exit code.

| | how it surfaced |
|---|---|
| **A `source_url:` line was returned as citable evidence** | sora said the one shape that would break it was front matter appearing in a quotation. It was already happening. |
| **Korean questions died at the index** | four labelled cases returned *nothing* — not a near miss, silence. Korean glues particles to nouns and the tokenizer's comment said the opposite. |
| **Ingest was quadratic** | sora asked for a number at ten thousand documents. Nobody here had run that size. 613 s, and one line of SQL was the whole of it. |
| **Every query walked the whole corpus twice** | same question, same reason. 68% of a `context` call at that size, invisible at 300 documents. |

The lesson is not "test more". It is that **a corpus written by the people who
wrote the retrieval cannot contain the shapes they did not think of**, which is
the same argument v1.0 (1) already makes about the trap rate, arriving from a
different direction and with four concrete instances instead of a hypothesis.

## Questions 0003 left open, answered by measurement

**Item 0's residual — can the 0.7 trap points that sections cost be recovered
by scoping ADR-0019's floor to a document?** *(Answered 2026-09-07. No.)*

`tools/measure_floor_scope.py`:

| scope | recall | precision | trap |
|---|---|---|---|
| query (ships) | 87.2% | 98.2% | **5.0%** |
| document | 87.2% | 97.0% | **26.7%** |

Five times worse, with recall unchanged. The reason is visible once measured: a
weak section of a weak document clears its own document's low bar, so every
document that matched anything contributes its best part as evidence. **The
0.7 stands as the price of chunking**, and item 0's last open question is
closed.

The variant lives in the measuring tool rather than as a setting. A switch
whose only measurement says it is five times worse is a switch somebody turns
on.

**Item 4's Chinese residual — does the trick that fixed Korean transfer?**
*(Answered 2026-09-07. No, and the cost is the reason.)*

Korean was fixed by expanding the *question* into its prefixes: free, because a
question is a dozen characters. The two Chinese cases that return nothing are
`食物` asked of `食品` and `学期` asked of `开学` — they share a **character**,
not a prefix. Query-side expansion cannot reach them, because the index holds
bigrams and a one-character query term matches no bigram.

Index-side unigrams do retrieve them, and cost **80% more index**
(0.789 → 1.421 terms per character) to move two cases of 180 from silence to a
lead that confirmation still rejects. Refused, with the number.

**The Chinese residual is genuine paraphrase**, which is item 1's subject, not
a tokenization bug. That is now measured rather than assumed.

## The roadmap this leaves

### Next — in the order I would do them

**1. A corpus this project did not write.** *(v1.0 condition 1, unchanged, and
now the top of the list rather than a footnote.)*

Everything above is an argument for it. Four defects came from one outside
reader in one week; the corpus has produced none in a month. The condition and
its four intake rules are in `0003` and do not move.

**Blocked**, and the only item here that is. Nothing in this repository
substitutes for it — that is the point of the condition.

**2. The paraphrase residual, through leads rather than through items.**
*(New. Supersedes the "carry them marked" half of item 1.)*

Twenty-three of 180 cases produce an empty package. Similarity ranks the answer
first in 15 of them and **0 survive confirmation**, which is why item 1 has
stood still: there was nowhere for an unconfirmed passage to go.

[ADR-0026](../adr/0026-a-lead-is-offered-only-when-there-is-nothing-to-confuse-it-with.md)
built somewhere. A **lead** is not evidence, carries no hash, is absent from
the frozen contract, and is offered only when the package is empty — exactly
the population similarity recovers. So the question item 1 could not answer,
*what confirms a semantic candidate*, stops being the blocker: **nothing
confirms it, and a lead does not claim to be confirmed.**

What is not yet known, and what would have to be measured first:

- bm25 leads are worth 43.5% useful against 21.7% misleading. An embedding
  candidate source has to beat that, on the same cases.
- The oracle ceiling over passages already offered is 93.8%, and both bm25 and
  a cross-encoder reach 62.5% — **so ranking is not the gap**; a candidate
  *source* that surfaces passages bm25 never returned might be.
- 16 cases is not enough to separate two rankers (one case is 6.25 points).
  This may be un-decidable until item 1 above lands.

**Closes** when a semantic source raises the useful share of leads without
raising the misleading share, on cases held out from whatever tuned it.
**Does not close** by letting a semantic candidate become an item.

**3. `connect` hands back a raw connection.** *(Carried from the axis review,
still true, now the cheapest thing on the list.)*

A library caller gets a `sqlite3.Connection` and a `try/finally`; the CLI has a
registry. Recorded in a test rather than fixed, and the surface is promised now
(ADR-0023), so the fix is additive: a context manager beside `connect`, not a
change to it.

**4. The FTS query is most of what a large query costs.** `context` at 10,000
documents is 593 ms, of which `index.search` is 365 ms. That is the right shape
— the search should dominate — and it is the next thing to profile if anyone
needs the number lower. Nobody does today: sora's budget is one second and this
fits.

### Answered since 0003

- **Item 0's residual**: no, measured (above).
- **Item 4's Chinese half**: the residual is paraphrase, not tokenization,
  measured (above). Korean's *was* tokenization and is fixed.
- **Item 3, revisited**: `0003` answered *may a package carry an unconfirmed
  item* with no, and that stands. ADR-0026 answers the different question it
  hid — *may a caller be told what was found when nothing was confirmed* —
  with yes, outside the package.

### Not planned, and why

Everything in `0003`'s list, unchanged, plus:

- **Index-side unigrams for ideographs.** 80% more index for two cases that
  confirmation rejects anyway (above).
- **A per-document relative floor.** Five times the trap rate (above).
- **Batch `search` over MCP.** One query per call. A process holding the
  connection open pays nothing for the round trip, and sora — the only consumer
  that would use it — said so.

## What v1.0 would mean

Unchanged from `0003`. Condition 2 is done; **condition 1 is the whole of what
is left**, and this proposal's first section is four more reasons it is the
right condition.
