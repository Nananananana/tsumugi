# The evaluation corpus

**Status:** proposed — the format below is decided
([ADR 0013](adr/0013-label-the-evidence-not-the-ideal-answer.md)); nothing is
built.

*The one-line version: **the corpus is generated, the labels are computed, and
nothing labels the ideal output.** Every metric is arithmetic over anchors.*

---

## A case

One case is a folder: a small synthetic corpus in one genre, a question, and the
evidence that answers it.

```text
cases/ja-0142-mountaineering/
├── case.json
└── corpus/
    ├── 2025-04-12-装備メモ.md
    ├── 2025-06-03-装備メモ-改訂.md      # supersedes the above
    ├── 2025-05-20-山行記録.md
    ├── 2024-11-08-キャンプ道具.md        # lexical near-miss
    └── README.md
```

Facts are planted with inline markup. **The loader strips the markup and computes
the offsets** — hand-written offsets are wrong often enough that a corpus
annotated that way measures the annotator rather than the system. This is
`mamori`'s dataset convention, and it is taken for the same reason.

```markdown
## 装備

{{F:tent-weight}}テントは 2.4kg、二人用{{/F}}。前回より 300g 軽い。
{{F:stove-fuel}}ガスは 250g カートリッジを 1 本{{/F}}。
```

```json
{
  "case_id": "ja-0142-mountaineering",
  "genre": "mountaineering",
  "language": "ja",
  "question": "テントの重量は?",
  "budget": {"unit": "tokens", "limit": 800},

  "must_include":     ["tent-weight"],
  "must_not_include": ["tent-weight-old", "camp-tent-weight"],

  "traps": {
    "tent-weight-old":  {"kind": "superseded",
                         "expect_omission_rule": "redundant_candidate"},
    "camp-tent-weight": {"kind": "lexical_near_miss",
                         "expect_omission_rule": "below_threshold"}
  },

  "split": "train"
}
```

`expect_omission_rule` is the field that makes this dataset worth more than a
retrieval benchmark. It does not ask whether the trap was excluded — it asks
**whether the stated reason was right**. A system that excludes the superseded
document under `below_threshold` got the right answer for the wrong reason, and
that is a ranking bug that will surface later on a case where the outcome also
goes wrong.

---

## What is measured

All six are arithmetic. No grader, no model, no rubric.

| Metric | Definition |
|---|---|
| Evidence recall | `must_include` facts whose span is inside some `items[]` anchor |
| Evidence precision | `items[]` overlapping a labelled fact, over all `items[]` |
| Trap rate | `must_not_include` facts that reached `items[]` |
| **Omission correctness** | excluded facts whose `omissions[]` entry names the expected rule |
| Budget adherence | `sum(item.cost) <= budget.limit` — an invariant, so any failure is a bug |
| Reproducibility | same case run twice, one `package_id` |

Reported per genre and per language as well as in aggregate. An aggregate hides
that the ranker is excellent on Japanese prose and useless on source code, and
those are different problems.

---

## The traps

The genres are decoration. **The traps are the dataset**: a generated corpus of
relevant documents measures nothing, because every retriever passes it.

| Kind | The corpus contains | Catches |
|---|---|---|
| `lexical_near_miss` | Same vocabulary, different subject — camping gear when the question is about mountaineering gear | Retrieval precision. Bigram indexing over-generates by design ([ADR 0007](adr/0007-index-japanese-by-bigram.md)), so this is the confirmation stage's exam |
| `near_duplicate` | The answer, restated almost identically elsewhere | [ADR 0008](adr/0008-redundancy-is-proposed.md): must be *marked*, still considered, never silently deleted |
| `superseded` | 94% identical to the answer; the differing 6% is the correction | Whether the later document wins — and whether the older one is *reported* rather than vanishing |
| `budget_squeeze` | A required fact ranked below where the budget cuts | [ADR 0005](adr/0005-selection-is-a-report.md): must appear in `omissions[]` under `budget_exhausted`, not disappear |
| `absent_answer` | Nothing answers the question | Does the package say so, or assemble something plausible |
| `mixed_script` | Japanese prose, English terms, code blocks in one document | [ADR 0007](adr/0007-index-japanese-by-bigram.md) tokenization, [ADR 0006](adr/0006-the-budget-is-an-estimate.md) cost estimation |
| `stale_anchor` | A document edited after ingest | [ADR 0010](adr/0010-the-index-stores-the-text.md): `stale_anchor`, never a silent re-anchor |

