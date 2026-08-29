# Architecture

*This is a current-state document: it describes what the code does today, at
v0.1.0.dev0. Where it disagrees with the code, one of the two is a defect. See
[docs/README.md](README.md).*

What is **planned** and not built is in
[proposals/0001-the-design.md](proposals/0001-the-design.md). Most of that
document is still unbuilt: there is no ContextPackage, no budget, no selection,
no ledger, no MCP server and no adapter to either sibling project yet.

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

Four commands: `ingest`, `search`, `trace`, `doctor`.

## Layers

```text
interfaces ──> application ──> domain
                    │              ▲
                    │              │
                    └──> ports <───┴── infrastructure
```

| Layer | Holds | May import |
|---|---|---|
| `domain/` | `Span`, `ContentHash`, `Document`/`Section`/`Block`, `Anchor` and resolution, offset-preserving normalization | **stdlib only** |
| `errors.py` | Every exception the library raises | nothing |
| `ports/` | `Parser`, `Tokenizer`, `DocumentStore`, `Index`, `CostModel` protocols | `domain`, `errors` |
| `infrastructure/` | Parsers and their registry, the filesystem walk, SQLite store, FTS5 index, bigram tokenizer | `domain`, `ports`, `errors` |
| `application/` | `ingest_paths`, `search`, `trace_quotation` | `domain`, `ports`, `errors` |
| `config.py` | `TsumugiConfig`, and where the index lives | `domain`, `ports`, `application`, `infrastructure` |
| `interfaces/cli/` | Argument parsing, output. The only composition root | everything above |

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

`DocumentStore`, `Index` and `CostModel` are the other three. `CostModel` has no
implementation yet; it is defined because the budget is v0.2 work and the shape
is settled ([ADR 0006](adr/0006-the-budget-is-an-estimate.md)).

Block kinds are an open registry rather than an enum, so a parser for a format
nobody has written yet gets a kind without patching the library.

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
| `test_cli.py` | Every command, and the things `doctor` must never fail to say |
| `test_leakage.py` | Greps logs, reprs and tracebacks for document text |

305 tests, 93% line coverage. Every test runs with no network, no model and no
third-party package beyond the test tools themselves.
