# 13. Label the evidence, not the ideal answer

**Status:** accepted

## Context

The evaluation chapter needed a dataset, and the proposal on the table was: generate
many prompts across random genres, pair each with the ideal structured prompt, and
score tsumugi against the pairing.

The instinct is right — a broad, genre-diverse corpus is exactly what a selector
needs, and hand-authoring one is hopeless. The pairing is the problem, in three
ways.

**There is no single ideal structured prompt.** For one question over one corpus,
many renderings are good. Section order, how much surrounding sentence to carry,
whether two adjacent passages merge — these are taste. A metric that scores
distance to one chosen rendering measures conformity, not quality, and will punish
an improvement that happens to look different.

**An LLM-authored ideal makes the test circular.** If a model writes both the
question and the ideal answer, the score measures agreement between tsumugi and
that model's taste on that day. Change the generator and every number moves.
`mamori` learned the general form of this the expensive way: its ADR-0023 records a
feature whose README paragraph described an intention as though it were a result,
and the correction only came when the harness was pointed at something objective.

**The ideal cannot know what was available.** A model writing an ideal prompt has
not run retrieval over the corpus. It cannot know which passage tsumugi *should*
have found, only which one reads well. That is the wrong question.

Meanwhile `mamori`'s own dataset shows the shape that does work. It does not label
the ideal protected text. It labels **which spans are sensitive** — with inline
markup, offsets computed by the loader, because hand-written offsets measure the
annotator — and derives leak rate and over-redaction from that. The label is
objective; the metric is arithmetic.

## Decision

**The corpus is generated. The labels are computed. Nothing labels the output.**

Each evaluation case is a synthetic corpus in a random genre, a question, and a
**labelled evidence set**: the spans that must appear in the package, and the
spans that must not.

Facts are planted with inline markup and the loader computes the offsets, in
`mamori`'s manner:

```markdown
## 装備
{{F:tent-weight}}テントは 2.4kg、二人用{{/F}}。予備は持たない。
```

```json
{"question": "テントの重量は?",
 "must_include": ["tent-weight"],
 "must_not_include": ["tent-weight-old"],
 "traps": {"tent-weight-old": "superseded"}}
```

Everything measured is then arithmetic over anchors, with no judgement:

| Measured | From |
|---|---|
| Evidence recall | share of `must_include` present in `items[]` |
| Evidence precision | share of `items[]` that are labelled relevant |
| Trap rate | share of `must_not_include` that got in |
| **Omission correctness** | for each excluded required fact, is it in `omissions[]` under the *right* rule |
| Budget adherence | `sum(cost) <= limit`, always |
| Reproducibility | two runs, one `package_id` |

Omission correctness is the one that would not exist without
[ADR 0005](0005-selection-is-a-report.md), and it is the most valuable of the six.
It does not ask whether the outcome was right; it asks whether the *reason given*
was right. A system that drops the correct document and correctly says
`budget_exhausted` is behaving well at a budget that is too small. A system that
drops it and says `below_threshold` has a ranking bug. The same outcome, two
diagnoses, and only a labelled corpus can tell them apart.

**The traps are the dataset.** A generated corpus of relevant documents measures
nothing, because any retriever passes it. The genres are decoration; the planted
adversaries are the test:

| Trap | What it catches |
|---|---|
| Lexically similar, topically wrong | Retrieval precision |
| Near-duplicate of the answer | [ADR 0008](0008-redundancy-is-proposed.md): marked `redundant_candidate`, still considered, never silently deleted |
| Superseded version — 94% identical, the 6% is the correction | Whether the later document wins, and whether the older one is *reported* rather than vanished |
| Required fact ranked below the budget line | [ADR 0005](0005-selection-is-a-report.md): does it appear in `omissions[]` with `budget_exhausted` |
| The answer is simply absent | Does the package say so, or assemble something plausible |
| Mixed Japanese, English and code | [ADR 0007](0007-index-japanese-by-bigram.md) and [ADR 0006](0006-the-budget-is-an-estimate.md) |
| Document edited after ingest | [ADR 0010](0010-the-index-stores-the-text.md): `stale_anchor`, not silent re-anchoring |

**A model runs at authoring time only.** Cases are generated once and committed as
fixtures. CI never calls a model. This follows from
[ADR 0003](0003-a-package-is-reproducible.md) — a dataset that varies between runs
cannot detect a regression — and from
[ADR 0001](0001-the-domain-depends-on-nothing.md): the evaluation of a
zero-dependency library must not need a GPU to run.

**Two tiers.** Roughly sixty cases in CI, running in seconds; the full 100–1000 on
demand and nightly. A suite too slow to run is a suite that decays, which is the
same reasoning that keeps the domain layer dependency-free.

**A held-out split.** A share of cases is not read while tuning. A ranker tuned
against every case it will be scored on is a ranker fitted to the dataset.

**Every sample is invented.** These files ship inside the package. A real name, a
real address or a real key committed here is published to everyone who installs
tsumugi. Generation helps here rather than hurting — nothing real gets pasted in —
but the generator is told, and a test greps the fixtures for the shapes anyway.

## The part this does not measure

Whether a rendered prompt is *good* — well-ordered, well-sectioned, pleasant for a
model to read — is a real question that this dataset deliberately refuses.

It is kept, separately and smaller: a hand-authored set judged by **comparing two
renderings against each other**, never by distance to an ideal. That is `mamori`'s
`--compare`, which prints the individual samples that changed rather than an
aggregate, because tuning against an aggregate fits a prompt to a number instead
of to a corpus.

Roughly twenty cases, human-judged, run when the renderer changes. Small on
purpose: a subjective set that grows starts to look like an objective one.

## Consequences

Every number in `tsumugi eval` is arithmetic over labelled anchors. There is no
grader, no model, no rubric, and nothing to disagree with.

The dataset can be large, because generating a corpus is cheap and labelling is a
by-product of generating it. A thousand genres cost about as much as ten.

The traps make ADRs testable. [ADR 0005](0005-selection-is-a-report.md),
[ADR 0008](0008-redundancy-is-proposed.md) and
[ADR 0010](0010-the-index-stores-the-text.md) each stop being prose and become a
column in a scoreboard.

Combined with the ledger ([ADR 0011](0011-record-what-was-sent-and-what-was-used.md)),
there are two independent measurements: a synthetic corpus with known answers, and
the owner's real questions with no answers but real usage. Neither is sufficient.
Together they cover the gap each leaves.

## What it costs

**A generated corpus is not a real one.** Synthetic notes are cleaner, more
consistent and better structured than anything a person actually writes. A ranker
tuned on it will be tuned for tidiness. The mitigation is the ledger over a real
corpus, and it is a mitigation rather than a fix.

**The generator becomes a maintained artefact** with its own bugs. A generator that
plants a trap incorrectly produces a case that fails a correct implementation, and
that failure is expensive to diagnose because the instinct is to blame the code. A
self-check pass — every case must be solvable by an oracle that reads the labels —
is required, and is itself more code.

**The genre distribution is arbitrary** and will not match anyone's corpus.
Reporting per-genre rather than only in aggregate keeps this visible.

**Judgement is not eliminated, only relocated.** Someone decides that a given
distractor is a trap and not a legitimate second answer. That decision is now in
the fixtures instead of in a grader — more visible, more reviewable, and still a
judgement.
