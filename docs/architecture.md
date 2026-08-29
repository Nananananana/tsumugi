# Architecture

*This is a current-state document: it describes what the code does today, at
v0.1.0.dev0. Where it disagrees with the code, one of the two is a defect. See
[docs/README.md](README.md).*

What is **planned** and not built is in
[proposals/0002-what-building-it-taught.md](proposals/0002-what-building-it-taught.md),
which revises 0001's roadmap from what building it cost. Redundancy marking is
now done; next are more evaluation cases, then templates and the sibling
adapters.

## What exists

```text
   a folder                                            a quotation
       │                                                    │
       ▼                                                    ▼
  ┌─────────┐   ┌──────────┐   ┌─────────┐          ┌───────────────┐
  │  walk   │──>│  parse   │──>│  store  │          │    resolve    │
  │ + skip  │   │ structure│   │ +index  │◀────────▶│  exact only   │
  │reported │   │  spans   │   │versioned│          │ resolved/stale│
  └─────────┘   └──────────┘   └─────────┘          └───────────────┘
                                    ▲
                                    │
                          ┌─────────┴─────────┐
                          │ search: candidates │
                          │  then confirmation │
                          └────────────────────┘
```

Ten commands: `ingest`, `search`, `context`, `verify`, `trace`, `forget`,
`ledger`, `mcp`, `eval`, `doctor`.

`context` is the one the library is for. It retrieves, confirms, ranks, fits to
a stated budget, and emits a **ContextPackage** — a portable JSON document that
says what is being sent, where each piece came from, what was left out, and
which rule dropped it. The contract is [context-package.md](context-package.md)
and the schema is [`schemas/context-package-1.json`](../schemas/context-package-1.json).

## Layers

```text
interfaces ──> application ──> domain
                    │              ▲
                    │              │
                    └──> ports <───┴── infrastructure
```

| Layer | Holds | May import |
|---|---|---|
| `domain/` | `Span`, `ContentHash`, `Document`/`Section`/`Block`, `Anchor` and resolution, normalization, `Budget`, `Omission`, `ContextItem`, `ContextPackage`, budget fitting, quotation matching, `Claim` | **stdlib only** |
| `errors.py` | Every exception the library raises | nothing |
| `ports/` | `Parser`, `Tokenizer`, `DocumentStore`, `Index`, `CostModel` protocols | `domain`, `errors` |
| `infrastructure/` | Parsers and their registry, the filesystem walk, SQLite store, FTS5 index, bigram tokenizer, cost models | `domain`, `ports`, `errors` |
| `application/` | `ingest_paths`, `search`, `build_context`, `verify_answer`, `trace_quotation` | `domain`, `ports`, `errors` |
| `config.py` | `TsumugiConfig`, and where the index lives | `domain`, `ports`, `application`, `infrastructure` |
| `interfaces/cli/` | Argument parsing, output. A composition root | everything above |
| `evaluation/` | The labelled corpus, its loader, the six metrics, the runner | everything above |
| `interfaces/mcp/` | JSON-RPC on stdio, four read-only tools. The other composition root | everything above |

**This table is executable.** `tests/test_architecture.py` parses every module
and asserts it; `.importlinter` asserts the direction. A diagram that stops
matching the code turns the build red rather than quietly becoming fiction.
This is `mamori`'s ADR-0017, adopted.

Five contracts hold in CI:

| Contract | Says |
|---|---|
| Layer dependency direction | The arrows above, and only those |
| The domain knows nothing below it | `domain/` imports no other layer |
| Nothing in the core touches the network | No `socket`, `ssl`, `http`, `urllib`, `asyncio` outside `interfaces/` |
| Only the adapters may know about a sibling | `kiseki` and `mamori` may be imported only from `infrastructure/adapters/` |
| The domain does not touch the filesystem | No `pathlib`, `os`, `sqlite3`, `argparse` in `domain/` |

A sixth rule lives only in the test, because import-linter cannot express it:
**the domain imports nothing outside the standard library**
([ADR 0001](adr/0001-the-domain-depends-on-nothing.md)).

## Where the guarantees live

All in `domain/`, none in a swappable component. A parser, an index or a
tokenizer is a *proposer*; replacing any of them cannot change an answer below.

| Decision | Module |
|---|---|
| Whether a span slices back to the text that was anchored | `domain/anchor.py` |
| Whether an anchor is resolved, stale or unresolvable | `domain/anchor.py` |
| Whether a document's recorded hash matches its content | `domain/document.py` |
| Whether a normalized offset maps back to the original | `domain/text.py` |
| Whether a span is inside the document at all | `domain/span.py` |
| Whether a package is well-formed enough to exist | `domain/package.py` |
| That every candidate leaves as an item or an omission | `domain/assembly.py` |
| Whether a quotation is really in the text that was sent | `domain/matching.py` |
| Whether two passages are near-duplicates | `domain/redundancy.py` |
| Whether the file a passage came from has changed | `infrastructure/freshness.py` |
| Whether a claim is supported, unsupported, uncited or unverifiable | `domain/claim.py` |