`superseded` is the trap most worth getting right and the easiest to get wrong. Two
paragraphs that are 94% identical are usually 94% identical *because the
interesting 6% is a correction* — which is exactly why redundancy is marked rather
than purged.

---

## Generation

**A model runs at authoring time. Never at test time.**

```bash
tools/generate_cases.py --genres 200 --per-genre 3 --seed 20260830 --out tests/cases/
```

Committed as fixtures. CI reads files and calls nothing. This follows from
[ADR 0003](adr/0003-a-package-is-reproducible.md) — a dataset that varies between
runs cannot detect a regression — and from
[ADR 0001](adr/0001-the-domain-depends-on-nothing.md): evaluating a
zero-dependency library must not require a GPU.

The generator writes the corpus **and** the labels in one pass, because it knows
where it planted each fact. Labelling is a by-product, not a second job. That is
what makes 100–1000 cases affordable when hand-authoring 100 would not be.

Genres are drawn broadly on purpose — cooking, employment regulations, research
notes, legal memos, hiking logs, medical appointments, code review notes, meeting
minutes, travel plans, gardening — because a selector tuned on one register learns
that register's shape.

### The self-check

**Every generated case is verified by an oracle before it is committed.** The
oracle reads the labels and constructs the package the labels demand: if it cannot,
the case is broken and is discarded rather than shipped.

This is not optional and it is not cheap. A generator that plants a trap wrongly
produces a case that fails a *correct* implementation, and that failure is
expensive precisely because the instinct is to go looking in the code.

Also checked mechanically before commit:

- Every `must_include` fact is reachable by the index at all
- Every trap is genuinely excluded by its stated rule
- No case is trivially solvable — a corpus where the only document is the answer is
  deleted
- **No real personal data.** Every sample is invented; these files ship inside the
  package, so a real name, address or key committed here is published to everyone
  who installs tsumugi. The generator is instructed, and a test greps the fixtures
  for credential and contact shapes regardless. Trusting the instruction alone
  would be trusting a model with a security property.

---

## Tiers and splits

| Tier | Size | When |
|---|---|---|
| `ci` | ~60 cases | Every commit. Seconds |
| `full` | 100–1000 | Nightly, and before a release |
| `held_out` | ~15% of `full` | Never read while tuning |

The two tiers exist because a suite too slow to run is a suite that decays — the
same reasoning that keeps the domain layer dependency-free.

`held_out` exists because a ranker tuned against every case it will be scored on is
a ranker fitted to the dataset. It is scored at release and the number is published
next to the tuned number. If they diverge, the tuned number was fiction.

---

## The half this refuses to measure

Whether a rendered prompt is *good* — well-ordered, well-sectioned, pleasant for a
model to read — is a real question, and this dataset does not answer it. There is
no single ideal structured prompt for a question, so scoring distance to a chosen
one measures conformity and punishes any improvement that looks different.

That question is kept separately and kept small: roughly twenty hand-authored
cases, judged by **comparing two renderings against each other**, never against an
ideal. `mamori`'s `--compare` prints the individual samples that changed rather
than an aggregate, for the reason that tuning against an aggregate fits a prompt to
a number instead of to a corpus.

Twenty, not two hundred. A subjective set that grows starts to be mistaken for an
objective one.

---

## Eight document shapes, because one shape measures one shape

Every document in this corpus used to be the same thing: front matter, one
heading, one sentence carrying the fact, one filler block. A ranker could have
been keying on *"the fact is the first sentence after an H1"*, scored 100%
recall, and learned nothing that survives a real notes folder.

So a document now takes one of eight shapes, chosen deterministically from the
genre, the variant and the document's **role** — the answer and its adversaries
never share a shape in one case, or "the fact is in the differently-shaped
document" would become a signal and the corpus would be measuring that.

| shape | what it stresses |
|---|---|
| `article` | the original: front matter, H1, fact, filler |
| `bare` | no front matter, no heading — a note somebody typed |
| `buried` | mid-paragraph, no blank line either side |
| `table` | the fact as a markdown table row |
| `bullets` | one bullet among several |
| `nested` | H1 → H2 → H3, fact under the deepest |
| `log` | a timestamped speaker line |
| `trailing` | the last line, and no trailing newline |

Borrowed in spirit from `mamori`, whose evaluation data is split by the *kind*
of text as well as by language — fragments, documents, conversations, tool
payloads — because those stress different things and one set measures one of
them.

**Turning it on cost four points of recall and thirteen of omission
correctness on the first run**, and every one of them was worth reading:

