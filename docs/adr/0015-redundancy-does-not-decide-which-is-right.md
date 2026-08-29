# 15. Redundancy does not decide which duplicate is right

**Status:** accepted
**Amends:** [ADR 0008](0008-redundancy-is-proposed.md), whose tie-break rule was
wrong. Everything else in 0008 stands and is now implemented.

## Context

ADR-0008 said near-duplicates are marked and never removed, and specified how
to choose which member of a pair survives:

> Which of a duplicate pair is preferred follows a stated, deterministic rule
> (earliest-dated source, then lowest `document_id`) so the choice is
> reproducible and explainable.

Implementing it produced two findings, and each of them contradicts a sentence
of that paragraph.

### Earliest-dated is backwards for the case that matters most

0008's own argument for marking rather than deleting is that near-duplicates
"differ in exactly the part that matters, because they are near-duplicates
*because* the difference is a correction." A corrected document is, necessarily,
the **later** one. Preferring the earliest-dated source would systematically
prefer the version that was corrected — the outdated value, the reversed
decision, the number that turned out to be wrong.

Two different situations were being treated as one. A passage copied from
another is best served by preferring the original; a passage that supersedes
another is best served by preferring the later. Nothing in a date tells you
which situation you are in.

### And the detector cannot tell them apart anyway

Measured, on containment over character shingles:

| | containment |
|---|---|
| verbatim copy | 1.000 |
| copy inside a longer document | 1.000 |
| copy, one clause changed | 0.889 |
| copy, reflowed to a new width | 0.873 |
| **same subject, corrected value** | **0.417** |
| **different subject, same shape** | **0.167** |
| same topic, rewritten | 0.000 |
| unrelated | 0.000 |

Copies separate cleanly from everything else, with a wide gap. But the two rows
in bold — *"the tent weighs 2.4kg"* against *"the tent weighs 3.1kg"*, and
against *"the tarp weighs 3.1kg"* — do not separate from each other. A
correction and a statement about a different subject look nearly the same to
character overlap, because what distinguishes them is meaning.

That is not a tuning problem and no threshold fixes it. Recognising a superseded
version requires reading both and understanding that they make competing claims
about one thing, which is exactly the judgement the deterministic core does not
make and a model is not allowed to make on its behalf.

### And whether a correction looks like a copy depends on the passage length

Running the corpus turned up a case that sharpens this. The table above uses
short sentences, where a changed value is a large fraction of the text. In a
real passage — a paragraph, which is what a package actually carries — the same
correction is a *small* fraction, and the two versions score **0.77**: above the
threshold, marked as copies.

So a superseded document is sometimes caught and sometimes not, and **which
happens depends on how much of the passage the correction changed**, not on
whether it is a correction. A detector whose behaviour on a category depends on
an unrelated variable cannot be trusted to act on that category.

That is the argument for marking rather than deleting, arriving from a
direction 0008 did not anticipate. If redundancy deleted, a one-word correction
inside a long paragraph would be deleted as a copy — and the one word would be
the answer.

## Decision

**Redundancy detection reports that two passages are alike. It does not decide
which is right, and it does not try.**

- **The ranker's order decides who survives.** `mark_duplicates` takes
  candidates already in preference order and marks each against the earlier
  ones. It has no date rule, no recency rule, and no opinion. Where the ranker
  is indifferent, the existing deterministic sort breaks the tie, so the result
  is still reproducible ([ADR 0003](0003-a-package-is-reproducible.md)).
- **`redundant_candidate` means "this is a copy", not "this is superseded".**
  The threshold is 0.75, sitting in the measured gap with 0.456 of margin
  either side. Anything the detector marks really is a copy.
- **Detecting a superseded version is out of scope**, and is written down as
  out of scope rather than left as an unmet expectation. The evaluation corpus
  previously asserted that a superseded document should be reported under
  `redundant_candidate`; that expectation was wrong and has been corrected to a
  `near_duplicate` trap, which is what the rule actually means.

## Consequences

The rule is one sentence and holds in both situations: whichever the ranker
preferred is the one that is kept, and the other is marked as a copy of it.
Nothing has to guess which of "original" and "correction" it is looking at.

The mark is trustworthy in the direction that matters. A `redundant_candidate`
omission is a statement the detector can support: this passage is a copy of
that one, by this much. It never means "this looked similar", which is what a
lower threshold would have made it mean.

`omission correctness` in `tsumugi eval` becomes measurable rather than
structurally zero, because the corpus now expects a rule the system can
actually produce.

## What it costs

**Superseded documents are not caught by anything.** A corpus with an old value
and a corrected one will offer both, and the package will not say that one
replaces the other. That is a real gap, and it is the one a reader of ADR-0008
would most expect to be closed. Two half-answers exist and neither is taken
here: the anchor layer already reports a *file* that changed
([ADR 0010](0010-the-index-stores-the-text.md)), which is a different thing; and
a model could be asked, which would put a model inside a selection decision.

**The ranker's order carries more weight than it used to.** It now decides not
only what is sent first but which member of a duplicate cluster is the survivor.
A ranking bug that used to cost ordering can now cost the wrong copy being kept
— and the copy that is kept is the one whose wording reaches the model.

**Shared boilerplate looks like duplication.** Two documents with the same
header, footer or template will overlap, and if the passages selected from them
are mostly boilerplate they will be marked as copies of each other. The
mitigation is only that marking is not deletion. If the evaluation corpus ever
shows this costing real recall, it comes back as a proposal for
boilerplate-aware shingling.
