# ADR-0025: Outside the domain, a library may help — if it is measured first

*Accepted 2026-09-01. Amends the reach of [ADR-0001](0001-the-domain-depends-on-nothing.md);
answers the condition [ADR-0007](0007-index-japanese-by-bigram.md) set for itself.*

## What changed

The rule was *zero runtime dependencies*, everywhere. It is now:

> **The domain depends on nothing but the standard library. Outside it, an
> external library is allowed, as an optional extra, when it has been measured
> on this corpus.**

ADR-0001 is unchanged where it matters — the domain still imports nothing, and
an architecture test still enforces that. What moves is `infrastructure/` and
`tools/`, which may now reach for something the standard library cannot do.

This is not a reversal. ADR-0007 wrote the door into its own costs:

> A proper analyzer (MeCab, Sudachi) would do better and is a dependency with a
> dictionary. **If the golden retrieval dataset ever shows this costing real
> recall, it comes back as an optional adapter** — never as a core dependency.

## What it reopened, and what the measurements said

Three refusals rested partly on the dependency rule. All three were measured
before anything was built, and **two of the three stayed refused.**

### Segmentation: refused, and the diagnosis was wrong

The dataset did show a cost — Chinese and Korean recall is 83.3% against
English's 90.7% — so ADR-0007's condition looked met. It was not the right
condition. Of the 23 cases the lexical stage misses:

| terms from | confirm the answer |
|---|---|
| character bigrams (today) | 0 of 23 |
| `janome` | **0 of 23** |
| `jieba` | **2 of 23** |

**These are paraphrases, not mis-segmentations.** The question uses different
words, and no boundary rule finds a word that is not there. Chinese recall is
lower because Chinese questions in this corpus are paraphrased more often, not
because bigrams cannot find Chinese words.

That is worth keeping as a correction to ADR-0007's own cost section: the
condition it wrote — *"costing real recall"* — was satisfied by a number that
had nothing to do with segmentation. **A trigger can fire for the wrong reason,
and a trigger that names a symptom rather than a mechanism usually will.**

### The reranker as a gate: refused

`BAAI/bge-reranker-base` is what a rival library reaches for, and it is good:
it ranks the answer first in **17 of the 23** cases the lexical stage misses,
beating the bi-encoder's 15.

It is also, on the 120 trap cases:

| | |
|---|---|
| forbidden document ranked first | **10 (8.3%)** |
| answer ranked first | 53 (44.2%) |

This library's trap rate is **4.2%**. A pipeline that let the model decide what
to send would roughly double it. [ADR-0022](0022-an-unconfirmed-candidate-is-an-omission-not-an-item.md)
refused to carry an item nothing lexical confirms, and **a better model does not
overturn that decision; it prices it.** Seventeen recoverable paraphrases,
declined, and now we know what they cost.

### The reranker as an ordering: accepted

Where being wrong costs an ordering rather than a false citation, the same model
is welcome. `--ordering rerank` reorders candidates **confirmation has already
accepted**, and cannot add one. It sits behind `tsumugi[research]`, and asking
for it without the extra raises rather than falling back — a setting that is
honoured and does nothing is this project's own named failure.

## The rule this leaves

An external library may be added when three things are true:

1. **it is outside the domain**, which the architecture test enforces;
2. **it is an extra**, so `pip install tsumugi` still installs nothing;
3. **it has been measured here**, and the measurement is in
   `docs/measurements.md` — including when the answer is *no*.

The third is the one that did the work today. Two of three candidates were
refused on evidence, and both would have been easy to adopt on reputation:
`jieba` and `janome` are the obvious answer to "your Chinese is weak", and a
cross-encoder is the obvious answer to "your recall is weak". **Both obvious
answers were wrong here**, and only for this corpus, which is the caveat v1.0's
first condition exists to remove.

## What it costs

**The dependency argument is gone as a defence.** Refusing something now
requires evidence, and evidence takes a day. Three ADRs could previously say
*"and it is a dependency"* and stop; none of them can any more, and one of them
(ADR-0007) turns out to have been right for a reason it did not give.

**An extra is a supported configuration.** `tsumugi[research]` will be
installed by someone, and `--ordering rerank` is now covered by ADR-0023's
deprecation rule. Two of its three libraries are not used by the library at all
— `janome` and `jieba` are there for `tools/measure_segmenters.py`, so that the
refusal above stays reproducible rather than becoming a claim about a
measurement nobody can re-run.

**And the reranker is slow and downloads a model.** Everything else here is
deterministic, local and offline. That is now true of everything *except* one
named setting, and the README says so where the setting is documented rather
than only here.
