# Measurements

*This is a current-state document: these are the numbers as of v0.1.0.dev0. Not
an ADR — a decision's reasoning stays as it was written, and a measurement gets
re-run. See [docs/README.md](README.md).*

Reproduce any row:

```bash
python tools/measure_index.py <corpus> [<corpus> ...]
```

Measured 2026-08-30, CPython 3.12.8, SQLite 3.47.1, Windows 11, NVMe SSD.

---

## The question ADR-0007 left open

[ADR 0007](adr/0007-index-japanese-by-bigram.md) chose character bigrams because
FTS5's default tokenizer finds nothing in Japanese and `trigram` cannot match a
two-character query. It listed the cost as unmeasured: *"An n-character document
yields n−1 tokens. The FTS5 index will be several times larger than a naive one.
Acceptable for a single local file, but it is a number, so it gets measured on a
real corpus in v0.1 and recorded rather than assumed."*

This is that number.

## Three corpora

| | Documents | Characters | CJK | Corpus | Index | Index ÷ corpus | Terms ÷ char |
|---|---|---|---|---|---|---|---|
| **A** code repositories (`mamori` + `kiseki`) | 666 | 2,727k | 0.9% | 3.15 MiB | 8.27 MiB | **2.62×** | 0.11 |
| **B** English prose (`tsumugi/docs`) | 22 | 128k | 0.2% | 0.13 MiB | 0.50 MiB | 3.98× | 0.15 |
| **C** CJK-heavy (Japanese and Chinese documents) | 6 | 66k | 37.3% | 0.12 MiB | 0.59 MiB | 5.11× | 0.43 |

**A is the number to quote.** B and C are small enough that SQLite's per-page
overhead is amortised badly and inflates their ratios; they are here because the
*shape* they show is real even where the absolute ratio is not.

### What drives it

Terms per character tracks the CJK share almost exactly, which is what the
tokenizer's design predicts: a CJK run of *n* characters yields *n−1* bigrams —
about one term per character — while Latin prose yields one term per word, about
0.15.

Corpus C is 37% CJK and measures 0.43 terms per character. The model
`0.37 × 1.0 + 0.63 × 0.15 ≈ 0.46` predicts 0.46. Close enough to trust for
planning:

> **terms per character ≈ (CJK share) + 0.15 × (1 − CJK share)**

So a corpus of Japanese notes should expect roughly **one index term per
character**, about seven times what English prose produces. The offsetting
factor is that a CJK character costs three UTF-8 bytes on disk against one for
Latin, so the *byte* ratio grows less than the term ratio does.

### What this means for a real notes folder

Extrapolating A's 2.62× with C's term density, a **50 MiB folder of Japanese
notes should produce an index somewhere around 200–300 MiB.** Large enough to
notice; not large enough to change the design. The verdict is that ADR-0007's
choice stands, and the cost it flagged is real but affordable.

Roughly 45% of the index is the stored document text
([ADR 0010](adr/0010-the-index-stores-the-text.md)) rather than the search
structure, so the bigram decision is not even the larger half of the bill.

## Speed

| | First ingest | Re-ingest, unchanged | Documents/second | Search p50 | Search p95 |
|---|---|---|---|---|---|
| **A** 666 documents | 2.98 s | **0.54 s** | 223 | 2.5 ms | 11.7 ms |
| **B** 22 documents | 0.09 s | 0.02 s | 233 | 0.4 ms | 2.9 ms |
| **C** 6 documents | 0.06 s | 0.01 s | 102 | 2.4 ms | 4.2 ms |

### Incremental ingestion is further away than the roadmap assumed

