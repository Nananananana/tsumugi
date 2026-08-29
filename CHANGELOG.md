# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v0.2 in progress

Nothing is released. The version is `0.1.0.dev0` and the public API is not
stable.

### Added — the kiseki adapter, and layers that survive the crossing

- Reads kiseki's **published export**, never its database, and **imports
  nothing**: the export is JSON with a documented shape. A test asserts no
  import creeps in, because that would turn a file format into a dependency.
- It surfaced a real collision. kiseki exports no evidence references by design,
  and a `ContextItem` needs an anchor. Resolved the honest way round: **the
  export is a document**, an interest is anchored into it, and the package
  claims "the kiseki export of 2026-08-30 said this, here" — which is true and
  checkable — rather than "your photographs say this", which would be neither.
- **A producer declares its own layer**, in document metadata, and
  `build_context` reads it. An interest arrives as an `interpretation` carrying
  its confidence and is labelled as one in the rendered prompt; a note is a fact.
  An unknown layer stops the build rather than laundering the passage.

### Added — the mamori adapter, and ADR-0009 tested for real

- `MamoriRedactor` satisfies the `Redactor` port over a `PrivacySession`.
  Optional: `pip install tsumugi[siblings]`, and the suite skips its tests when
  the sibling is absent. Nothing outside `infrastructure/adapters/` imports it,
  and an architecture test asserts that.
- **ADR-0009's property now runs against the real redactor**, not only against
  a fake written to make the point: *privacy protection must not change a single
  classification*. A fake can only show the argument is internally consistent.
- tsumugi stores the scope identifier and never the mapping. Holding it would
  put every real value back into an index that is already a complete plaintext
  copy of a corpus, for no benefit.
- A package is never redacted in place: protecting it would leave items whose
  text no longer matched their `text_hash`, and the contract refuses to build
  one of those. What goes to the model is protected; what stays here is not.

### Added — `forget`, and `ingest --rebuild`

- **`tsumugi forget PATH`.** The index keeps the text it anchored, so deleting a
  file from your corpus does not delete it from here — that is what lets an
  anchor survive an edit, and it is exactly why this had to exist. It vacuums,
  and says what it does not cover: anything already sent to a model.
- **`tsumugi ingest --rebuild`** empties the index and reads the corpus again.
  Needed after the tokenizer changes, and the error about that mismatch now
  names a flag that exists.

### Fixed — the CLI leaked its database connections

- A process that exits closes its files, which is why every command worked and
  nothing noticed. But a leaked connection holds a read lock, a read lock stops
  the next command's `wal_checkpoint` from truncating, and so `forget` vacuumed
  and left the text sitting in the write-ahead log. Found by a test that runs
  two commands in one process, and the reason `forget` now checkpoints on both
  sides of its `VACUUM`.

### Fixed — staleness could never fire

- **`build_context` compared an anchor against the store**, which holds the
  text it anchored and so always matches. That check catches a corrupt index
  and nothing else; whether the *file* had moved on was never asked. A package
  would silently offer a passage from a file rewritten last week as though it
  were current. True since v0.2, and found by adding `stale_anchor` cases to
  the evaluation corpus.
- `FreshnessCheck` is a port, because finding out costs I/O. The filesystem
  implementation compares byte length first — allowing for a byte order mark,
  which ingest strips — and hashes only when that matches. A caller with no
  corpus to hand gets `freshness/unchecked` recorded in the package's
  providers, so nobody reads "no stale anchors" as "nothing was stale".
- `tsumugi context --corpus PATH` turns it on.

### Added — twenty more cases, and two traps that needed their own shape

- `absent_answer`: a question the corpus does not answer. `stale_anchor`: a
  document edited after it was indexed, which is the only way to exercise
  ADR-0010 end to end.
- Omission correctness **45% → 95%** once staleness could be detected.
- One finding is **reported and not gated**: four of ten English
  `absent_answer` cases still return context. That is not a defect. A package
  is passages that bear on a question, and documents about the right subject do
  bear on it; saying "the corpus has no answer" is a semantic judgement the
  instruction set leaves to the model. The number is kept because the gap is
  worth watching.

