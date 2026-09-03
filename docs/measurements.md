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

## Script-aware bigramming: 12–20% fewer terms, and almost no smaller a file

Prompted by reading what other people do with FTS5 and CJK: several
practitioners bigram only the ideographic runs and let kana pass through.
Measured here across four variants, on the evaluation corpus, train and
held-out agreeing:

| bigrammed | terms/char | recall | precision | traps |
|---|---|---|---|---|
| ideographs + all kana + hangul *(was)* | 0.40 | 92.6% | 96.5% | 2.9% |
| **ideographs + katakana** *(is)* | **0.32** | 92.6% | 96.5% | 2.9% |
| ideographs only | 0.32 | 91.9% | 96.4% | 2.9% |

**Hiragana is grammar** — particles and inflection — so its bigrams are an
index's most frequent terms and its least discriminating. **Katakana is not**:
it writes loan words and they concatenate, so `スポーツクラブ` has to be
findable by `スポーツ`, and the third row is what dropping it costs.
**Hangul is spaced**, so a run is already a word.

The same change on three real corpora, before and after:

| corpus | terms/char | index ÷ corpus |
|---|---|---|
| Japanese working notes | 0.41 → **0.36** | 4.61× → 4.49× |
| this repository (`src` + `docs`) | 0.13 → 0.13 | 3.02× → 3.02× |
| the evaluation fixtures | 0.24 → **0.22** | 8.16× → 8.09× |

**The term count falls 12–20% on CJK text and the file barely moves.** That is
the honest result and it is not the one the change was made for: at these
sizes the index is dominated by the *stored text*
([ADR 0010](adr/0010-the-index-stores-the-text.md)) and by SQLite's page
granularity, so a fifth off the term list rounds away. The saving is in term
count, which is what grows with a corpus, not in the file as measured on a
corpus this small.

It also found a defect. Once hiragana stopped being bigrammed it stopped being
its own script for run-splitting, so `tsumugiは予算` indexed `tsumugiは` as one
term — a token no query could produce. Runs now break at **every** change of
script class rather than at the boundary of what gets bigrammed.

## Speed

| | First ingest | Re-ingest, unchanged | Documents/second | Search p50 | Search p95 |
|---|---|---|---|---|---|
| **A** 666 documents | 2.98 s | **0.54 s** | 223 | 2.5 ms | 11.7 ms |
| **B** 22 documents | 0.09 s | 0.02 s | 233 | 0.4 ms | 2.9 ms |
| **C** 6 documents | 0.06 s | 0.01 s | 102 | 2.4 ms | 4.2 ms |

*Re-measured 2026-08-31, and the corrections are worth more than the numbers.*

| corpus | documents | chars/doc | first ingest | docs/s | re-ingest | ratio |
|---|---|---|---|---|---|---|
| `mamori` + `kiseki` working copies | 1,877 | 6,811 | 31.58 s | 59 | 1.55 s | **20.4×** |
| `tsumugi/tests/cases` | 1,020 | 295 | 2.24 s | 455 | 1.05 s | **2.1×** |

**Documents per second is not a unit.** It moves 7.7× between those two,
because one corpus has documents twenty-three times the size of the other's.
The ratio moves with it: 0002 has said **5.5×** since it was written, and these
give 20.4× and 2.1×.

**A is the same pair of repositories as row A above, and it now holds 1,877
documents rather than 666.** So the defect in the table is not that its corpora
are unnamed — they are named, three sections up, with their document counts and
character counts. It is that **a name is not a pin.** `mamori` + `kiseki` in May
and `mamori` + `kiseki` today are different corpora sharing a label, and every
row measured against that label reproduces something else.

**And then the same corpus gave a different answer an hour later.** Re-running
`tsumugi/tests/cases`, with `git diff` over `src/` between the two runs empty:

| | documents/second | re-ingest |
|---|---|---|
| the run in the table above | 455 | 1.05 s |
| three runs an hour later | 571, 558, 570 | 0.64, 0.63, 0.63 s |

Three consecutive runs agree to **±1%**, and that is what makes it dangerous:
inside a batch the number looks precise, between batches it moves 25%. What
changed is the machine, which is running seven other agent sessions and a GPU
workload. **A repeated measurement is evidence that conditions held during the
batch, and evidence of nothing else.**

`tools/measure_index.py` now repeats the re-ingest and prints a band rather
than a number, with the caveat **above** it rather than below — a reader who
has already seen a figure has finished forming an opinion about it. It also
says what the band is not: repeating within one batch measures how still the
machine held for a minute, not how still it holds for a day, and those differ
here by a factor of ten (0.01s within a batch against 0.42s across an hour).