## Key types

**`Span`** — `[start, end)` in Python string indices. `slice()` **raises** past
the end rather than clamping: Python's silent clamp turns a bad anchor into
plausible wrong text.

**`ContentHash`** — a digest that carries its algorithm, rendering as
`sha256:9f2c…`. A bare hex string is a hash whose algorithm is an assumption.

**`Document`** — identity split in two. `document_id` is derived from the source
path and survives an edit; `version` is the content hash and changes with every
edit. An anchor names both ([ADR 0010](adr/0010-the-index-stores-the-text.md)).

**`Anchor`** — a span plus both hashes. `Anchor.into(document, span)` is the only
supported constructor, so the correct thing is the easy thing.

**`Resolution`** — `RESOLVED`, `STALE` or `UNRESOLVABLE`. The middle one is the
point: evidence taken before an edit was true when it was taken. `ok` is `True`
only for `RESOLVED`, so treating historical evidence as current is always a
visible decision.

## Search is two stages

```text
   query
     │
     ▼
  ┌──────────────────────────────┐
  │ bigram terms -> FTS5 -> bm25 │   over-generates on purpose
  └──────────────┬───────────────┘
                 │  candidates
                 ▼
  ┌──────────────────────────────┐
  │ exact match against the text │   the store already holds it
  │ the store holds, then anchor │
  └──────────────┬───────────────┘
                 │  results, each with a resolvable anchor
                 ▼
```

SQLite's default FTS5 tokenizer indexes a whole Japanese sentence as one token,
so a search returns nothing forever and raises nothing. `trigram` cannot match a
two-character query, and two-character compounds are the backbone of written
Japanese. Both were measured; the numbers are in
[ADR 0007](adr/0007-index-japanese-by-bigram.md).

So text is tokenized into overlapping character bigrams **before** SQLite sees
it, per script run — Latin words are not cut up, because spaces already say
where they end. FTS5's `unicode61` then only has to split on the spaces the
tokenizer put in.

The bigram index does not know where words end, so it returns documents
containing `京の` for a query of `東京`. Confirmation against the anchored text
costs one string search on content that is already loaded. **Approximate
retrieval confirmed by exact evidence** is the shape of the whole library.

A candidate the index proposed and confirmation could not find is reported as
`unconfirmed` rather than dropped, so over-generation is visible instead of
mysterious.

## Storage

One SQLite file, holding both the documents and the search index: one thing to
back up, one thing to delete, one thing to keep out of a synced folder.

Schema version 1, migrated explicitly. Opening an index written by a newer
tsumugi is refused rather than attempted.

**Versions are append-only.** Ingesting an edited file adds a revision and moves
the `is_current` flag; nothing is overwritten, because an anchor into the old
revision has to keep resolving. `forget()` deletes every revision and then
`VACUUM`s — deleting rows leaves the text in free pages, and for a file holding
a person's notes "removed from the table" is not removed. A test asserts this by
grepping the database file afterwards.

## Extension points