### Added — redundancy, marked and never removed

- **Near-duplicate detection** (ADR-0008), on character shingles and set
  containment. Deterministic, model-free, and it **marks**: a copy that fits the
  budget is still sent, carrying a `redundant_with:itm_001` signal so a reader
  knows two items are one idea. Only when the budget refuses it does it become
  an omission — and then under `redundant_candidate`, because "this repeats
  itm_001" is a better answer to *why* than "there was no room".
- The threshold, 0.75, sits in a **measured** gap: copies score 0.873–1.000 and
  everything else scores at most 0.417.
- **ADR-0015 amends ADR-0008's tie-break, which was wrong.** Preferring the
  earliest-dated source would systematically prefer the version that was
  corrected. The ranker's order decides who survives; redundancy says two
  passages are alike and has no way to know which is right.
- And the detector's limit is written down rather than discovered later: it
  cannot tell a corrected value from a different subject, because what
  distinguishes those is meaning. Whether a correction even *looks* like a copy
  depends on how much of the passage it changed — 0.42 in a sentence, 0.77 in a
  paragraph. A detector whose behaviour on a category depends on an unrelated
  variable must not be allowed to delete.
- `omission correctness` went **0% → 90%**, which is what asked for this work.
  Building it also showed the corpus's expectation had been wrong: it asserted a
  *superseded* document should be caught as a duplicate. The trap is now a
  verbatim copy, which is what the rule means.

### Added — a corpus that measures the selection

- **`tsumugi eval`.** Thirty labelled cases across ten genres, Japanese and
  English: a small generated corpus per case, a question, the fact that answers
  it, and planted adversaries. Facts carry their labels inline and the loader
  computes the offsets, so nobody counts characters by hand. **The corpus is
  labelled; the ideal output is not** (ADR-0013).
- Six metrics, all arithmetic over anchors. CI runs the fast tier and checks
  floors — deliberately looser than today's scores, because a gate set at the
  current number makes every honest experiment a build failure.
- `tools/generate_cases.py` generates the fixtures deterministically and an
  **oracle rejects any case that is broken before it ships**: a bad case fails a
  *correct* implementation, which is the expensive kind of failure. The oracle
  also runs in CI over what is committed.

### Fixed — found by that corpus, on its first run

- **Unconfirmed candidates were entering packages.** When the index proposed a
  document and confirmation found no occurrence of the query in it, the result
  was included anyway as an item covering the head of that document — dragging a
  whole unrelated document into the package. That contradicts ADR-0007 in its
  own words. Now an omission under `below_threshold`, with a reason. Four
  commits old; unit tests, CLI output and package validity all missed it.
- **Confirmation was weaker in English than in Japanese.** A Japanese query has
  no spaces, so the whole query is one needle; an English query was split into
  words, and a document sharing the word *nodes* with "how many nodes does the
  staging cluster have" confirmed. Needles for a space-separated query are now
  contiguous runs of two or more words: a phrase is evidence a document is about
  the query, a token is evidence it is written in the same language.
- Together: near-miss trap rate 96.7% → 36.7% → **10.0%**, with train and
  held-out agreeing. The residual is diagnosed in `application/search.py` and
  deliberately left alone.
- A line-ending bug in the harness itself: materialising a case wrote `

`
  under Windows while the spans were computed over `
`, so every offset was
  wrong and it looked like a retrieval failure.

### Added — an agent-facing surface, and the freeze

- **`tsumugi mcp`.** A read-only MCP server: JSON-RPC 2.0 over stdio,
  newline-delimited, on the standard library. Four tools — `search`, `context`,
  `trace`, `verify` — over the same application layer the CLI uses.
- **Nothing that writes is reachable.** A call naming `ingest` or `forget` is
  answered by saying the server is read-only, so an agent reaching for a write
  tool is told it does not exist rather than getting a generic failure.
