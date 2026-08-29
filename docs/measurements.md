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

## Search quality, as far as this measures it

Twelve queries, mixed Japanese and English. On corpus A one query returned
nothing; on B, two. Both are correct answers — those repositories do not discuss
those topics — but a count of empty results is **not a quality measurement**, and
nothing here should be read as one.

Retrieval quality gets measured properly against the labelled corpus in
[docs/evaluation-corpus.md](evaluation-corpus.md), which is not built. Until it
is, the only claims this project makes about retrieval are the six hit counts in
[ADR 0007](adr/0007-index-japanese-by-bigram.md).

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

## What these numbers are not

- **Not a benchmark against anything else.** No other tool was run.
- **Not representative of a personal notes corpus.** Corpus A is source code and
  developer documentation; the largest CJK-heavy sample available here was six
  documents. The extrapolation above is an extrapolation, and is labelled as one.
- **Measured against one tokenizer.** `cl100k_base` says little about a model
  that tokenizes differently. Naming it is honest rather than sufficient.
- **One machine, one SSD, one SQLite build.** Timings on spinning disk, on a
  network drive, or on a SQLite compiled differently will differ.
- **Not tracked over time yet.** There is no regression gate on any of this. When
  one exists it will be in CI, and this file will say so.
