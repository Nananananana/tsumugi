# ADR-0019: Confirmation is relative, and it has to say where

*Accepted 2026-08-30. Extends [ADR 0007](0007-index-japanese-by-bigram.md) and
[ADR 0018](0018-confirm-a-paraphrase-by-coverage.md).*

## The question

The evaluation corpus went from 10 genres to 30, in four languages instead of
two, with document shapes instead of one document shape. Nothing about the
library changed, and the trap rate went from **6.0% to 25.8%**.

That is the number this whole exercise was for. Ten genres written by whoever
was writing the ranker is ten genres whose vocabulary was chosen — without
anybody intending it — to suit the ranker. Twenty more, drafted by a local
model and reviewed rather than authored, are not.

The failure was consistent. A case asks:

```
what is the coverage period of product warranty
```

and the corpus holds a near-miss document saying

```
The coverage period of product return policy is 1 year
```

The near-miss shares a **five-word run** with the question. The answer document
shares six. Confirmation was a yes-or-no, so both confirmed, and with a loose
budget both were sent. The discriminating word — *warranty* — carried no more
weight than *the*.

## The decision

Three changes, all in the confirmation stage.

**1. Confirmation reports how much it matched, and that is scored.** A document
matching six words of a six-word question ranks above one matching five.
Cheap, and it was simply missing: the score counted how many *times* a needle
occurred and never how *long* the needle was.

**2. A confirmation much weaker than the best one found is not evidence.**
`RELATIVE_MATCH_FLOOR` is the fraction of the strongest match a candidate has
to reach; below it the candidate becomes unconfirmed and is reported as an
omission rather than sent.

Relative, not absolute, and that is the substance of this ADR. "Five words" is
overwhelming evidence in one corpus and nothing in another. What can be
compared is *this document against the other documents that answered this
question*, which needs no tuning per corpus and no term statistics.

**3. Coverage says where the evidence is, not where the first word was.**
[ADR 0018](0018-confirm-a-paraphrase-by-coverage.md) matched each content term
independently and took the first occurrence of each. The first mention of what
a document is about is its heading — so a package built by coverage carried
titles where the evidence should have been. Terms are now located *together*:
the item is where they crowd.

**4. A stem match counts as the whole term.** Korean asked for this and every
Korean case failed without it: `가계부의` and `가계부` are one word with a
particle attached, and the particle is Hangul like the stem, so the script
segmentation that separates `テント` from `の` cannot separate them. A match on
a prefix at least two characters long and no more than `INFLECTION_TAIL` short
counts in full. English plurals and Japanese okurigana fall out of the same
rule, and it needs no word list — the constraint that ruled out a segmenter in
ADR-0007 and a stopword list in ADR-0018.

### Measured

`RELATIVE_MATCH_FLOOR` swept on train, confirmed on held-out:

| floor | train recall | train traps | held-out recall | held-out traps |
|---|---|---|---|---|
| off | 88.0% | 21.4% | 72.2% | 30.6% |
| 0.6 | 88.9% | 8.3% | 72.2% | 11.1% |
| 0.7 | 88.9% | 7.1% | 72.2% | 8.3% |
| **0.8 – 1.0** | **88.9%** | **3.6%** | **72.2%** | **2.8%** |

Train and held-out agree on the shape, so the gain is not fitted. **It costs no
recall at all.**

Whole corpus, across the four changes: trap rate **25.8% → 3.3%**, precision
93.9% → 97.4%, recall 82.6% → 87.2%, omission correctness → 100%. The `ci`
tier is 149 of 150 clean with recall at 100%.

Changes 3 and 4 are worth nothing apart and 3.5 points of recall together: the
stem rule looked like a bad trade (8 points of precision for no recall) until
coverage stopped pointing at headings, because a stem match that located the
wrong line is a wrong answer with better manners.

### The threshold, chosen rather than measured

0.8 through 1.0 score identically, so the corpus cannot separate them. **0.8 is
chosen, and it is the permissive end — the opposite of ADR-0018's choice.**

Both follow from the same question: which value depends less on a peculiarity
of the fixtures? In ADR-0018 the threshold governs admitting evidence with no
phrase match at all, and strict is the safer reading of weak evidence. Here it
governs *discarding* evidence that did phrase-match strongly, and every case in
this corpus has exactly one answer — so the corpus cannot show the cost of
discarding a second document that answers nearly as well. A real notes folder
produces those constantly. Picking 1.0 would be optimising for the fixtures.

## What it costs

**A near-miss now needs a competitor.** The floor is relative, so a query whose
only confirmed match *is* the near-miss keeps it — there is nothing to be
weaker than. That is correct, and it means the trap rate above is a property of
corpora that contain the answer. On a corpus that does not, precision is
whatever the index gave.

**Rank now affects membership, not just order.** Before this, a confirmed
candidate that fitted the budget was sent. Now it can be excluded for being
relatively weak, and the exclusion is reported under `below_threshold` like any
other. That is a real widening of what "below threshold" means, and a reader
comparing packages across versions will see candidates move.

**Four knobs where there were none.** `MATCH_WEIGHT`,
`RELATIVE_MATCH_FLOOR`, `INFLECTION_TAIL` and `COVERAGE_THRESHOLD` are all
tuned against one corpus, and a corpus is not the world. Each is a named
constant with its sweep recorded, which is the most this project can honestly
offer: they are visible, and they are re-measurable.

**Chinese still has no answer for the paraphrase case.** Its particles — 的,
是 — are Han characters like its nouns, so script segmentation yields one term
for a whole question and nothing can be dropped. Japanese has kana, Korean has
spaces, Chinese has neither. This is where the no-dictionary constraint bites
hardest, and the corpus now says so out loud instead of not asking.

## What was not decided

Term rarity from the index (bm25 knows which word is *warranty*, and the
confirmation stage still does not), embeddings, and any per-language resource.
The first is the obvious next thing and wants its own measurement.