- The transport survives bad input: a malformed line is answered with a parse
  error and the session continues. `params` must be absent or an object;
  positional parameters are refused rather than read as empty, because leniency
  there hides a client bug. Nothing but responses reaches stdout.
- **ContextPackage v1 is frozen.** A field may be added; none will be removed
  or change meaning inside version 1. Frozen once a *second program* had
  produced and consumed a package — the MCP server builds one in one process
  and verifies it in another, through JSON, with no shared objects — rather than
  when the calendar said v0.2. Readers still accept the `-draft` string that
  earlier packages carry; nothing writes it.

### Added — verification, and the loop closed

- **`tsumugi verify`.** The model quotes; tsumugi resolves the offsets
  (ADR-0004). Resolution is exact with one stated tolerance — NFKC, case-folded,
  whitespace runs collapsed — and nothing beyond it. A resolved citation comes
  back as an anchor into the real document, so `trace` can follow it to a line.
- **Four outcomes, kept apart.** `supported`, `unsupported`, `uncited`,
  `unverifiable`. A model that cites nothing failed differently from one that
  cites something that does not exist; and a package redacted irreversibly
  cannot be checked at all, which is neither of those.
- **Restore, then verify** (ADR-0009). A verifier that sees a protection record
  and holds no restorer refuses, naming the scope it would need — verifying
  as-is would report every honest citation as unsupported, and the failure
  would look exactly like a hallucination. A test asserts the property that
  matters: **protection never changes a classification.**
- **`ContextPackage.from_json`.** A contract only one program can produce is not
  a contract. The `package_id` is recomputed and checked, not trusted: an
  altered package is refused.
- **`tsumugi ledger`.** `context` opens an entry, `verify` closes it with which
  items were actually cited. It holds **no text** — identifiers, offsets, scores,
  counts and a hash of the query — checked by grepping the database file and by
  a schema test that fails if a text column is ever added. It is derived data:
  `--forget` deletes it and the corpus is untouched.
- The uncited share is `None` until something has been verified. Reporting 100%
  unused for a ledger nobody closed would be a lie about the tool rather than
  about the corpus.

### Added — the ContextPackage

- **`tsumugi context`.** Retrieve, confirm, rank, fit to a stated budget, and
  emit a package that says what is being sent, where each piece came from, what
  was left out, and which rule dropped it.
- **The contract is published**: `schemas/context-package-1.json`, with a
  conformance suite any producer can run. Three of its tests assert that the
  omission rules, provenance layers and budget units in the code are exactly
  those in the schema — there is no pydantic to derive one from the other, so
  those tests are all that keeps them in step.
- **`omissions[]` is required and enforced.** Every candidate that comes in
  leaves as an item or an omission, checked by a property test and asserted
  again in `build_context`, which raises rather than shipping a package that
  lost one. Any cap that bounded the search appears under `truncated_by_cap`.
- **Packages are reproducible.** `package_id` is a hash of everything that
  determined the package, excluding the timestamp. Same corpus, query and
  settings produce a byte-identical package.
- **Budgets name their unit.** `Budget.tokens(8000)` / `.characters(20000)` /
  `.bytes(65536)`; a bare number is refused. Characters and bytes are counted,
  tokens estimated — and a token budget with no measured error is refused at
  construction.
- **A CJK-aware token estimator**, fitted against `cl100k_base` and scored on a
  held-out split: p50 4.95%, p95 18.28%. A kanji costs six times a Latin
  character, which is the entire argument of ADR-0006. `tools/measure_cost.py`
  re-derives and re-scores it.
- **The prompt tells the model the selection has edges.** A rendered package
  with omissions carries a `# NOT INCLUDED` section, because a model cannot see
  the edge of a selection and will otherwise answer with the confidence of
  complete information.
- **kiseki's layering survives the crossing.** An interpretation stays an
  interpretation inside a package, must carry confidence, and is labelled as
  such in the rendered prompt. A fact carrying confidence is refused.

### Added — the evidence core

