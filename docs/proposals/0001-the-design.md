# 1. The design

**Status:** proposed
**Supersedes:** the 0.1.0 draft specification (held outside this repository)

*This is a proposal. Nothing here is implemented. It is not evidence that any
part of tsumugi exists. See [docs/README.md](../README.md).*

---

## What this changes from the draft

The draft's constitution is kept whole: evidence first, deterministic core, local
first, zero runtime dependencies, fail closed, the source is the truth. Those are
not opinions, they are the bill already paid by `mamori` and `kiseki`.

Twelve things are added, five changed, two dropped. The additions are what
separate a library from an infrastructure, and they share one theme: **the draft
was a one-way pipe.** Files went in, a prompt came out, and nothing came back.
Without a return path there is no way to know whether the selection was any good,
which makes the evaluation chapter aspirational.

| | Change | Where |
|---|---|---|
| + | ContextPackage becomes a versioned, portable **contract**, not a Python type | [§3](#3-the-contextpackage), [ADR 0002](../adr/0002-the-context-package-is-a-document.md) |
| + | A **ledger** records what was sent and what was cited | [§6](#6-the-ledger), [ADR 0011](../adr/0011-record-what-was-sent-and-what-was-used.md) |
| + | An **agent-facing surface** (MCP) alongside the CLI | [§7](#7-interfaces), [ADR 0012](../adr/0012-an-agent-facing-surface.md) |
| + | Packages are **reproducible**; `package_id` is the hash of the inputs | [ADR 0003](../adr/0003-a-package-is-reproducible.md) |
| + | A package states **what it left out and why** | [ADR 0005](../adr/0005-selection-is-a-report.md) |
| + | The model **quotes**; tsumugi resolves offsets | [ADR 0004](../adr/0004-the-model-quotes.md) |
| + | Budget is an estimate **whose error is measured and published** | [ADR 0006](../adr/0006-the-budget-is-an-estimate.md) |
| + | Japanese is indexed by **bigram**, confirmed against the text | [ADR 0007](../adr/0007-index-japanese-by-bigram.md) |
| + | **Restore before you verify** — the mamori ordering problem | [§8](#8-adapters), [ADR 0009](../adr/0009-restore-before-you-verify.md) |
| + | A **threat model** for tsumugi's own index | [threat-model.md](../threat-model.md) |
| + | Incremental ingestion is **designed for, deferred behind a measurement** | [§10](#10-roadmap) |
| + | Documents are separated into now / why / next from day one | [docs/README.md](../README.md) |
| ~ | The ContextPackage contract moves from v0.3 to **v0.1** | [§10](#10-roadmap) |
| ~ | Minimal search moves from v0.2 to **v0.1** | [§10](#10-roadmap) |
| ~ | "Structural Max Purge" is dropped by name; redundancy is **proposed, never removed** | [ADR 0008](../adr/0008-redundancy-is-proposed.md) |
| ~ | `max_tokens=` becomes `budget=Budget.tokens(...)`, unit explicit | [§5](#5-budget) |
| ~ | Layering is checked by **both** import-linter and an executable table | [§9](#9-quality) |
| − | Any reading in which "verified" implies "true" is removed | [§4](#4-evidence) |
| − | Knowledge graph and concept linking leave the roadmap until measured | [§11](#11-non-goals) |

---

## 2. Layers

```text
interfaces ──> application ──> domain
                    │              ▲
                    │              │
                    └──> ports <───┴── infrastructure
```

| Layer | Holds | May import |
|---|---|---|
| `domain/` | Documents, sections, anchors, evidence, claims, selection, budget, package assembly | **stdlib only** |
| `ports/` | `DocumentStore`, `Index`, `CostModel`, `ContextProvider`, `Redactor`, `LLMProvider` protocols | `domain` |
| `application/` | Ingest, search, build-context, build-prompt, verify, ledger | `domain`, `ports` |
| `infrastructure/` | SQLite store and index, filesystem reader, parsers, cost models, adapters | `domain`, `ports` |
| `evaluation/` | Datasets, retrieval and context metrics, comparison | everything above |
| `interfaces/` | CLI, MCP server | everything above |

`domain/` imports nothing outside the standard library, and nothing from
`kiseki` or `mamori` — those are `infrastructure/adapters/`, and the core runs
with neither installed ([ADR 0001](../adr/0001-the-domain-depends-on-nothing.md)).

The table is executable. A test parses every module and asserts it, so a diagram
that stops matching the code turns the build red rather than quietly becoming
fiction. This is `mamori`'s ADR-0017, adopted wholesale.

### The path a question takes

```text
                        a question, and a budget
                                   │
   ┌───────────────────────────────▼──────────────────────────────┐
   │ retrieve      bigram index -> candidate spans                │  infrastructure/index
   ├──────────────────────────────────────────────────────────────┤
   │ confirm       candidates checked against the anchored text   │  domain/anchor
   ├──────────────────────────────────────────────────────────────┤
   │ rank          structural + lexical signals, deterministic    │  domain/selection
   ├──────────────────────────────────────────────────────────────┤
   │ mark          redundancy flagged, never removed              │  domain/redundancy
   ├──────────────────────────────────────────────────────────────┤
   │ fit           estimated cost against a stated budget;        │  domain/budget
   │               everything dropped is recorded with a reason   │
   ├──────────────────────────────────────────────────────────────┤
   │ assemble      ContextPackage + omissions + package_id        │  domain/package
   └───────────────────────────────┬──────────────────────────────┘
                                   │
                          ┌────────▼─────────┐
                          │  the ledger      │  application/ledger
                          │  writes an entry │
                          └────────┬─────────┘
                                   │
                        rendered, protected, sent
                                   │
                          answer + citations
                                   │
   ┌───────────────────────────────▼──────────────────────────────┐
   │ restore       placeholders back to values, FIRST             │  adapters/mamori
   ├──────────────────────────────────────────────────────────────┤
   │ resolve       each quotation located in the real document    │  domain/anchor
   ├──────────────────────────────────────────────────────────────┤
   │ classify      supported / unsupported, per claim             │  domain/claim
   ├──────────────────────────────────────────────────────────────┤
   │ close         the ledger learns which context was cited      │  application/ledger
   └──────────────────────────────────────────────────────────────┘
```

The order is not arbitrary. Confirmation has to happen after retrieval because
the index over-generates on purpose. The budget has to be applied after ranking
and before assembly, so that what was dropped can be named. And restoration has
to happen before verification, or every citation through `mamori` fails to
resolve — see [§8](#8-adapters).

---

## 3. The ContextPackage

The central object, and the reason this is infrastructure rather than a library.

The draft described it as a Python dataclass. It is instead a **versioned,
serializable contract** with a published JSON Schema and a conformance suite, in
the way `kiseki` publishes `PhotoRecord v1`. A contract that other programs can
produce and consume is a different kind of object from a class other programs can
import ([ADR 0002](../adr/0002-the-context-package-is-a-document.md)).

The full contract is [docs/context-package.md](../context-package.md). In outline:

```text
ContextPackage
├── contract          "tsumugi.context-package/1"
├── package_id        hash of every input that produced it
├── query             what was asked
├── instructions      role and rules
├── items[]           the selected context, each with its evidence anchor
├── omissions[]       what was NOT selected, and which rule dropped it
├── constraints       output rules
├── output_schema     expected shape of the answer
├── budget            unit, limit, estimate, and the estimator's known error
└── provenance        corpus state, settings, tsumugi version
```

Three properties make it worth freezing:

**It is complete.** A consumer that has the package needs nothing else from
tsumugi to render a prompt. No hidden state, no callbacks.

**It is honest.** `omissions` is not optional. A package that selected three
documents out of eleven says so, and says which rule removed the other eight.

**It is reproducible.** `package_id` is the hash of the corpus state, the query,
the settings and the version. Two identical packages are byte-identical, which
buys caching, diffing and regression tests at once
([ADR 0003](../adr/0003-a-package-is-reproducible.md)).

---

## 4. Evidence

Every unit of context carries an anchor:

```json
{
  "document_id": "...",
  "start": 1204,
  "end": 1391,
  "text_hash": "sha256:...",
  "document_hash": "sha256:..."
}
```

The invariant, asserted by property-based tests over generated documents:

```text
document.content[anchor.start:anchor.end] == anchor.text
sha256(anchor.text)                       == anchor.text_hash
```

An anchor whose `document_hash` no longer matches the document on disk is
**stale**, not wrong: the evidence was true when it was taken. tsumugi reports it
as stale and refuses to present it as current. Silently re-anchoring against
edited text would be the single most damaging thing this library could do.

### What "verified" means, exactly

It means: *this string exists at this offset in this document, whose content
hashes to this value.*

It does not mean the string is true, that the document is reliable, or that a
sentence built around the quotation follows from it. A model can quote your notes
perfectly and reason from them badly, and tsumugi will report the citation as
resolved because it is.

This is repeated in the README, the concept document and the CLI output, because
the failure mode of an evidence system is that people stop reading the word
"evidence" and start reading it as "correct".

### Claims

When a model answers, its output is split into claims, each with zero or more
citations. Each citation is resolved deterministically against the text that was
sent. The result is one of:

| | Meaning |
|---|---|
| `supported` | Every citation resolved to a real span in a document in the package |
| `unsupported` | A citation did not resolve — the quotation is not there |
| `uncited` | The claim carries no citation at all |

`uncited` is separate from `unsupported` on purpose. A model that cites nothing
has failed differently from one that cites something that does not exist, and
collapsing the two hides which of the two problems you have.

---

## 5. Budget

The draft was unsure whether to speak in tokens. The resolution is to make the
unit explicit at the call site and the error measurable:

```python
package = tsumugi.build_context(
    query="...",
    budget=Budget.tokens(8000),  # or .characters(20000), or .bytes(...)
)
```

`CostModel` is a port. The default implementation is a heuristic in the standard
library, with different coefficients per script — a CJK character and a Latin
character do not cost the same, and a Japanese-first library that pretends
otherwise will be wrong by a factor, not a percent.

**The heuristic's error is measured against real tokenizers in development-only
tests, and the measured error is published in the package itself**
([ADR 0006](../adr/0006-the-budget-is-an-estimate.md)):

```json
"budget": {
  "unit": "tokens",
  "limit": 8000,
  "estimate": 7412,
  "estimator": "heuristic/cjk-aware@1",
  "measured_error": {"p50": 0.03, "p95": 0.11, "dataset": "...", "against": "..."}
}
```

A caller who needs exactness installs a tokenizer adapter. A caller who does not
gets a number and knows how much to distrust it. Claiming accuracy would be worse
than either.

---

## 6. The ledger

The return path the draft was missing.

Every `build_context` writes an entry: the `package_id`, what was included, what
was omitted and under which rule, and the estimated cost of each item. That entry
stays open. When an answer is verified, the ledger is closed with what the model
actually cited.

Over a few weeks this answers questions nothing else can:

- Which documents are sent constantly and cited never
- Whether the budget is binding, and what falls off the edge when it is
- Whether a change to the ranking made things better or only different
- What a month of context actually cost

Without it, [§12](#12-evaluation) is a plan to compute metrics on synthetic data.
With it, the metrics are computed on the questions the owner actually asked
([ADR 0011](../adr/0011-record-what-was-sent-and-what-was-used.md)).

The ledger records identifiers, offsets and counts — not the text. It is derived
data and can be deleted at any time; deleting it costs history and nothing else.

---

## 7. Interfaces

### CLI

```bash
tsumugi ingest ./knowledge      # read a folder, anchor and index it
tsumugi search "context engineering"
tsumugi context "what did I decide about budgets?" --budget tokens:8000
tsumugi prompt  "draft the proposal" --template synthesize
tsumugi verify  answer.json     # resolve the citations in a model's answer
tsumugi trace   <evidence-id>   # from a quotation back to the document
tsumugi ledger  --since 30d     # what was sent, what was used
tsumugi doctor                  # stale anchors, missing files, index drift
tsumugi eval
```

`context` and `verify` are the two commands the design is for. `trace` is the one
that makes people trust it, because it goes backwards.

### MCP

The primary consumer of a ContextPackage is not a person composing a prompt. It
is an agent. tsumugi ships an MCP server as a first-class interface, exposing
`search`, `context`, `trace` and `verify` as tools
([ADR 0012](../adr/0012-an-agent-facing-surface.md)).

MCP is JSON-RPC over stdio. It needs no dependency, and it makes every MCP client
a user of this library without either side writing an integration.

### Library

```python
import tsumugi

corpus = tsumugi.Corpus("~/.tsumugi/index.db")
package = corpus.build_context(
    "what did I decide about budgets?", budget=tsumugi.Budget.tokens(8000)
)

print(package.render())  # a structured prompt
print(package.omissions)  # what did not fit, and why

report = package.verify(answer)  # claims, each supported / unsupported / uncited
```

---

## 8. Adapters

The core does not import `kiseki` or `mamori`. Both are optional adapters, and
the test suite runs with neither installed.

### kiseki — a context provider

`ContextProvider` is a port. The kiseki adapter reads kiseki's **export**, not its
database, so tsumugi is coupled to a published contract rather than a schema.

The layering kiseki enforces is carried across unchanged: facts, measures and
interpretations stay separate. **A kiseki interpretation entering a ContextPackage
is labelled as an interpretation, with its own evidence and confidence, and never
becomes a fact because it crossed a library boundary.** An interest that says
"you seem to care about ceramics, confidence 0.7, from these eleven photographs"
must still say that inside the package.

### mamori — a redactor, and an ordering problem

This is the part the draft missed, and it is not a detail.

```text
  original      "田中太郎との打ち合わせは金曜"
      │
      ▼  tsumugi anchors against the ORIGINAL
  anchor        offset 0-5 = "田中太郎"
      │
      ▼  mamori protects the RENDERED package
  sent          "<PERSON_001>との打ち合わせは金曜"
      │
      ▼  the model quotes what it was given
  citation      "<PERSON_001>との打ち合わせ"
      │
      ▼  tsumugi resolves against the original
  result        ✗ no match — a true citation reported as unsupported
```

Every citation through a redacted package fails to resolve, and the failure looks
exactly like a hallucination. An evidence system that reports honest citations as
unsupported is worse than one with no verification at all, because it teaches its
user to ignore the signal.

The rule: **restore, then verify**
([ADR 0009](../adr/0009-restore-before-you-verify.md)). `mamori`'s scope survives
the round trip and is held by the session, so restoration is available at exactly
the moment verification needs it. The package records that it was protected and
by which scope, so a verification attempted without restoration can fail loudly
instead of quietly.

This is why `mamori` and `kiseki` are worth having in the integration tests even
though neither is a dependency: the interesting failures are at the seams, and
the seams only exist when something real is on both sides.

---

## 9. Quality

**Layering.** Both mechanisms, because they catch different things. `import-linter`
asserts the direction between layers (kiseki's method). An executable table in
`tests/test_architecture.py` asserts that `domain/` imports only the standard
library and that the table in `docs/architecture.md` is true (mamori's method).

**Invariants**, checked with property-based tests over generated documents:

```text
text[start:end] == anchor.text                 for every anchor
sha256(anchor.text) == anchor.text_hash        for every anchor
sum(cost(item)) <= budget.limit                for every package
every omission names a rule and a reason       for every package
same inputs -> identical package_id            for every package
every anchor in a package resolves             for every package
```

**Adapter conformance.** Every port has a conformance suite; a new implementation
subclasses the mixin and inherits the contract rather than guessing at it. Test
doubles are held to the same suite as real adapters — a fake that is easier to
satisfy than the real thing is a fake that hides bugs.

**No hidden truncation.** Anything that bounds coverage — a top-N, a sampling, a
cut-off — is reported in `omissions`. A silent cap reads as "everything was
considered", and that is the lie this project exists to avoid.

---

## 10. Roadmap

### v0.1 — Evidence core, and a shape to build on

- Markdown, JSON, text and source-code ingestion
- Document / Section / Block, offsets and hashes
- SQLite store, bigram index, FTS5
- Evidence anchors, and the invariants as tests
- Minimal search: path, heading, exact and keyword
- **ContextPackage v1, marked draft** — the envelope, the schema, the conformance suite
- CLI: `ingest`, `search`, `trace`, `doctor`
- Architecture tests and layering gates from the first commit
- The evaluation corpus generator, and the ~60-case CI tier — retrieval lands
  here, so the thing that measures retrieval lands with it

The draft put ContextPackage at v0.3. Everything hangs off its shape, so
everything written before it would be rewritten after it. Its *contents* can grow;
its *envelope* is decided now.

Search moves here for a similar reason: "confirm this works without a model" is
not confirmable if nothing can be retrieved.

**Exit criterion:** a folder of a thousand real documents is ingested, searched
and traced, with no model, no network and no third-party package.

### v0.2 — Context and budget

- Selection and ranking
- `CostModel`, the heuristic, and its measured error
- Omission reporting
- Reproducibility and `package_id`
- The ledger's opening half
- ContextPackage v1 **frozen**
- The full 100–1000 case corpus, the traps, and the held-out split
- CLI: `context`, `ledger`, `eval`

### v0.3 — Prompt infrastructure

- Structured prompt rendering, addressable sections
- Templates: summarize, extract, compare, synthesize, answer-with-evidence
- Instruction de-duplication and context compression
- Redundancy **marking** — candidates only, never automatic removal
- Output schemas
- CLI: `prompt`

### v0.4 — Verification, and the loop closed

- `LLMProvider` port, optional
- Claims and citations, model quotes and tsumugi resolves
- supported / unsupported / uncited
- The ledger's closing half: which context was actually cited
- CLI: `verify`
- MCP server

### v0.5 — Adapters

- kiseki context provider, over the export contract
- mamori redactor, and restore-before-verify
- Privacy-aware context flow, end to end, with integration tests that need both

### v0.6 — Scale, when it is measured to hurt

Each item waits behind a number, not an opinion. This is `kiseki`'s discipline
and it is the reason that project is not carrying three unused subsystems.

| Held item | Trigger to build it |
|---|---|
| Incremental ingestion | A full rebuild passes ten seconds on the real corpus |
| Embedding / vector search | The golden retrieval dataset shows lexical search failing |
| Context cache | The ledger shows repeated identical packages |
| Structural redundancy removal | Marking has run long enough to show it is right |

The schema is designed so that each is possible; none is written until the
measurement asks for it.

---

## 11. Non-goals

Not in any planned version:

- A web service, a hosted anything, cloud sync
- A required model, required embeddings, a required vector database
- A dependency on LangChain, LlamaIndex, pydantic or pandas in the core
- Automatic knowledge-graph construction and concept linking — **dropped from the
  roadmap.** It is the feature that demos best and contributes least to
  [§13](#13-success). If a measurement asks for it, it comes back as a proposal.
- Any claim of zero hallucination
- A dependency on kiseki or mamori

---

## 12. Evaluation

Two independent measurements, because neither is honest alone.

**A labelled corpus with known answers.** 100–1000 generated cases across random
genres, each a small synthetic corpus, a question, and a labelled evidence set
with planted traps. **The corpus is labelled; the ideal output is not** — there is
no single correct structured prompt, so scoring distance to a chosen one measures
conformity. Every metric is arithmetic over anchors, with no grader and no model
in the loop. See [ADR 0013](../adr/0013-label-the-evidence-not-the-ideal-answer.md)
and [docs/evaluation-corpus.md](../evaluation-corpus.md).

**The ledger over a real corpus**, which has no labels but has real questions and
real usage. It answers what a synthetic set cannot: whether any of this helps the
person who owns the notes.

The first measures correctness against a corpus that is tidier than life. The
second measures usefulness against a corpus with no ground truth. Together they
cover the gap each one leaves.

`tsumugi eval` reports both.

| Family | Measured |
|---|---|
| Retrieval | precision, recall, rank of the first relevant span |
| Context | size, redundancy share, evidence coverage, **omission rate under budget** |
| Prompt | reduction against send-everything, instruction duplication |
| Verification | supported / unsupported / uncited shares, citation resolution rate |
| Cost | estimator error against a real tokenizer, p50 and p95 |
| **Omission correctness** | for each excluded required fact, whether the *reason given* was right |

Omission correctness is the metric that would not exist without
[ADR 0005](../adr/0005-selection-is-a-report.md), and it is the most useful of
them. It does not ask whether the outcome was right — it asks whether the reason
was. Dropping the correct document and correctly saying `budget_exhausted` means
the budget is too small. Dropping it and saying `below_threshold` is a ranking
bug. Same outcome, different diagnosis, and only a labelled corpus separates them.

Two rules borrowed from `mamori`'s evaluation, which found that its own model tier
did not do what its README claimed:

- **`--compare` prints the individual samples that changed**, not only the
  aggregate. Tuning against an aggregate fits the ranker to a number instead of to
  a corpus.
- **Results are published even when they are unflattering.** mamori's ADR-0023 is
  a public record of a feature that did less than advertised, and it is the most
  trustworthy document in that repository.

---

## 13. Success

> Not "send the whole folder", and not "paste three files by hand", but: **the
> parts that bear on the question, fitted to a budget you chose, each traceable to
> the document it came from, with an explicit account of what was left out — and
> afterwards, a check of whether the answer actually used any of it.**

If tsumugi does that, locally, with no runtime dependencies, it is worth using
alone. Everything about kiseki and mamori is addition on top of a library that
already stands up by itself. That ordering is a requirement, not a preference.

---

## 14. Settled, and still open

Settled on 2026-08-30:

1. **License: Apache-2.0.** Chosen over MIT for the explicit patent grant, which
   lowers the bar for adoption inside an organisation — the setting where a
   context tool is most useful and most scrutinised. Matches `mamori`.
2. **Python 3.12.** Matches `kiseki`. `tomllib`, `datetime.UTC` and modern typing
   syntax are in the standard library, which keeps the configuration story
   dependency-free.
3. **ContextPackage freezes at v0.2**, marked `1-draft` until then. Freezing in
   v0.1 risks a v2 within a month; never freezing means it is not a contract.
4. **The kiseki adapter reads the published export**, not the database. Whether
   the export carries enough for useful context is unverified and is the first
   thing v0.5 measures. If it does not, the fix is a proposal against kiseki's
   export, not a private read of its schema.
5. **Evaluation labels the evidence, not the ideal answer**
   ([ADR 0013](../adr/0013-label-the-evidence-not-the-ideal-answer.md),
   [docs/evaluation-corpus.md](../evaluation-corpus.md)). 100–1000 generated
   cases across random genres, each with a labelled evidence set and planted
   traps. A model runs at authoring time only; CI calls nothing.

Still open:

6. **Where the index lives by default.** `~/.tsumugi/` or beside the corpus. The
   second is easier to reason about and easier to leak — see
   [threat-model.md](../threat-model.md). Needed before v0.1 ships.
7. **What the generator is.** ADR 0013 fixes the dataset's shape, not which model
   authors it or how genre diversity is sampled. Needed before the corpus is
   generated, and the seed and model must be recorded with the fixtures.
