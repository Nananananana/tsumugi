# Proposal 0003: What running it taught

*This is a proposal: it revises the roadmap. It is not evidence that anything
here exists. See [docs/README.md](../README.md).*

[0001](0001-the-design.md) was the design before any code. [0002](0002-what-building-it-taught.md)
revised it from what building cost. This one revises it from what **running it**
cost — against real models, against a corpus that was not written to suit it,
and against what other people have published since.

Everything on 0002's roadmap has shipped. What follows is what replaced it.

---

## What running it taught

### 1. A fake provider tests the shape you wrote, not the one people use

Every model-facing decision that turned out to be wrong survived a full test
suite and was found in the first minutes against a real one.

- `qwen2.5:14b` answered a Japanese question perfectly and cited
  `notes/持ち物リスト.md (持ち物リスト（控え）)` — the *header* above the
  passage, which is what "citation" means everywhere outside this library.
  Every claim reported unsupported and the answer was right.
- Moving the JSON shape into the package's `OUTPUT_SCHEMA` section and out of
  the prose dropped the sentence telling the model to reply with JSON.
  `qwen2.5:14b` kept working. `llama3.1:8b` answered **fifty of fifty**
  evaluation cases in fluent prose, which parses as nothing, verifies as zero
  claims, and reads clean.
- `{"claims": []}` verified clean, because `all()` over nothing is true.

**The rule this suggests:** any decision about what a model is asked for is
unmeasured until two models have been asked. One is a fixture with a bigger
vocabulary.

### 2. A metric about a model is wrong three times before it is right

The trap rate for "was the model fooled by the outdated passage" read 88%,
then 88%, then 100%, while the model did something defensible each time.
It ended as a rate over the one unambiguous case (the outdated passage was
*all* the reader got) and a count for everything else, because
**citations cannot say which reading a prose answer leaned on** — the same two
spans back both readings.

### 3. Ten genres was not a corpus, it was a mirror

Tripling the evaluation corpus took the trap rate from 6% to 26% **with no
code change at all**. The vocabulary of ten genres written by whoever was
writing the ranker had been chosen, without anyone intending it, to suit the
ranker. Varying document *shape* — eight instead of one — found three more
defects, including a case that had been passing because every answer document
repeated its heading in front matter and won on bm25.

---

## What the outside world says

Read in August 2026, and only the parts that bear on decisions here.

**Citation evaluation has converged on the three questions this library
already asks.** Practitioner guides describe the rubric as: did the model emit
a citation, does it resolve, and does the source actually contain the claim.
That is `uncited` / `unsupported` / `supported`, and it is worth knowing that
the shape is not idiosyncratic. Where the field goes further is in *scoring
groundedness with a model judge* — 80% agreement with human raters is the
number quoted — and this project should not: a judge that agrees with humans
80% of the time is a fourth verdict to trust, and the whole point here is
resolving a quotation deterministically.

**Hybrid retrieval is the standard answer to exactly this library's residual.**
The consensus is that BM25 wins on exact matches — identifiers, codes, rare
terms — and dense retrieval wins on *paraphrase queries where no keywords
overlap*, and that fusing the two ranked lists with Reciprocal Rank Fusion
beats either. tsumugi's measured residual is precisely the second half:
`テントは何キロ?` against a document that says `2.4kg`, and every Chinese
paraphrase case.

**Script-aware bigramming is common practice**, not an invention: bigram the
ideographic runs and let kana through. *Adopted, measured, and shipped* — 12–20%
fewer terms at identical scores, and it exposed a run-splitting defect.

**MCP retired its handshake.** The 2026-07-28 revision is stateless: per-request
metadata, `resultType` on every result, cacheable list results. *Adopted and
shipped*, both eras answered.

---

## The roadmap this leaves

### Next — the residual has a name now

**1. An embedding candidate source, and a fourth thing a package can say.**

*Retitled 2026-08-30.* It read "...fused, **with confirmation unchanged**"
until the clause was measured: **0 of 23.** Every document similarity recovers
is dropped again by confirmation, so the phrase written as this item's safety
guarantee described doing nothing. The body below already said so -- "makes the
whole feature inert" -- and the heading claimed it as a feature anyway. The
heading is what a reader skims.

The one thing lexical retrieval provably cannot do here, and the literature
agrees on both the technique and the fusion. The design question is not
whether it works; it is **what confirms a semantic candidate.**

[ADR 0007](../adr/0007-index-japanese-by-bigram.md) is explicit: anything that
makes the index smarter is allowed, anything that skips confirmation is not. A
document proposed by cosine similarity may share no substring with the
question, so *nothing* confirms it in the current sense. Three options, and the
third is the one worth proposing:

- Drop unconfirmable candidates: makes the whole feature inert. **Measured,
  not assumed: 0/23.** Mostly tautological -- these cases are lexical misses
  and confirmation is lexical -- but retrieval and confirmation are different
  rules, so it could have been 3 or 5, and now it is not a guess.