- **Domain**: `Span` (raises past the end rather than clamping), `ContentHash`
  (carries its algorithm), `Document`/`Section`/`Block` with identity split
  between path and revision, `Anchor` and three-way resolution, and
  offset-preserving NFKC normalization.
- **Anchors resolve, go stale, or fail.** Staleness is a distinct outcome:
  evidence taken before an edit was true when it was taken, and is reported as
  historical rather than as wrong or silently re-anchored.
- **Storage**: SQLite, schema 1, explicit migrations. Versions are append-only,
  so an anchor into an old revision keeps resolving. `forget` vacuums, and a
  test greps the database file afterwards to prove it.
- **Search in Japanese.** Script-aware character-bigram tokenization into FTS5.
  SQLite's default tokenizer scores 0/6 on a Japanese probe and `trigram` 4/6,
  failing on exactly the two-character compounds the language is built from;
  bigrams score 6/6. Measured first, in ADR-0007.
- **Search is two stages**: the index over-generates on purpose and every
  candidate is confirmed against the anchored text. A candidate that could not
  be confirmed is reported as such rather than dropped.
- **Parsers** for Markdown, plain text, source code and JSON, behind a registry
  a new format joins with one call. Every parser reports spans over the original
  string and never rewrites it — asserted by a property test over generated
  documents.
- **Ingestion reports everything**: added, revised, unchanged, skipped and
  failed. Files that look like credential stores are refused and named whether
  or not they were asked about.
- **CLI**: `ingest`, `search`, `trace`, `doctor`. Every command that touches the
  index prints where the index is.
- **`doctor`** separates what is measured, what is true by construction, and
  what is the operator's responsibility — and names the test behind each
  by-construction claim.

### Added — the gates, from the first commit

- `tests/test_architecture.py`: the layer table as an executable assertion, plus
  the stdlib-only rule for the domain, the no-network rule for the core, and the
  rule that only adapters may know about a sibling project.
- Five `import-linter` contracts, `mypy --strict`, `ruff`.
- CI asserts the runtime dependency count is zero by installing without extras
  into a clean environment.
- CI checks the runner's SQLite has FTS5 before running anything, so a build
  without it fails with one sentence rather than forty confusing errors.
- 305 tests, 93% coverage, no network and no model anywhere in the suite.

### Added — the documents

- The design, [docs/proposals/0001-the-design.md](docs/proposals/0001-the-design.md),
  and fourteen decisions in [docs/adr/](docs/adr/README.md).
- The ContextPackage contract, `tsumugi.context-package/1-draft`, in
  [docs/context-package.md](docs/context-package.md). Freezes at v0.2. Not
  implemented.
- [docs/threat-model.md](docs/threat-model.md). The index is a complete
  plaintext copy of whatever corpus it was built from, and that is the first
  thing it says.
- [docs/evaluation-corpus.md](docs/evaluation-corpus.md): the corpus is
  labelled, the ideal output is not. Not implemented.
- [docs/architecture.md](docs/architecture.md), written only once there was an
  architecture to describe.

### Measured

- [docs/measurements.md](docs/measurements.md), with `tools/measure_index.py`
  to reproduce it. The index is **2.62x the corpus** on 666 real documents, and
  terms per character tracks the CJK share. ADR-0007's flagged cost is real and
  affordable; roughly 45% of the index is stored document text rather than the
  search structure, so the bigram decision is not the larger half of the bill.
- Re-ingesting an unchanged corpus is **5.5x cheaper** than a cold build, which
  moves the incremental-ingestion trigger from about 2,200 documents to about
  12,000. Nothing to build yet.

### Decided

- Apache-2.0, for the explicit patent grant.
- Python 3.12+.
- The index lives at `~/.tsumugi/index.db` and never inside the corpus
  (ADR-0014).

### Not built yet

ContextPackage, budgets, selection, omission reporting, the ledger, claim
verification, the MCP server, and the kiseki and mamori adapters. A `forget`
command, and `--rebuild` for a tokenizer change.