The [design](proposals/0001-the-design.md#10-roadmap) holds incremental
ingestion behind a measured trigger: *"a full rebuild passes ten seconds on the
real corpus."* At 223 documents per second that trigger arrives at roughly
**2,200 documents** for a first build.

But the case that actually matters is re-running ingest over a corpus where
little changed, and that is **5.5× cheaper** — 0.54 s against 2.98 s — because an
unchanged document skips the store write and the index update entirely. Ten
seconds of *re-ingest* is around **12,000 documents**.

So the honest position: the trigger is real, it is not close, and the number to
watch is the re-ingest one rather than the cold-build one. Nothing to build yet.

The remaining cost in the re-ingest path is reading and hashing every file. If
that ever becomes the bottleneck, the cheap fix is a size-and-mtime pre-check
before the read — worth remembering, not worth writing now.

## Retrieval, against the labelled corpus

Thirty cases, ten genres, Japanese and English, each with a planted answer and
planted adversaries. Reproduce with:

```bash
tsumugi eval            # everything
tsumugi eval --tier ci  # the fast tier CI runs
```

Seventy cases across ten genres, with **all seven trap kinds planted**: thirty
in three budget shapes, plus one each of `absent_answer`, `stale_anchor`,
`budget_squeeze` and `mixed_script` per genre.

| | all (70) |
|---|---|
| Evidence recall | **100%** |
| Evidence precision | **99.1%** |
| Lexical-near-miss trap rate | **7.5%** |
| Omission correctness | **96.7%** |
| Budget adherence | exact |
| Reproducibility | exact |

**Train and held-out agree at 10% on the trap rate**, which is what says it is
not fitted to the cases it was measured on.

### What the corpus has found so far

Three real defects, each invisible from every other angle:

| Run | Found |
|---|---|
| First | **Unconfirmed candidates entering packages**, contradicting ADR-0007 in its own words. Four commits old. |
| Same | **Confirmation weaker in English than Japanese** — a single shared word confirmed a document about something else. |
| Adding `stale_anchor` cases | **Staleness could never fire.** `build_context` compared the anchor against the *store*, which holds the text it anchored and so always matches. Nothing read the disk. True since v0.2 (see below). |
| Adding `budget_squeeze` cases | **The harness refused to load them.** A case that requires no fact but expects one to be *reported* is exactly what ADR-0005 is about, and the validation rule only knew about required and forbidden facts. The oracle caught a rule rather than a case. |

### Staleness was structurally undetectable

ADR-0010 says an anchor whose document has changed **on disk** is stale.
`build_context` checked `resolve(anchor, stored_document)` — which by
construction always resolves, because the store holds the text it anchored.
That check catches a corrupt index and nothing else.

Only the disk knows, so checking costs I/O, so it is a port:
`FreshnessCheck`, with a filesystem implementation that compares byte length
first and hashes only when that matches. A caller without a corpus to hand gets
`freshness/unchecked` recorded in the package's providers, so nobody reads "no
stale anchors" as "nothing was stale".

Omission correctness went **45% → 95%** when the check was wired in.

### One thing tsumugi cannot do, now measured

Four of ten English `absent_answer` cases still return context for a question
the corpus does not answer. That is **reported and not gated**, because it is
not a defect: a package is passages that bear on a question, and documents
about the right subject do bear on it. Saying "the corpus has no answer" is a
semantic judgement, and the instruction set leaves it to the model — *"if the
context does not answer the question, say so plainly."*

The Japanese cases return nothing, and the difference is the needle mechanism
rather than any semantic capability: an English question shares the subject
phrase with the subject's documents, and a Japanese one shares the whole query
or nothing.

### What the first run found

The corpus was built at v0.2 and found a defect four commits old on its first
run:

| | |
|---|---|
| First run | trap rate **96.7%** |
| Keeping unconfirmed candidates out of packages | 36.7% |
| Requiring a phrase rather than a token to confirm | **10.0%** |

Unconfirmed candidates were entering packages as items covering the head of
their document, which contradicts
[ADR 0007](adr/0007-index-japanese-by-bigram.md) in its own words. Nothing else
caught it: the unit tests passed, the CLI output looked reasonable, and the
packages were well-formed. It took a corpus that knew which document was right.

The full account is
[proposals/0002](proposals/0002-what-building-it-taught.md).

### The residual 10%, named

All three remaining failures confirm on a **stopword phrase**: "when is the
first ferry departure" matches a document about a shuttle bus on *the first*.
Fixing it needs term rarity — which the index has as bm25 and the confirmation
stage does not — or a stopword list, which is a vocabulary list per language and
does not generalise. Chasing it on thirty synthetic cases would be fitting the
ranker to the fixtures.

### Omission correctness: 0% → 90% → 95%, and what the 0% bought

The metric asks whether the *reason given* for an exclusion was right, and it
read **0%** on its first run. It was the first number in this project that
asked for a feature rather than permitting one, and it asked for redundancy
marking ([ADR 0008](adr/0008-redundancy-is-proposed.md)).

Building it moved the number to **90%** — and along the way showed that the
corpus's expectation had been wrong. It asserted that a *superseded* document
should be reported under `redundant_candidate`, and measurement says that is
not detectable and not what the rule means
([ADR 0015](adr/0015-redundancy-does-not-decide-which-is-right.md)). The trap
is now a verbatim copy, which is what `redundant_candidate` does mean.

Wiring in the freshness check took it to **95%**. The remaining 5% is one case
where the copy fitted the budget and was correctly *sent*, carrying a
`redundant_with:` signal rather than becoming an omission. That is ADR-0008's
rule working: redundancy lowers priority and never vetoes.

### Near-duplicate detection: what containment separates

| | containment |
|---|---|
| verbatim copy | 1.000 |
| copy inside a longer document | 1.000 |
| copy, one clause changed | 0.889 |
| copy, reflowed to a new width | 0.873 |
| *— the threshold, 0.75, sits here —* | |
| same subject, corrected value | 0.417 |
| different subject, same shape | 0.167 |
| same topic, rewritten | 0.000 |
| unrelated | 0.000 |

Copies separate from everything else by 0.456. **Corrections do not separate
from unrelated statements**, which is why a superseded version is out of scope
rather than an unmet expectation — and why redundancy marks rather than
deletes. Whether a correction *looks* like a copy turns out to depend on how
much of the passage it changed, not on it being a correction: the same
correction scores 0.42 in a sentence and 0.77 in a paragraph.

### Floors, not targets

CI checks `evidence recall >= 95%` and `trap rate <= 20%`, with budget adherence
and reproducibility exact because they are invariants. The floors are
deliberately looser than the current numbers: a gate set at today's score makes
every improvement a new floor and every honest experiment a build failure, and
tuning to reach a threshold is the failure `mamori`'s ADR-0023 records.

## The token estimator's error

[ADR 0006](adr/0006-the-budget-is-an-estimate.md) says a token budget is an
estimate and that the estimate must state how wrong it is. Reproduce with:

```bash
python tools/measure_cost.py calibrate <corpus> [<corpus> ...]
```

Fitted by least squares against `cl100k_base` over 8,591 four-hundred-character
windows of mixed Japanese, Chinese, English and source code, with **every
seventh window held out** and used only for scoring.

| Tokens per character | |
|---|---|
| Latin | 0.215 |
| CJK ideograph | 1.292 |
| Kana | 1.192 |
| Digit | 0.814 |
| Space | 0.160 |
| Other | 0.484 |
| Hangul | 1.2 — **not fitted**, see below |

**A kanji costs six times a Latin character.** That spread is the whole argument
of ADR-0006: one constant for both would be comfortable in English and blow the
context window in Japanese, which is the direction that hurts.

| Error | p50 | p95 | worst |
|---|---|---|---|
| In-sample | 0.0507 | 0.1786 | 0.766 |
| **Held out** | **0.0495** | **0.1828** | 0.612 |

The held-out row is what ships in `measured_error`, and therefore what travels
in every package. The two rows agreeing to within half a percentage point is not
evidence that the estimator is good — it is evidence that the number is not
flattered by the corpus it was fitted on. A model with seven parameters should
behave this way.

**Practical reading:** at p95 the estimate is off by 18%, so a caller who cannot
afford an overrun should set an 8,000-token budget at about 6,800 — or use
`Budget.characters()`, which is counted rather than estimated.

**Hangul was never fitted.** The calibration corpus had no Korean, so least
squares returned zero, which would say Hangul is free — and a budget that thinks
Korean costs nothing is worse than one that guesses. The weight is set by
analogy with kana and marked in the source as an assumption. The measured error
above says nothing about Korean text.

## What a real model does with a package

*Measured 2026-08-30 against `ollama/llama3.1:8b`, on the ten held-out cases.
Reproduce with:*

```bash
tsumugi eval --split held_out --model llama3.1:8b
```

**This is never a floor.** Everything above is a property of this code. This is
a property of this code *and* one local model on one afternoon, and gating on
the second kind would make the first kind negotiable. It is reported, dated and
named by model, and that is all it claims to be.

| | |
|---|---|
| answered | **9 of 10** — one call failed against the provider |
| checkable | **9 of 9** — every answer came back in the requested shape |
| grounded | **100%** — every citation resolved |
| on target | **100%** — every answer cited a planted answer |
| captured | **0%** — no answer gave an outdated passage as all the reader got |
| also cited a contradicting passage | **9** — a count, not a verdict; see below |
| cited a verbatim copy | 1 — reported, never counted as being fooled |

Nine of nine parseable is worth one line of its own, because two runs earlier
it was **0 of 50**. The same model, the same cases: what changed was a rule
saying "reply with JSON only" in words rather than only as a schema, and a
tolerance for the markdown fence models wrap JSON in anyway.

### The number that would not hold still

The interesting part of this measurement is that the first three versions of it
were wrong, in the same way each time.

Every held-out answer cites the outdated figure as well as the current one.
That is real: tsumugi carries a superseded passage on purpose
([ADR 0008](adr/0008-redundancy-is-proposed.md) marks and never removes, and
[ADR 0015](adr/0015-redundancy-does-not-decide-which-is-right.md) records the
measurement behind refusing to choose — the similarity ranges of *a correction*
and *a passage about a different subject* overlap, so no threshold separates
them). Grounding cannot catch it either: the old version really is in the
corpus, so its citation resolves.

The question is whether that is a failure, and three attempts to answer it with
a rate all produced **88%, 88%, 100% "fooled"** while the model was doing
something defensible:

1. counting `near_duplicate` citations — but a copy carries the answer's own
   content, so quoting it *is* quoting the answer;
2. counting per answer — but the instruction set asks a model to cite both when
   passages disagree, so following it scored as failing;
3. counting per claim — but a model that answers in one claim and adds the
   history in a second has still shown the reader both.

**Citations cannot say which reading a prose answer leaned on.** "It weighs
2.4kg; it previously weighed 3.1kg" and "It weighs 3.1kg; a later note says
2.4kg" cite exactly the same two spans. Telling them apart means grading the
prose against an ideal answer, which
[ADR 0013](adr/0013-label-the-evidence-not-the-ideal-answer.md) refused — and
refusing it a fourth time is cheaper than a number that sounds like it knows.

So the rate is now **captured**: the contradicting passage was *all* the reader
got. That is unambiguous, and it is 0%. The rest is a count.

Two things did change as a result, both of them prompt-side and both worth
having:

1. **The redundancy marking never reached the model.** `redundant_with:itm_001`
   was in the published JSON and not in the rendered prompt. Marking a consumer
   cannot see is not marking. `render()` now says `-- repeats itm_001`.
2. **Nothing asked what to do when two passages disagree.** The instruction set
   now does: *say so and cite both, do not quietly pick one.* Asking a model to
   surface a disagreement is not asking it to resolve one, and surfacing rather
   than deciding is what this project does everywhere else.

### What this measurement cannot say

- **It is one model.** `qwen2.5:14b-instruct` answered correctly through the
  same pipeline where `llama3.1:8b` produced fifty unreadable answers in fifty
  cases, and only running both found the regression that caused it. A number
  here without its model name is not a measurement.
- **Ten cases.** Held-out, which is the honest split to quote, and small.
- **"Grounded" is not "right".** It means every quotation was where the model
  said it was. A model can quote a corpus perfectly and reason from it badly,
  and nothing in this table would notice.
- **The unreadable answers are excluded from every rate**, and counted
  separately. An answer nobody can parse says nothing about grounding, and
  counting it as ungrounded would report a model that cannot follow an output
  contract as one that cites badly — two problems with two different fixes.

## What these numbers are not

- **Not a benchmark against anything else.** No other tool was run.
- **Not representative of a personal notes corpus.** Corpus A is source code and
  developer documentation; the largest CJK-heavy sample available here was six
  documents. The extrapolation above is an extrapolation, and is labelled as one.
- **Measured against one tokenizer.** `cl100k_base` says little about a model
  that tokenizes differently. Naming it is honest rather than sufficient.
- **One machine, one SSD, one SQLite build.** Timings on spinning disk, on a
  network drive, or on a SQLite compiled differently will differ.
- **Not a measure of whether an answer is correct.** Retrieval finding the right
  passage says nothing about what a model then does with it. The model section
  above measures whether an answer was *anchored*, which is a smaller and
  different question: a model can quote perfectly and reason badly.
- **Seventy cases is a fourteenth to two-thirds of what
  [evaluation-corpus.md](evaluation-corpus.md) describes.** All seven trap
  kinds are planted; what is thin is the number of genres and the variety
  within each.
- **The index and cost numbers above are not gated.** Retrieval is: CI runs the
  `ci` tier and fails below its floors. Index size and ingest speed are re-run
  by hand.