Each is a `Protocol`, so an implementation is anything with the right shape and
never has to import tsumugi (`kiseki`'s ADR-0004).

```python
class Parser(Protocol):
    name: str
    suffixes: Sequence[str]
    media_type: str

    def parse(self, content: str) -> ParsedDocument: ...
```

Reports **spans over the original string** and never rewrites it. This is what
makes a hand-written Markdown reader an acceptable trade against a dependency: a
parser that misreads a nested list produces worse *sections*; it cannot produce a
wrong *anchor*. A property test over generated documents asserts that no parser
ever reports a span outside the document.

Registering a format is one call, and a suffix belongs to one parser — stealing
a claimed one needs `replace=True`, so an override is deliberate:

```python
register_parser(OrgModeParser())
```

```python
class Tokenizer(Protocol):
    name: str

    def index_terms(self, text: str) -> Sequence[str]: ...
    def query_terms(self, query: str) -> Sequence[str]: ...
```

The seam a morphological analyser (MeCab, Sudachi) would arrive through, if the
retrieval dataset ever shows bigrams costing real recall. An index records which
tokenizer built it and refuses to be searched by another, because the terms
would not line up and the failure would look like an empty corpus.

`DocumentStore`, `Index`, `CostModel`, `Redactor`, `LedgerStore` and
`FreshnessCheck` are the others. `CostModel` has
three implementations: `CharacterCost` and `ByteCost` count exactly, and
`HeuristicTokenCost` estimates tokens by script class and reports its own
measured error ([ADR 0006](adr/0006-the-budget-is-an-estimate.md), numbers in
[measurements.md](measurements.md)).

Block kinds are an open registry rather than an enum, so a parser for a format
nobody has written yet gets a kind without patching the library.

## Building a package

```text
   question + budget
          │
          ▼
   search (two stages, above)
          │  candidates, each already anchored
          ▼
   ┌──────────────────────────────────────────────┐
   │ fit_to_budget                                │  domain/assembly.py
   │   best-first, deterministic to the last key  │
   │   one oversized candidate does not stop the  │
   │     fill -- a later smaller one may fit      │
   │   EVERY candidate leaves as an item or an    │
   │     omission, with the rule that dropped it  │
   └──────────────────┬───────────────────────────┘
                      ▼
   ContextPackage: items, omissions, budget report, provenance
```

The invariants are checked at construction, so a package that would be wrong
cannot be built:

| Refused | Because |
|---|---|
| An item whose text length differs from its anchor's span | The anchor would not describe what is sent, and every citation into it would be meaningless |
| Items whose costs do not sum to the reported estimate | A budget that does not add up cannot be checked by anyone |
| An estimate above the limit | A package over its own budget is not a package |
| A token budget with no `measured_error` | An estimate that does not say how wrong it is misleads a caller once, expensively |
| An interpretation with no confidence, or a fact with one | `kiseki`'s layering survives the crossing |
| The same passage in both `items` and `omissions` | A package cannot both send and withhold one passage |
| An unrecognised `contract` string | Fail closed |

`build_context` additionally asserts that every candidate reached the package as
one or the other, and raises rather than shipping if it did not. That failure
would be the exact thing [ADR 0005](adr/0005-selection-is-a-report.md) exists to
prevent, so it stops the build rather than going out quietly.

## Verifying an answer

```text
   model's answer (JSON: claims, each with quotations)
          │
          ▼
   ┌──────────────────────────────────────────────┐
   │ restore, IF the package records a protection │  ADR-0009
   │   no restorer + reversible  -> REFUSE loudly │
   │   irreversible              -> unverifiable  │
   ├──────────────────────────────────────────────┤
   │ resolve each quotation against the text that │  domain/matching.py
   │   was sent. NFKC, case-folded, whitespace    │
   │   runs collapsed. Nothing else.              │
   ├──────────────────────────────────────────────┤
   │ classify                                     │  domain/claim.py
   └──────────────────┬───────────────────────────┘
                      ▼
    supported / unsupported / uncited / unverifiable
```

**The model quotes; tsumugi resolves** ([ADR 0004](adr/0004-the-model-quotes.md)).
Models cannot count characters, so asking for offsets produces coordinates that
are plausible, self-consistent, and wrong by enough to point at a different
sentence.

The four outcomes are kept apart on purpose. `uncited` is not `unsupported`: a
model that cites nothing has failed differently from one that cites something
that does not exist. `unverifiable` is neither: when a package was redacted
irreversibly the citation *cannot* be checked, and calling that `unsupported`
would report an honest citation as a fabricated one.

A resolved citation comes back as an **anchor into the real document**, so
`trace` can follow it to a line in a file.

**Restore, then verify.** If a redactor rewrote the package, the model was shown
`<PERSON_001>` and quoted `<PERSON_001>`, while the anchors point at the original
value. Verifying without restoring first fails for every honest citation, and
the failure looks exactly like a hallucination. A verifier that sees
`provenance.protection` and holds no restorer refuses, naming the scope it would
need. A test asserts the property that matters: **protection never changes a
classification**.

## The ledger

`build_context` opens an entry; `verify` closes it with which items were
actually cited. Over time it answers what no synthetic dataset can: which
documents are sent constantly and cited never
([ADR 0011](adr/0011-record-what-was-sent-and-what-was-used.md)).

Two rules, checked rather than promised:

- **No text.** Identifiers, offsets, scores, counts and a hash of the query.
  Never the question, the document or the answer. A test greps the database file
  for both; another asserts the schema has no text column beyond identifiers, so
  adding `query TEXT` later is a build failure rather than a quiet change of
  what the ledger is.
- **Derived.** `tsumugi ledger --forget` deletes it, and the corpus is
  untouched. It never feeds back into a build — a ledger that influenced ranking
  would make packages depend on their own history, and reproducibility would be
  gone.

`usage()` returns `None` for the uncited share when nothing has been verified.
Reporting 100% unused for a ledger nobody closed would be a lie about the tool
rather than about the corpus.

## The agent-facing surface

`tsumugi mcp` speaks JSON-RPC 2.0 over stdio — newline-delimited JSON, one
object per line. That is the whole framing, which is why an agent surface costs
no dependency ([ADR 0012](adr/0012-an-agent-facing-surface.md)).

| Tool | Returns |
|---|---|
| `search` | ranked spans with anchors |
| `context` | a full ContextPackage, **including `omissions[]`** |
| `trace` | from a quotation back to document, section and line |
| `verify` | claim classifications for an answer |

Three constraints, all of them tested:

**Read-only.** `ingest` and `forget` are not exposed, and a call naming one is
answered by saying the server is read-only rather than by a generic failure.
That rule bounds the damage instead of trying to prevent every case; adding a
fifth tool that writes would end it.

**The full package, including omissions.** An agent that cannot see the edge of
a selection has the same problem as a person who cannot.

**The same application layer as the CLI.** Both are thin shells over the same
use cases. A behaviour available in one and not the other is a defect.

The transport survives bad input: a malformed line is answered with a parse
error and the session continues. `params` must be absent or an object —
positional parameters are refused rather than read as empty, because leniency
there hides a client bug. Nothing but responses reaches stdout; diagnostics go
to stderr, since a stray line corrupts the stream and the client sees an error
it cannot attribute.

Document text goes out to the caller. Nothing coming back is executed, fetched
or written.

## Measuring the selection

`tsumugi eval` scores the selection against thirty labelled cases: a small
generated corpus per case, a question, the fact that answers it, and planted
adversaries. **The corpus is labelled; the ideal output is not** — there is no
single correct structured prompt, so scoring distance to one measures
conformity ([ADR 0013](adr/0013-label-the-evidence-not-the-ideal-answer.md)).

Facts carry their labels inline and the loader computes the offsets:

```markdown
{{F:tent-weight}}テントの重量は2.4kg、二人用{{/F}}。
```

Six metrics, all arithmetic over anchors. The current numbers, and what they do
not say, are in [measurements.md](measurements.md).

CI runs the `ci` tier and checks **floors** — recall ≥ 95%, trap rate ≤ 20%,
budget and reproducibility exact — deliberately looser than today's scores. No
model runs: the fixtures were authored once and committed, and an oracle checks
every case before it ships, because a broken case fails a *correct*
implementation.

## Configuration

```text
built-in defaults  ->  TSUMUGI_* env  ->  command-line flags
```

Unknown keys are refused rather than ignored: a typo in a setting that silently
does nothing is the worst available outcome. The index lives at
`~/.tsumugi/index.db` unless told otherwise, and never inside the corpus
([ADR 0014](adr/0014-the-index-does-not-live-beside-the-corpus.md)).

## Testing

| File | Covers |
|---|---|
| `test_architecture.py` | The layer table, the stdlib-only rule, the network rule, the sibling rule |
| `test_domain_values.py` | Span, hash, document and section invariants |
| `test_anchor.py` | Resolution's three outcomes; Hypothesis over the slice-back invariant |
| `test_normalization.py` | The offset map, including forms that expand |
| `test_tokenization.py` | Script runs, bigrams, and the over-generation being deliberate |
| `test_parsers.py` | Each format, and the property that no parser reports an impossible span |
| `test_storage_and_index.py` | Versions, staleness, `forget` leaving nothing recoverable, FTS5 edge cases |
| `test_ingest_and_search.py` | The walk, what gets skipped and reported, search, trace |
| `test_budget_and_cost.py` | Units, the script-aware estimator, and that no script is free |
| `test_context_package.py` | Every package invariant; Hypothesis over "nothing is dropped without a reason" |
| `test_contract_conformance.py` | Real packages against the published JSON Schema, and that the schema and the enums have not drifted |
| `test_verification.py` | The four outcomes, the matching tolerance, and that redaction never changes a verdict |
| `test_ledger.py` | Opening, closing, and that no text reaches the file |
| `test_mcp.py` | The transport, the read-only rule, and context→verify entirely over the protocol |
| `test_evaluation.py` | The markup, the fixtures, the metrics, and a run end to end |
| `test_freshness.py` | Whether the file a passage came from has changed |
| `test_adapter_mamori.py` | ADR-0009 against the real redactor. Skipped when `mamori` is absent |
| `test_cli.py` | Every command, and the things `doctor` must never fail to say |
| `test_leakage.py` | Greps logs, reprs and tracebacks for document text |

690 tests, 92% line coverage. Every test runs with no network, no model and no
third-party package beyond the test tools themselves.
