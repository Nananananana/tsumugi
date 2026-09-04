# ADR-0018: A paraphrase confirms by coverage, not by phrase

*Accepted 2026-08-30. Extends [ADR 0007](0007-index-japanese-by-bigram.md).*

## The question

ADR-0007 made retrieval two stages, and the split is the design: the bigram
index over-generates, and **confirmation against the anchored text is what
turns a candidate into a result.** Confirmation was a phrase match — for a
query written without spaces, which is most Japanese, the whole query is one
needle.

That works exactly as well as the questioner's memory of the document's
wording:

```
テントの重量は?          1 item      the phrase the document uses
テントの重さは?          0 items     重量 -> 重さ
テントはどれくらい重い?   0 items     the way a person asks
```

The index proposed the right document every time. Confirmation rejected it.

**Seventy cases at 100% recall could not see this**, because every question in
the corpus was generated from the subject and attribute its own document uses.
The corpus was measuring a questioner who had already read the answer.

## The decision

Content terms, and coverage.

A question is split into **script runs** — `テントの重量は` becomes
`[テント, の, 重量, は]` — and the hiragana runs are dropped. What is left is
the question's content: `[テント, 重量]`. The particles and inflection that
were dropped are precisely what changes when the same question is asked in
different words.

A candidate confirms on coverage when **every content term appears in it**.
`テントはどれくらい重い` has content `[テント, 重]`, both present in
`テントの重量は2.4kg` — so it confirms, though the two strings share no phrase
longer than `テント`.

**No morphology and no dictionary.** A run of one script is structure the
string already has. A word list would need one per language and would not
survive the next corpus, which is the same reason ADR-0007 chose bigrams over
a segmenter.

**A fallback, never a replacement.** Coverage runs only where the phrase rule
found nothing — which today means the candidate is rejected outright — so it
can turn a rejection into a result and never the reverse. ADR-0007's guarantee
is untouched: the index still over-generates and confirmation still decides.

### The threshold, and the part that is chosen rather than measured

`COVERAGE_THRESHOLD` is the fraction of the question's content characters that
must be found. Swept on the train split:

| threshold | recall | precision | trap rate |
|---|---|---|---|
| 0.5 | 93.3% | 85.6% | **28.6%** |
| 0.6 | 93.3% | 86.9% | 20.0% |
| 0.7 | 93.3% | 92.2% | 11.4% |
| **0.8 – 1.0** | **95.6%** | **98.8%** | **5.7%** |
| off | 91.1% | 98.8% | 5.7% |

Held-out agrees on the shape — 80.0% / 100% / 6.7% across 0.8–1.0, against
73.3% with coverage off — so the gain is not fitted to the cases it came from.

Whole corpus, before and after: recall **86.7% → 91.7%**, precision 99.1%
unchanged, trap rate 6.0% unchanged. It costs nothing measurable.

~~**The corpus cannot separate 0.8 from 1.0**, so the value is chosen rather
than measured~~ — **superseded 2026-09-05, and it separates them.**
`tools/measure_sensitivity.py` moves the threshold and re-scores the whole
labelled corpus:

| coverage_threshold | recall | trap |
|---|---|---|
| 0.6 | 87.8% | 18.3% |
| 0.8 | 87.2% | 5.8% |
| **1.0** | **87.2%** | **5.0%** |

The corpus at the time this was written was smaller and could not see the
difference; it can now, and it agrees with the choice. Loosening the rule to
0.6 buys **0.6 recall points for 13.3 trap points** — the trade this ADR
guessed at, with the numbers it lacked.

So 1.0 is measured rather than chosen, and the reason to keep it is unchanged:
where evidence is absent this library fails closed. At 1.0 the rule also states
plainly — *every content term of the question appears in the candidate* — and a
rule a reader can hold in their head is worth something on its own.

It is a **setting** now rather than a constant
([ADR-0026 measured all three](0026-a-lead-is-offered-only-when-there-is-nothing-to-confuse-it-with.md)
is the leads decision; the sweep is described in `docs/measurements.md`). A
value that swings the trap rate 13 points across its range is a value fitted to
one corpus, and the only way to disagree with it used to be editing this file's
implementation.

## What it costs

**A question using words the document does not contain still finds nothing.**
`テントは何キロ?` against a document that says `2.4kg` fails, because `キロ` is
not there. Reaching it needs half-coverage, which the corpus measured at a
28.6% trap rate — five times the ceiling. This is lexical retrieval's real
boundary and the honest place to stop; crossing it wants embeddings, and that
is a different ADR with a different cost.

**Two English paraphrase cases fail and always will.** "how much warning must
be given" shares no content word with "the length of the notice period is 30
days". No rule over the strings fixes that.

**A single kanji is now a content term.** `重` in `テントはどれくらい重い` is
one character and matches inside `重量`. That is deliberate — it carries the
question's subject matter — but it is the weakest evidence in the scheme, and
it is why the threshold is at the strict end: at 1.0 a single-character term
must be joined by *every other* content term before anything confirms.

**The rule is coarser than a language.** Script runs treat `ＮＹ支店` as two
terms and `お茶` as one, and neither is linguistically defensible. It is
defensible as *structure*, which is all it claims to be, and it is measured.

## What was not decided

Embeddings, synonyms, and any per-language word list. The first is a real
answer to the residual above and needs its own measurement; the other two are
maintenance in the shape of a feature.