- Relax the coverage threshold instead. **The ceiling is 6/23**, and it is not
  reachable: in the other 17 the rival covers as much of the question as the
  answer or more -- `answer 0.42 / rival 0.42`, `0.56 / 0.56` -- and no
  threshold separates a tie. In `zh-medical-appointment` the rival covers
  *more* (0.18 against 0.00) on a case embeddings get right, so relaxing admits
  the adversary while still excluding the answer.
- Confirm semantically: replaces exact evidence with a score, which is the
  thing this library exists not to do.
- **Carry them, marked.** A package already reports *why* each item is there
  (`selection.signals`) and already renders that ([ADR 0019](../adr/0019-confirmation-is-relative.md)).
  An item proposed by similarity and confirmed by nothing says so, in the
  prompt, in the JSON, and to the reader — and its anchor is still exact,
  because the span still comes from the stored text. **Selection is a report,
  not a promise** ([ADR 0005](../adr/0005-selection-is-a-report.md)), and this
  is what that sentence is for.

**What would close this, and what would not.** The question is no longer which
threshold. It is whether a package may carry an item that nothing lexical
confirms and say so plainly -- which is a change to what a package *means*, and
therefore an ADR before it is a line of code.

It closes when an embedding source recovers the paraphrase residual **without
raising the trap rate** --
both measured on train and confirmed held-out, as ADR-0018 and ADR-0019 were.

It does **not** close on recall alone, and that is not a hypothetical caution:
[the measurement](../measurements.md) says a naive embedding source would
recover every English and Japanese paraphrase case *and* spring near-miss traps
in Chinese and Korean, which have none today. All eight of its failures were
the near-miss outranking the answer. A version of this that reported "+12
paraphrase cases" and left the trap rate out would be the fourth wrong number
this project has produced about its own retrieval.

Nor does it close on one embedding model. `bge-m3` and `nomic-embed-text`
recovered 15 and 13 of the same 23 cases -- close totals over *different*
cases, which is the disagreement that item 2 exists for, arriving in the
embedding half.

Cost, stated up front: embeddings need a model, so this is opt-in like `ask`,
and it belongs behind an `Embedder` port with the ollama adapter satisfying it
(the endpoint exists; `bge-m3` and `nomic-embed-text` are the obvious local
choices). The index gains vectors, which is index size ADR-0007 already
measured for terms. Fusion should be RRF rather than score arithmetic, because
bm25 and cosine are not on the same scale.

**2. Two models in the answer evaluation, not one.** — **done, 2026-08-30.**

`eval --model a,b` runs each and names the cases where their outcomes differ.
Left here rather than deleted because the reason is the durable part: every
model-facing defect this project found was found by two models disagreeing,
and `qwen2.5:14b` answering fifty cases while `llama3.1:8b` answered none of
them — on the same prompt — is what one model alone looks like when it looks
fine.

**3. May a package carry an item that nothing lexical confirms?**
*(promoted 2026-08-30, when item 1 was withdrawn)*

The question item 1 left behind, and it outranks the rest because it is the
only one that changes what a package *means*. An item proposed by similarity
and confirmed by nothing is either declared -- in the JSON, the prompt and the
rendering -- or it does not go in. **ADR before code**, and the ADR has to
survive the trap measurement, not the recall one.

**Closes** with an ADR that either states the rule or records the refusal.
**Does not close** with an implementation; if a change to what a package means
arrives as a diff, the decision was never made.

**4. Chinese, or an honest sentence about it.**

Japanese has kana and Korean has spaces; Chinese has neither, so a whole
question is one content term and nothing can be dropped. Either something
structural exists that is not a dictionary — character-level coverage with a
threshold, which the corpus can now measure — or the limitation gets stated in
the README rather than only in a measurement table.

**Two decisions taken before any corpus exists**, because neither can be added
afterwards. Raised by `bench`, whose charter refuses to own another project's
measurement and who therefore handed this back rather than take it — correctly:
what tsumugi supports is tsumugi's decision, and the corpus follows the
decision rather than the other way round.

*Some Chinese genres are written by hand.* Not because handwritten genres are
the good ones — they are the **control**, and they are the flattering arm: 4.0%
trap against drafted genres' 28.0%. Without them Chinese can only ever be
scored drafted-against-drafted, and the single most informative cut this corpus
has — the one that widened to 4.0% against 33.3% when the script confound was
removed — becomes permanently impossible in the language that most needs it.
The Chinese and Korean genres are 100% drafted today, which is exactly why line
one of that measurement could not separate "someone else's words" from "a
script the tokenizer does not segment".

Worth stating plainly: *handwritten* here means written by the same author as
the ranker, not by a human elsewhere. That is the point of the arm. It measures
the author's imagination, which is the thing the drafted arm is there to escape.

*At least two drafting lineages*, which is akashi's rule and it is right: one
drafter makes the corpus that model's dialect, and a good score stops meaning
more than "this handles that model's phrasing".

**This turned out to be unimplementable, and fixing it was the first work.**
`genres.json` recorded `origin: drafted` and nothing else. Which model drafted
them is not in the file, not in the commit, and not in the working log — so
every origin split quoted in this repository, including the 4.0%/28.0% above,
compares handwritten against **drafted-by-something-unnamed**, and no record
can say what. Worse, `draft_genres.py` never wrote `origin` at all: a person
marked the output by hand after pasting it in. Provenance that depends on
remembering is one distraction from being wrong.

