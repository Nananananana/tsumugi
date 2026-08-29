# 2. What building it taught, and what to do next

**Status:** proposed
**Supersedes:** the roadmap in
[0001-the-design.md](0001-the-design.md) §10, and nothing else in it.

*This is a proposal. [0001](0001-the-design.md) stays where it is, describing
what was proposed before any of it existed — that is the point of keeping the
two apart. What is **true now** is in [architecture.md](../architecture.md).*

---

## Why revise at all

0001 was written before a line of code. Most of it held: the constitution, the
layering, the ContextPackage as a document, evidence that survives an edit.

But a roadmap written before building is a guess about what will be hard, and
six things turned out differently. Three of them change what to build next, and
one of them is a measurement that did not exist when 0001 was written.

---

## What was wrong in 0001

### The ordering was wrong: verification is not v0.4 work

0001 put claim verification at v0.4, after prompt templates. That was
backwards. Verification is what makes a package *worth* anything: without it,
"evidence first" is a claim about data structures. It got built at v0.2, and
everything after it was easier for being able to ask "did the model actually
use this?".

Templates, meanwhile, have not been missed once.

### Incremental ingestion is much further away than assumed

0001 held it behind "a full rebuild passes ten seconds", which sounded near.
Measured ([measurements.md](../measurements.md)): 223 documents a second, so a
cold build reaches ten seconds at ~2,200 documents — but **re-ingesting an
unchanged corpus is 5.5× cheaper**, because an unchanged document skips the
store write and the index update. Ten seconds of *re-ingest* is nearer 12,000
documents.

The trigger was right; the number to watch was the wrong one. Nothing to build.

### The freeze wanted evidence, not a date

0001 said ContextPackage freezes at v0.2. It froze one commit later than that,
after the MCP server had built a package in one process and verified it in
another. The rule worth keeping: **a contract freezes when a second program has
produced and consumed it**, not on a version number.

### "Runtime dependencies: zero" was cheaper than expected

The cost 0001 predicted — a hand-written Markdown parser, hand-written
validation, no tokenizer — was all real and all small. The parser is 110 lines
and the invariant that protects it (*a parser reports spans and never rewrites
text*) means a bad parse costs section quality and cannot cost an anchor.

Zero is not a sacrifice being tolerated. It should be defended harder than 0001
defended it.

### The evaluation corpus should have come earlier

It was v0.6-ish in 0001 and got built at v0.2. In its first run it found a
defect that had been in the code for four commits and was invisible from every
other angle. See below. **Anything that changes selection should have been gated
on this existing.**

### Redundancy marking is now the top of the list, and it was near the bottom

Not by opinion. By a measurement — the first one this project has that asks for
a feature rather than permitting one. See below.

---

## What the evaluation corpus found

Its first run, on thirty labelled cases:

| | first run | after the two fixes below |
|---|---|---|
| evidence recall | 100% | 100% |
| lexical-near-miss trap rate | **96.7%** | **10.0%** |
| omission correctness | n/a | **0.0%** |

### The trap rate was a real defect, four commits old

**Unconfirmed candidates were entering packages.** When the bigram index
proposed a document and confirmation found no occurrence of the query in it,
the result was included anyway as an item covering the head of that document —
dragging a whole unrelated document into the package.

That contradicts [ADR 0007](../adr/0007-index-japanese-by-bigram.md) in its own
words: *the index generates candidates; it does not decide matches.* Nothing
caught it. Unit tests passed, the CLI output looked reasonable, and the packages
were well-formed. It took a corpus that knew which document was the right one.

The fix: an unconfirmed candidate becomes an **omission** under
`below_threshold`, with a reason saying so. Reported, not sent. 96.7% → 36.7%.

### Then confirmation turned out to be weaker in English than in Japanese

The residual was entirely English, and the cause was structural rather than a
bug. A Japanese query has no spaces, so the whole query is one needle and
confirmation is a phrase match. An English query was split into words, so a
document sharing the word *nodes* with "how many nodes does the staging cluster
have" confirmed.

The fix: for a space-separated query, needles are contiguous runs of **two or
more** words. A phrase is evidence a document is about the query; a token is
evidence it is written in the same language. 36.7% → 10.0%, and train and
held-out agree at 10%, so it is not fitted to the cases it was measured on.