`--against` a previous `--json` run reports which of the three states applies,
and it was worth building for one reason: **run against a baseline taken
minutes earlier with nothing edited, it reported a difference larger than the
spread.** Twice. So the within-batch spread is not a floor for anything
measured across batches — the caution above, restated as a result. The tool
says so in the sentence rather than leaving the reader to notice.

The helper that decides whether a difference is real returns `bool | None`,
because **`False` has to mean *this run cannot tell*, not *no improvement***.
Written as a `bool` it lets a reader hear the stronger thing. The three states
are `bench`'s, arrived at from the other direction.

So none of the speed figures on this page are properties of the code alone —
including the ones taken today to correct the ones taken before. `bench` holds
a lease for the GPU for exactly this reason. There is no equivalent for the
CPU, and these were taken without one.

### Incremental ingestion is further away than the roadmap assumed

The [design](proposals/0001-the-design.md#10-roadmap) holds incremental
ingestion behind a measured trigger: *"a full rebuild passes ten seconds on the
real corpus."* At 223 documents per second that trigger arrives at roughly
**2,200 documents** for a first build.

But the case that actually matters is re-running ingest over a corpus where
little changed, because an unchanged document skips the store write and the
index update entirely. Ten seconds of *re-ingest* is around **12,000
documents**.

**That last number survived re-measurement and it is the only one that did.**
Re-ingest ran at 1,211 documents/second on the large corpus and 971 on the
small one, which put ten seconds at 12,100 and 9,700 documents — the same place
0002 put it, from a different corpus, by a different route. It is stable
because re-ingest is dominated by per-document work (stat, read, hash) rather
than by document size, which is exactly why it is the number worth watching.

So the honest position is unchanged and its arithmetic is not: the trigger is
real, it is not close, and nothing needs building yet. **The cold-build trigger,
however, has been crossed** — 1,877 real documents take 31.6 seconds, against
the design's ten. It bought nothing: a first build is a first build, and
incremental ingestion does not make one faster.

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

### Ten genres was not a corpus, it was a mirror

The corpus went from 10 genres to 30, in four languages instead of two, with
eight document shapes instead of one. **No code changed**, and:

| | 10 genres | 30 genres |
|---|---|---|
| evidence recall | 91.7% | 82.6% |
| evidence precision | 99.1% | 93.9% |
| trap rate | 6.0% | **25.8%** |

Ten genres written by whoever was writing the ranker is ten genres whose
vocabulary was chosen — without anyone intending it — to suit the ranker.
Twenty more, drafted by a local model and reviewed rather than authored, are
not. Everything above this line in this document was measured on the mirror.

Recovering from it took four changes to the confirmation stage
([ADR 0019](adr/0019-confirmation-is-relative.md)), the largest being that a
match much weaker than the strongest found for the same query is no longer
treated as evidence:

### The thing it cannot say, and the threshold that will not fix it

`tsumugi eval` reports it in its own output: **13 unanswerable questions still
returned context.** All thirteen are English, all are genuinely
`confirmed_in_text`, and the mechanism is specific — an English question splits
into sub-phrases, so `the coverage period` can confirm against a document that
never mentions the thing being asked about. A Japanese question with no spaces
yields one needle and confirms all-or-nothing, which is why none of the
thirteen are Japanese.

**How much of the question was confirmed separates them cleanly:**

| | cases | median share confirmed |
|---|---|---|
| answerable | 124 | **0.91** |
| unanswerable | 13 | **0.44** |

Which makes a threshold look obvious, and it is not:

| cut | unanswerable silenced | **answerable lost** |
|---|---|---|
| 0.5 | 11 of 13 | **51 of 157** |
| 0.6 | 12 of 13 | 56 of 157 |
| 0.7 | 13 of 13 | 57 of 157 |

**Thirty-two percent of correct answers, to suppress eleven false ones.**
ADR-0018 refused a stopword list and ADR-0019 refused an absolute bar, both on
the argument that a threshold tuned on one corpus does not transfer; this is
the same refusal with a price on it.

So the share is **reported and not thresholded**. Every confirmed item now
carries `confirmed_share:0.44` beside `confirmed_in_text`, and a consumer with
their own corpus and their own tolerance can cut where they like. The library
does not guess on their behalf, and the number above is why.

### What external libraries are worth here *(measured 2026-09-01)*

The zero-dependency rule was relaxed for everything outside the domain, which
reopens three things earlier ADRs refused **for dependency reasons rather than
for evidence**. So they were measured rather than adopted.

**Segmentation does not recover the residual.** ADR-0007 chose bigrams and named
its own condition: *if the golden retrieval dataset ever shows this costing real
recall, it comes back as an optional adapter*. Of the 23 cases the lexical stage
misses:

| terms from | confirm the answer | a rival also confirms |
|---|---|---|
| character bigrams (today) | 0 of 23 | 12 |
| `janome` (Japanese morphology) | **0 of 23** | 7 |
| `jieba` (Chinese words) | **2 of 23** | 7 |

The condition looked met — Chinese recall is 83.3% — and the measurement says
the diagnosis was wrong. **These are paraphrases, not mis-segmentations**: the
question uses different words, and no boundary rule finds a word that is not
there. A segmenter fixes where words end, and the residual is about which words
were chosen.

**A cross-encoder recovers them and cannot be trusted to gate them.**
`BAAI/bge-reranker-base`, the multilingual reranker rival libraries reach for:

| | |
|---|---|
| of the 23 misses, answer ranked first | **17** — better than embeddings' 15 |
| of the 120 trap cases, **the forbidden document ranked first** | **10 (8.3%)** |
| of the 120 trap cases, the answer ranked first | 53 (44.2%) |

**This library's trap rate is 4.2%.** Letting the reranker decide what is sent
would roughly double it while ranking the answer first in under half the cases.
ADR-0022 refused to carry an item nothing lexical confirms; a better model does
not overturn that, it prices it — seventeen paraphrases, declined.

So the reranker ships where being wrong costs an *ordering* rather than a false
citation: `--ordering rerank` reorders candidates that confirmation has already
accepted. Reproduce with `tools/measure_segmenters.py` and the reranker figures
in `docs/adr/0025-outside-the-domain-a-library-may-help.md`.

### Does a different ordering help? *(measured 2026-09-01)*

`fit_to_budget` fills a budget best-first, and *best* meant descending score.
That has a measurable weakness here — **113 of 240 packages contain two items
sharing a twelve-character window** — so the ordering became a parameter, with
Maximal Marginal Relevance (Carbonell & Goldstein, SIGIR 1998) as the
alternative: relevance traded against novelty, using the character-shingle
similarity already used for the duplicate marks.

**It changes almost nothing here, and that is the finding:**

| | |
|---|---|
| packages whose **contents** differ | **5 of 240** |
| packages whose **order** differs | 20 of 240 |
| packages where the budget actually bound | 32 of 240 |
| recall, precision, trap rate | **identical to three decimal places** |

Two reasons, and neither is that MMR is broken — a unit test holds it demoting
a near-duplicate below a distinct passage, and `diversity=1.0` reduces exactly
to the score ordering.

**The budget rarely binds.** In 208 of 240 cases everything that confirms also
fits, so the order things are tried in cannot change what is sent.

**And bm25 has usually separated the duplicates already.** For an ordering to
matter, a near-duplicate has to rank *directly behind its twin*, and scoring by
term frequency over documents of different lengths usually puts something else
between them. The arrangement MMR is for is one the earlier stages mostly
prevent.

So the default stays `score`, which is what every number on this page was
measured on, and `mmr` is available for corpora that are not this one —
**named in the settings rather than chosen for everybody on the strength of a
1998 paper and no local evidence.**

Reproduce: `python tools/measure_baselines.py`.

### How much of this is free? *(measured 2026-09-01)*

`manager` asked `iriguchi` how much of its headline was just getting the
majority class right, and the answer overturned two published wins. **The same
question had never been asked here**, so `tools/measure_baselines.py` asks it
against two baselines that would be embarrassing to lose to:

| | recall | trap rate |
|---|---|---|
| `first_fit` — fill the budget in corpus order, **never read the question** | 70.0% | 75.8% |
| `no_confirm` — the index's candidates, no confirmation stage | 63.9% | 67.5% |
| **tsumugi** | **87.2%** | **4.2%** |

**Seventy of the eighty-seven points are free.** A baseline that does not read
the question at all gets most of the recall, because a case has five documents
and a budget that fits several — that is the corpus's shape, not retrieval.
The recall headline has been reported as though it were the library's, and
about a fifth of it is.

**The trap rate is where the work is:** 75.8% to 4.2%, eighteen-fold. That is
the number this library earns, and it is the one the design is about — ADR-0007
over-generates and lets confirmation decide, and this is the measurement of what
that decision buys.

**And `no_confirm` scores *worse* than never retrieving at all** — 63.9%
against 70.0%. The index's top candidates fill the budget with documents that
share words with the question and do not answer it, where corpus order stumbles
onto the answer more often. Retrieval without confirmation is not a weaker
version of this library; on this corpus it is worse than nothing, which is the
sharpest evidence for the two-stage design that has been produced here.

Reproduce: `python tools/measure_baselines.py`.

**All 240 cases** — `tsumugi eval` with no `--tier`. Stated because it was not,
and the omission cost an afternoon: reading this table beside the sentence
below it, `--tier full` looks like the way to reproduce it, and `--tier full`
reports **74.4%**. That is not a regression and never was — it is the 90 hard
cases, where the 23 paraphrase misses land, and it has read 74.4% since the day
this table was written. Confirmed by building a worktree at that commit and
running it.

A number without its population is a number that will be compared against the
wrong thing, by whoever reads it next — including its author, four days later,
who spent a bisect proving his own library had not broken.

| | before | after |
|---|---|---|
| evidence recall | 82.6% | **87.2%** |
| evidence precision | 93.9% | **97.4%** |
| trap rate | 25.8% | **3.3%** |
| omission correctness | 93.1% | **100%** |

Reproduce: `tsumugi eval`. The three tiers, so a reader can pick the right one:

| | cases | evidence recall |
|---|---|---|
| `tsumugi eval` (everything) | 240 | 87.2% |
| `--tier ci` (the gated floor) | 150 | 100% |
| `--tier full` (the hard cases) | 90 | 74.4% |

The `ci` tier is 149 of 150 clean at 100% recall. The residual is the
`-paraphrase` cases below, and Chinese, which has no script boundary between
its content and its grammar and so cannot be helped by any rule that refuses a
dictionary.

### Split by who chose the vocabulary

The headline above mixes two populations, and the split is the whole point.
Every case now carries an `origin` — `handwritten` for the ten genres written
by whoever was writing the ranker, `drafted` for the twenty a local model wrote
and a person reviewed — and `tsumugi eval` reports the trap rate for each.

With the ADR-0019 fix disabled, which reconstructs the state that produced the
headline number:

| | handwritten | drafted |
|---|---|---|
| all four languages | **4.0%** (2/50) | **28.0%** (28/100) |
| Japanese and English only | **4.0%** (2/50) | **33.3%** (15/45) |

**On the vocabulary its author chose, the ranker looked fine. On somebody
else's, it was broken seven ways out of eight.** The second row removes the
obvious confound — every Chinese and Korean genre is drafted, so the first row
cannot separate "another person's words" from "a script the tokenizer had not
met". Restricting to the two languages that have both origins makes the gap
*wider*, so it is vocabulary and not script.

With the fix in place both fall to roughly the same place — drafted 3.0%,
handwritten 4.0% — which is what fixing it was supposed to mean.

**What this is not.** It is a reconstruction, not the original run. The
historical 6.0% → 25.8% was measured on a smaller corpus, before Chinese
existed, and with document shapes changing at the same time. The numbers above
are today's 240 cases with one constant flipped, which is the cleaner
experiment and a different one.

**What this does not say:** that thirty genres is enough. It is three times
what there was, and the last time the corpus tripled it found a 20-point defect.

### A question asked in other words: 0% to 100%

Measured by asking one document three ways:

```
テントの重量は?          1 item      the phrase the document uses
テントの重さは?          0 items     重量 -> 重さ
テントはどれくらい重い?   0 items     the way a person asks
```

The index proposed the right document every time; confirmation rejected it,
because it was a phrase match and a paraphrase shares no phrase. **In the
library's primary language.**

Seventy cases at 100% recall could not see it: every question in the corpus was
generated from the subject and attribute its own document uses, so the corpus
was measuring a questioner who had already read the answer. A `-paraphrase`
case per genre now plants the real thing.

The fix is content-term coverage
([ADR 0018](adr/0018-confirm-a-paraphrase-by-coverage.md)), swept on train and
confirmed held-out:

| | before | after |
|---|---|---|
| evidence recall | 86.7% | **91.7%** |
| evidence precision | 99.1% | 99.1% |
| trap rate | 6.0% | 6.0% |

Five points of recall for nothing measurable. The `ci` tier is 100% / 100% /
5.0% either way, which is why it took a new case shape to find at all.

**What it does not fix:** a question using words the document does not contain.
`テントは何キロ?` against `2.4kg` still finds nothing, and reaching it needs
half-coverage, which the corpus measured at a 28.6% trap rate — five times the
ceiling. That is lexical retrieval's boundary, and crossing it wants embeddings.

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

## Would embeddings close the residual? Measured before building anything

[Proposal 0003](proposals/0003-what-running-it-taught.md) leads with an
optional embedding candidate source, because the literature is consistent that
dense retrieval wins exactly where lexical loses: paraphrases with no keyword
overlap. That is a claim about tsumugi's residual, so it was measured against
tsumugi's residual before any of it was built.

For every case lexical retrieval **misses**, the question and each document
were embedded and ranked by cosine. Does the answer document come first?

| embedding model | recovered |
|---|---|
| `bge-m3` (multilingual, 1024-d) | **15 of 23** |
| `nomic-embed-text` (English-first) | 13 of 23 |

Two models, because [one model is a fixture with a bigger
vocabulary](#what-a-real-model-does-with-a-package). They agree on the shape
and differ on which cases, which is the useful part.

**Where it works:** every English and Japanese paraphrase case. `bge-m3` ranks
the answer first on all twelve.

**Where it does not, and this is the finding:** Chinese and Korean, where the
*near-miss* wins. `neighbour.md` states the same attribute about a different
subject, and a sentence embedding of a short document scores that as more
similar than the answer. Eight of the eight failures are this.

So an embedding source dropped in without confirmation would recover a dozen
paraphrase cases and **spring near-miss traps in the two languages that
currently have none** — which is precisely why ADR-0007 says the index may get
smarter and confirmation may not be skipped, and why
[proposal 0003](proposals/0003-what-running-it-taught.md) proposes carrying
similarity-proposed items *marked* rather than silently.

### ...and would confirmation keep any of them? *(added 2026-08-30)*

The number above says similarity finds them. It does not say the pipeline could
use them, and that is a separate measurement:

| | |
|---|---|
| embeddings rank the answer first | **15 of 23** |
| ...and survive confirmation unchanged | **0 of 23** |
| answer covers more of the question than every rival | **6 of 23** |

**The middle row retired a roadmap item.** Proposal 0003 led with *"an
embedding candidate source, fused, with confirmation unchanged"* — and with
confirmation unchanged it recovers nothing at all. Unconfirmed results never
enter a package (`build_context` drops them with a declared omission), so a
candidate recovered by similarity is recovered into the bin. The clause written
as the item's safety guarantee was a description of the feature being absent.

**Most of that row is tautology and is reported as such:** these are lexical
misses and confirmation is lexical. But retrieval and confirmation are
different rules — bm25 over bigrams against coverage of content terms — so a
document can fail one and pass the other, and the result could have been 3 or
5. It pins a number where reasoning would have said "near zero".

**The third row is the one that was not predictable.** 6 of 23 is the *ceiling*
for lowering `COVERAGE_THRESHOLD`, and the ceiling is not reachable, because in
the other 17 the rival covers as much as the answer or more — often exactly:

| case | answer | best rival |
|---|---|---|
| `zh-sports-club-logistics` | 0.42 | 0.42 |
| `zh-warranty-terms` | 0.56 | 0.56 |
| `zh-medical-appointment` | 0.00 | **0.18** |

No threshold separates a tie. And the last row is a case embeddings get
*right*, where relaxing the threshold would admit the adversary and still
exclude the answer — the trap, bought with the fix.

So the open question is not which threshold. It is whether a package may carry
an item that nothing lexical confirms, and say so — a change to what a package
means, and an ADR before it is a line of code.

Reproduce: `tools/measure_embeddings.py` (needs ollama and an embedding model).

## What a real model does with a package

### The models these numbers name

**A tag is a moving pointer.** `ollama` tags are not digests, so
`qwen2.5:14b-instruct` can hold different weights next month and nothing in a
figure recorded against the tag would say so. Digests as of 2026-09-01, on this
machine:

| tag | digest |
|---|---|
| `llama3.1:8b` | `46e0c10c039e` |
| `qwen2.5:14b-instruct` | `7cdf5a0187d5` |
| `qwen2.5:14b-instruct-q4_K_M` | `7cdf5a0187d5` |
| `bge-m3` | `790764642607` |
| `nomic-embed-text` | `0a109f422b47` |

The last two rows of the first three are **the same digest**: those two tags
are one set of weights, so a result quoted against one is a result about the
other. That is worth knowing before anyone reads them as two data points.

Recorded because the family listed "a hosted model changing behind a fixed
model id" as a floor nobody measures, and the local equivalent is one command
away. Nothing here re-checks the digest at run time; this is a note about when
these numbers were taken, not a guarantee about when they are read.

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
