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

**1. An embedding candidate source, fused, with confirmation unchanged.**

The one thing lexical retrieval provably cannot do here, and the literature
agrees on both the technique and the fusion. The design question is not
whether it works; it is **what confirms a semantic candidate.**

[ADR 0007](../adr/0007-index-japanese-by-bigram.md) is explicit: anything that
makes the index smarter is allowed, anything that skips confirmation is not. A
document proposed by cosine similarity may share no substring with the
question, so *nothing* confirms it in the current sense. Three options, and the
third is the one worth proposing:

- Drop unconfirmable candidates: makes the whole feature inert.
- Confirm semantically: replaces exact evidence with a score, which is the
  thing this library exists not to do.
- **Carry them, marked.** A package already reports *why* each item is there
  (`selection.signals`) and already renders that ([ADR 0019](../adr/0019-confirmation-is-relative.md)).
  An item proposed by similarity and confirmed by nothing says so, in the
  prompt, in the JSON, and to the reader — and its anchor is still exact,
  because the span still comes from the stored text. **Selection is a report,
  not a promise** ([ADR 0005](../adr/0005-selection-is-a-report.md)), and this
  is what that sentence is for.

Cost, stated up front: embeddings need a model, so this is opt-in like `ask`,
and it belongs behind an `Embedder` port with the ollama adapter satisfying it
(the endpoint exists; `bge-m3` and `nomic-embed-text` are the obvious local
choices). The index gains vectors, which is index size ADR-0007 already
measured for terms. Fusion should be RRF rather than score arithmetic, because
bm25 and cosine are not on the same scale.

**2. Two models in the answer evaluation, not one.**

`eval --model` runs one model. Every model-facing defect this project found was
found by disagreement between two. A `--model a,b` that reports both and flags
where they differ turns an anecdote into a signal.

**3. Chinese, or an honest sentence about it.**

Japanese has kana and Korean has spaces; Chinese has neither, so a whole
question is one content term and nothing can be dropped. Either something
structural exists that is not a dictionary — character-level coverage with a
threshold, which the corpus can now measure — or the limitation gets stated in
the README rather than only in a measurement table.

### Then

**4. Term rarity in the confirmation stage.** ADR-0019 closed the near-miss
gap with a *relative* rule, and noted what it does not have: bm25 knows that
`warranty` is the rare word and `the coverage period of` is not, and
confirmation still does not. Cheap, already computed, and the last piece of the
lexical story.

**5. Incremental ingestion.** Unchanged from 0002 and still not close: re-ingest
over an unchanged corpus is 5.5× cheaper than a cold build, so the ten-second
threshold arrives near 12,000 documents.

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

## What this proposal is not

It is not a schedule, and it is not evidence. The one item with a number
attached is the first, and the number is the residual: **five paraphrase cases
and every Chinese one**, which is what an embedding source would have to earn
its cost against.