The field now exists, the tool stamps itself, the loader refuses a drafted
genre with no drafter and a handwritten one that names one, and the twenty
existing genres are marked `unrecorded` rather than back-filled with the tool's
default — the value is not known, and guessing it would put a name where the
honest entry is a gap.

### Then

**5. Term rarity in the confirmation stage.** ADR-0019 closed the near-miss
gap with a *relative* rule, and noted what it does not have: bm25 knows that
`warranty` is the rare word and `the coverage period of` is not, and
confirmation still does not. Cheap, already computed, and the last piece of the
lexical story.

**Closes** when the residual near-miss rate falls with no recall cost, train
and held-out agreeing. **Does not close** by way of a stopword list or any
per-language resource: ADR-0007 refused a segmenter, ADR-0018 a stopword list
and ADR-0019 a word list, each time for the same reason, and a rarity signal
that smuggled one in would be those three decisions reversed without an ADR
saying so. bm25 already knows this and needs no list, which is the whole
attraction.

**6. Incremental ingestion.** Unchanged from 0002 and still not close: re-ingest
over an unchanged corpus is 5.5× cheaper than a cold build, so the ten-second
threshold arrives near 12,000 documents.

### Answered since

**Should the ledger record a protection scope?** No — it records one boolean,
`protected`, and not the scope, the mode, the kinds or the counts
([ADR 0021](../adr/0021-the-ledger-records-that-a-package-was-protected-not-how.md)).

Worth keeping the shape of the answer, because the question was left open here
on purpose and the waiting paid: `mamori.protection-scope/1` shipped, and its
ADR-0032 said to *borrow the criterion and redraw the prohibitions* rather than
copy them. Redrawn for a ledger, the line fell in a place a copied list would
have missed — every field the ledger holds today is derivable from the index it
sits beside, and a scope would be the first one pointing somewhere else.

### Not planned, and why

- **A model judge for groundedness.** The field's answer to "is this claim
  supported" at scale. This library's answer is a resolved offset, and a
  judge would be a fourth verdict that agrees with people 80% of the time.
- **A prompt template language.** Two instruction sets ship
  ([ADR 0017](../adr/0017-the-instruction-set-is-a-parameter.md)); the third
  shape has to argue for itself.
- **A morphological analyser.** ADR-0007 refused a segmenter, ADR-0018 refused
  a stopword list, ADR-0019 refused a word list. Each refusal has cost
  something measurable and each has been cheaper than a per-language resource
  to maintain. Embeddings are the escape hatch that does not require one.

---

## What v1.0 would mean

*Asked across the family on 2026-08-30, and tsumugi was the one project from
which it could not be read.* Fair: nothing here said it.

**This project froze its contract at v0.1**, which is backwards from the usual
order and makes the usual answer unavailable. `tsumugi.context-package/1` is
already fixed; a consumer written against it today will keep working. So 1.0
cannot mean *the format settles* -- that happened first, on purpose, because a
context package is read by things outside this repository and a format that
moves is worse than a format that is limited.

What is left is a promise about the code that produces it, and there are two.

**1. The floors hold on a corpus this project did not write.**

Every number in [measurements.md](../measurements.md) comes from
`tools/generate_cases.py` -- a generator in this repository, written by the
same hand as the retrieval it scores. That is not a hypothetical conflict:

- 10 genres to 30 moved the trap rate **6.0% to 25.8% with no code change**;
- split by origin, handwritten genres trapped **4.0%** and genres drafted from
  a model's vocabulary **28.0%**;
- restricted to ja+en, to remove the script confound, the gap **widened** to
  4.0% against 33.3%.

The vocabulary I did not choose is the vocabulary that breaks it, every time it
has been tried. A 1.0 whose evidence is entirely self-generated is a 1.0
measured against its author's imagination.

**Closes** when the `ci` tier floors are met on at least one corpus whose text
came from outside this repository, scored without tuning against it -- the
held-out discipline, one level up: held out from the *generator*, not just from
the tuning.

**Does not close** by generating more genres with the same tool, however many.
30 genres from `draft_genres.py` is still one generator's idea of what
documents look like; that is the axis the numbers above say is load-bearing.
Nor by a corpus this project selects after seeing the scores.

**2. The public surface stops moving without notice.**

Today anything may be renamed. 1.0 means `build_context`, `search`, `verify`,
`ask`, the ports and the CLI verbs carry a deprecation policy, and the
architecture test's layer table gains the public names as a fourth column.

**What 1.0 is deliberately not:** a distribution on PyPI. The name `tsumugi`
there belongs to an unrelated project, the alternative is a distribution name
that differs from the import name, and **that is the owner's decision and is
open.** Tying 1.0 to it would make a packaging question into a correctness
milestone. A library can be 1.0 in a repository.

## What this proposal is not

It is not a schedule, and it is not evidence. The one item with a number
attached is the first, and the number is the residual: **five paraphrase cases
and every Chinese one**, which is what an embedding source would have to earn
its cost against.