**The residual 10% is diagnosed and left alone.** All three remaining failures
confirm on a stopword phrase — "when is the first ferry departure" matching a
shuttle-bus document on *the first*. Fixing it needs term rarity, which the
index has as bm25 and the confirmation stage does not, or a stopword list, which
is a vocabulary list per language and does not generalise. Chasing it on thirty
synthetic cases would be fitting the ranker to the fixtures.

### And omission correctness is 0%, which is the most useful number here

Not a bug. The metric asks whether the *reason given* for an exclusion was
right, and every case expects a superseded document to be reported under
`redundant_candidate`. **Nothing ever reports that, because redundancy marking
([ADR 0008](../adr/0008-redundancy-is-proposed.md)) is not built.**

So the first metric this project has that asks for a feature is asking for that
one. That is the trigger the whole "hold it behind a measurement" discipline was
waiting for, and it arrived on the metric's first run.

---

## The revised roadmap

### Next — v0.3, in this order

1. **Redundancy marking** ([ADR 0008](../adr/0008-redundancy-is-proposed.md)).
   Structural, deterministic near-duplicate detection; the duplicate is
   *marked*, still considered, and loses only priority. Success is
   `omission correctness` moving off 0% — a number that exists before the work
   starts, which is the first time that has been true here.

2. **The corpus grows to its stated size.** Thirty cases is a tenth of what
   [evaluation-corpus.md](../evaluation-corpus.md) describes, and the traps it
   defines are only half planted: `near_duplicate`, `budget_squeeze` as its own
   case, `absent_answer`, `mixed_script` and `stale_anchor` have no cases yet.
   The missing ones are the interesting ones.

3. **Prompt templates.** Demoted from v0.3-first to v0.3-last: not once in
   building has the single built-in instruction set been the limiting factor.
   Build it when a second use needs a second shape, not before.

   > *Since written:* the condition fired. `ask` needs a machine-readable
   > answer and a human reader does not, so `build_context` takes an
   > instruction set and two ship
   > ([ADR 0017](../adr/0017-the-instruction-set-is-a-parameter.md)). Two named
   > dictionaries, not a template language — the rule above still holds for the
   > third shape.

### Then — v0.4

4. **The sibling adapters.** [ADR 0009](../adr/0009-restore-before-you-verify.md)
   is still an argument on paper: the restore-before-verify property is tested
   against a fake redactor written to make the point. The seam only exists when
   something real is on both sides, which was the argument for having siblings
   at all.

5. **`forget`, and `--rebuild`.** Both owed. `DocumentStore.forget` exists and
   nothing calls it; the tokenizer-mismatch error names a flag that is not
   implemented.

### Held, with the number that would release each

| Held | Trigger |
|---|---|
| Incremental ingestion | **Re-ingest** passes ten seconds — about 12,000 documents, not 2,200 |
| Embedding / vector search | The corpus shows lexical retrieval failing, which at 100% recall it does not |
| Context cache | The ledger shows repeated identical `package_id`s |
| Automatic redundancy *removal* | Marking has run long enough for the ledger to show marked duplicates going uncited |
| A morphological analyser | The corpus shows bigrams costing real recall |

Four of those five now have a place the number comes from, which none of them
had in 0001.

### Dropped

**Nothing new.** 0001's non-goals stand, and the knowledge graph it dropped
stays dropped.

---

## Three rules to keep

Written down because each was learned by nearly not doing it.

**Build the measurement before the thing it measures.** The corpus found a
four-commit-old defect on its first run. Every change to selection between now
and v1.0 should be gated on it.

**A number that asks for a feature outranks an opinion that asks for one.**
Redundancy marking moved from the bottom of the list to the top because
`omission correctness` is 0%, not because it seemed important.

**State the residual.** Every measurement in this project now ships with what
it does *not* say: the estimator's error names its tokenizer, the index
measurement names its corpus as source code rather than notes, and the trap rate
names the three cases it does not catch and why. That habit is the most
transferable thing here, and it came from `mamori`'s ADR-0023 — a public record
of a feature that did less than its README claimed, and the most trustworthy
document in that repository.