1. **`_widen` stopped at a line break** while its own docstring said "sentence
   boundary". On one-sentence-per-line fixtures those are the same thing. A
   fact planted mid-paragraph produced an item three times too big, which lost
   a tight budget and took the case with it. Fixed in the library.
2. **A squeeze case was ill-posed.** Its budget fit two of four passages, which
   asked tsumugi to rank the current answer above the superseded one — the
   thing [ADR 0015](adr/0015-redundancy-does-not-decide-which-is-right.md)
   measured and refuses to do. It passed anyway, because every answer document
   carried front matter repeating its heading and won on bm25. **The corpus was
   rewarding a structural artefact.**
3. **Two scoring rules named one of two identical passages.** Where a case
   plants the same sentence twice, which copy survives a tie depends on
   document length and heading repetition. Recall now accepts the fact
   delivered by either, and the redundancy expectation accepts the rule firing
   either way — as an omission when the budget binds, or as a `redundant_with`
   mark when it does not. Both are ADR-0008; a case that accepted only the
   first was testing the budget rather than the rule.

After all three: recall unchanged at 91.7%, trap rate **6.0% → 4.0%**, omission
correctness **96.7% → 100%** — with eight times the structural variety.

---

## The shape the corpus could not see

Every question in this corpus was generated from the **subject and attribute
the document uses**. So every question shared a contiguous phrase with its own
answer — and confirmation is a phrase match ([ADR 0007](adr/0007-index-japanese-by-bigram.md)).

Seventy cases at 100% recall could not see that a question worded any other way
finds *nothing*:

```
テントの重量は?          1 item      the phrase the document uses
テントの重さは?          0 items     重量 -> 重さ
テントはどれくらい重い?   0 items     the way a person asks
```

The index proposes the right document every time. Confirmation rejects it,
because no substring of the question appears in the text.

A `-paraphrase` case per genre now plants exactly that: the documents are
unchanged and only the wording of the question moves, which is what makes it
measure the confirmation stage rather than the ranker. They are `full` tier and
`held_out`, so CI stays green on the `ci` tier while the number is visible to
anyone who runs the whole corpus — reported rather than gated, on the same
terms as the unanswerable-question residual below.

**8 of 10 fail today.** That number is the point of adding them.

---

## The half that now runs, opt-in

`tsumugi eval` printed one sentence from the day the corpus existed:

> Nothing here measures whether an answer built from a package is correct.

That was true because the pipeline stopped at a rendered prompt. It no longer
does, so `--model NAME` puts every case to a local model and reports four
things, kept deliberately apart:

| | Says |
|---|---|
| **grounded** | every citation resolved. Not that the answer is right — a model can quote perfectly and reason badly |
| **on target** | a resolved citation landed inside a *planted answer*: retrieval and the answer agreed |
| **trapped** | a resolved citation landed inside a planted *adversary*. Grounding cannot catch this: the superseded version really is in the corpus |
| **abstention** | on `absent_answer` cases only — did the model decline, as it should? |

The fourth is the one the deterministic half genuinely cannot reach. tsumugi
**reports** that a corpus may not answer a question and does not gate on it,
because "there is no answer here" is the model's call to make. Until there was
a model, that decision had no test at all; now the cost of it is a number.

**It is never a floor.** The deterministic scores are a property of this code.
These are a property of this code *and* whichever model somebody happened to
pull, and putting a gate on the second kind would make the first kind
negotiable. Results are dated and named by model in
[measurements.md](measurements.md), like everything else here.

Both halves materialise a case through the same `prepared_case`. Two ways to
build a case's index would be two ways for a case to mean slightly different
things, and the difference would surface as a model looking better or worse
than it is.

---

## What the corpus cannot tell you

Stated here rather than discovered later:

- **Synthetic notes are tidier than real ones.** Consistent headings, sensible
  dates, no half-finished sentences, no note that is three words and a URL. A
  ranker tuned here is tuned for tidiness. The ledger over a real corpus
  ([ADR 0011](adr/0011-record-what-was-sent-and-what-was-used.md)) is the
  counterweight, and it is a counterweight rather than a fix.
- **The genre mix is arbitrary** and matches nobody's actual corpus. Per-genre
  reporting keeps this in view.
- **A model's score is a fact about that model on that day.** The opt-in half
  above measures a pair — this code and one local model — and neither half of
  the pair is the subject on its own. A number quoted without its model name
  and date is not a measurement.
- **Judgement was relocated, not removed.** Someone decided that a given distractor
  is a trap rather than a legitimate second answer. That decision now sits in the
  fixtures — more visible and more reviewable than a grader, and still a judgement.
