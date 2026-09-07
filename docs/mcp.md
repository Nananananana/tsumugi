# The MCP surface, for a consumer holding the connection open

*What is true now.* The agent-facing interface: six read-only tools on
JSON-RPC over stdio, one process, any number of named indexes. The
ContextPackage contract is [context-package.md](context-package.md); this
document is everything *around* it that a caller needs and that the contract
deliberately does not say.

Written for `sora`, the family's orchestration layer, which holds the model and
the device and calls tsumugi only for *what to send and what was left out*.
Anything here that is specific to one consumer says so.

```json
{"mcpServers": {"tsumugi": {"command": "tsumugi", "args": ["mcp"]}}}
```

## The six tools

| Tool | Reads | Writes | Returns |
|---|---|---|---|
| `indexes` | every named index | nothing | `tsumugi.indexes/1-draft` (below) |
| `search` | the index | nothing | `tsumugi.search-hits/1-draft` (below) |
| `context` | the index | the ledger, unless `ledger: false` | a `tsumugi.context-package/1` document |
| `render` | nothing | nothing | the exact prompt for a package |
| `trace` | the store | nothing | where a quotation occurs |
| `verify` | the store | the ledger | four-way claim classifications |

Nothing that can change the corpus or the index is reachable. That is the rule
that bounds the damage rather than trying to prevent every case
([ADR-0012](adr/0012-an-agent-facing-surface.md)).

### `context`

```json
{"query": "...", "budget": "characters:4000", "min_score": 0.0,
 "instructions": "default", "ledger": true, "index": "personal"}
```

- **`instructions`** — `default` or `answering`. The default set addresses a
  person reading the prompt. `answering` asks the model for JSON claims with
  verbatim citations and carries the `OUTPUT_SCHEMA` section, **which is the
  only shape `verify` can check**. A consumer running its own model and wanting
  a meaningful verification result wants `answering`. They are different
  prompts, so they have different `package_id`s — an id that called them the
  same would be the thing that is wrong.
- **`ledger`** — `true` records the package in tsumugi's ledger. A caller that
  keeps its own record may pass `false`, so the question's hash is not kept in
  two places. Same reason the CLI has `--no-ledger`
  ([ADR-0011](adr/0011-record-what-was-sent-and-what-was-used.md)).
- **`index`** — a *name* from the server's configuration (below). Never a path.

The result is the package document, unchanged, at the top level. It validates
against the published schema; nothing is added to it.

### `render`

```json
{"package": "<the JSON string context returned>"}
```

Returns `{"package_id", "contract", "rendered"}`. **Use this rather than
composing a prompt from the package yourself.** A package is the record of a
prompt — its id, the ledger, `--json` all describe exactly what is about to be
sent — and a prompt with anything added afterwards makes all three of those
slightly false ([context-package.md](context-package.md)). The CLI has always
printed this; a consumer holding the connection open had no way to get it
without starting a Python process per turn.

`render` touches nothing — no index, no ledger — so it needs no `index` argument
and works on a package from any index or any machine.

### `search`

```json
{"query": "...", "limit": 10, "index": "news"}
```

Returns:

```json
{
  "contract": "tsumugi.search-hits/1-draft",
  "index": "news",
  "hits": [
    {
      "text": "...",
      "score": 4.1234,
      "confirmed": true,
      "anchor": {
        "document_id": "doc_...",
        "source_path": "notes/x.md",
        "section": "Fuel",
        "start": 60,
        "end": 106,
        "text_hash": "sha256:...",
        "document_hash": "sha256:..."
      }
    }
  ],
  "truncated": null
}
```

**`-draft` means what it says.** This is a named shape, not a frozen contract,
and it may change with notice. What *is* promised, and held by a test: each
hit's `anchor` has exactly the seven keys an item's anchor has in the package
contract (the package serialises with sorted keys, so the set is the promise
and the order is not). A consumer's anchor code — display, `trace`,
`end >= start` checks — works on both without a branch.

Each hit says which index it came from, in the envelope's `index` — `"default"`
when no name was given. A caller writing *"from your notes"* against *"from the
news"* reads it there rather than remembering which index it asked.

`confirmed: false` marks a passage the index proposed and confirmation could
not support. Searching is exploratory and shows these; a package never carries
one as an item ([ADR-0022](adr/0022-an-unconfirmed-candidate-is-an-omission-not-an-item.md)).

### `trace` and `verify`

Unchanged from before this document, except that `trace` takes `index`.
`verify` takes the package JSON string — the same string `render` takes — and
needs no index of its own. Its result carries
`contract: "tsumugi.verification-report/1-draft"`, so a caller keeping its own
record has a name to write down.

### `indexes`

Takes nothing. Returns which named indexes this server can reach:

```json
{
  "contract": "tsumugi.indexes/1-draft",
  "indexes": [
    {"name": "personal", "documents": 1234, "ingested_at": "2026-09-07T09:12:04Z"},
    {"name": "news", "documents": 0, "ingested_at": null},
    {"name": "archived", "documents": null, "ingested_at": null, "unavailable": "StorageError"}
  ]
}
```

**No path appears anywhere in this, including in `unavailable`.** That field
carries the *kind* of failure and nothing else, because the message it would
otherwise repeat names a file: `StorageError` means that profile has no index
yet. A held test renders the whole response and searches it for the directory
the indexes really live in.

An index that will not open is a **row**, not an error — a deleted profile must
not take the working ones down with it. Empty when the server was started with
no named indexes, which is also not an error: the default index is reached by
omitting `index` and is deliberately not listed here.

## Several indexes in one process

```bash
TSUMUGI_INDEXES="personal=/home/me/.tsumugi/index.db;news=/data/news/index.db" tsumugi mcp
```

Separated by the platform's path separator (`;` on Windows, `:` elsewhere).
Each tool that touches an index takes `index: "<name>"`; omitting it uses the
default index (`TSUMUGI_INDEX`, or `~/.tsumugi/index.db`). An unknown name is a
tool error that lists the known ones.

**Names, never paths, cross the tool boundary.** An agent that could pass a
path could point a read-only server at any SQLite file on the machine. The
person starting the process decides which corpora it may see.

## Errors, and how to tell them apart

A tool failure is a result with `isError: true`, not a protocol error: the
request was well-formed and the caller can act on the message. **The message
begins with the kind**, so a caller mapping failures onto its own states has
one word to match:

| Message begins | Meaning | A reasonable mapping |
|---|---|---|
| `StorageError:` | the index is missing, unreadable, or built by an older tokenizer or indexing rule | unavailable |
| `ConfigurationError:` | an unknown index name, instruction set, or unparseable budget | failed, and fix the call |
| `ValueError:` | a malformed package or answer, an empty query | failed |
| `UnsupportedContractError:` | a package from a contract this tsumugi does not know | failed |

### Exit codes, for the CLI

The same distinction, for a caller shelling out rather than holding a
connection. **A non-zero exit is not always a failure**, and one verb uses it
to mean something specific:

| Verb | 0 | 1 | 2 |
|---|---|---|---|
| `context` | at least one confirmed item | no confirmed items (a lead may still have been printed) | bad arguments |
| `verify` | at least one claim, **all** supported | anything else: an unsupported or uncited claim, **or no claims at all** | bad arguments |
| `ingest` | read something, nothing failed | a file failed to parse | the path does not exist |
| `indexes` | listed, including an empty list | — | bad arguments |
| `search`, `trace`, `ledger`, `doctor` | ran | — | bad arguments |

Two of these are ordinary rather than broken, and a caller that maps them to
*failed* will report a working system as such:

- **`context` exit 1** means the corpus had no confirmed answer. That is the
  library working. The JSON is still on stdout.
- **`verify` exit 1** is the common case for a model that cited badly, and its
  JSON is on stdout either way. It also covers *uncited* claims and an answer
  with **no claims at all** — `all()` over nothing is true, so "asserts
  nothing" would otherwise verify clean. Read `claims[]` to tell the three
  apart; the exit code deliberately does not.

**On the CLI, the reason for a failure is on stderr.** stdout carries the
command's output and nothing else: the `index:` and `corpus:` lines an ingest
prints go to stderr, because they say what is about to happen rather than what
happened. A consumer reading only stdout's last line used to see
`corpus: <path>` after a failure, in the same shape as a success summary, with
nothing saying to look elsewhere. Read the exit code, and read stderr when it
is non-zero.

### The catalogue

`tsumugi errors --json` prints every kind that can be reported, as
`tsumugi.errors/1-draft`:

```json
{
  "contract": "tsumugi.errors/1-draft",
  "by": "tsumugi/0.1.0.dev0",
  "errors": [
    {"kind": "StorageError", "exit_code": 2, "outcome": "unavailable",
     "retryable": false, "detail": "...", "detail_ja": "..."}
  ],
  "open_namespaces": []
}
```

**`retryable` is not a synonym for `unavailable`, and the difference is the
part only this library knows.** A missing or stale index is *unavailable* and
**not** retryable: the same call fails identically until somebody runs `ingest`
or `ingest --rebuild`. A model that is not listening is *unavailable* and **is**
retryable: it may be listening in a minute. A caller that retries the first
forever learns nothing.

`exit_code` is `null` for two kinds, and that is a claim rather than a gap:
`ValueError` and `UnsupportedContractError` reach a caller through an MCP tool
result, which has no exit code. On the command line they are either wrapped in
a `ConfigurationError` or left to traceback, because one raised deep in a call
is a bug and should be loud rather than tidy.

The catalogue is **exhaustive by test**: a walk over the package finds every
exception class it defines and fails if one is not listed, so adding an error
without cataloguing it breaks the build rather than passing quietly. It carries
**no values** — no paths, no examples, no message templates — because a
consumer folding failures together can only promise its own record holds
nothing sensitive if the vocabulary it reads holds nothing either.

**A `Kind:` line appears only when there is a kind.** `context` and `verify`
exit 1 for outcomes rather than failures and write nothing to stderr at all.
`ingest` exits 1 when a document failed to parse and writes its progress there,
so those lines are deliberately shaped **not** to be read as a kind — indented,
with no colon after the label:

```text
  index   /home/ada/.tsumugi/index.db
  corpus  /home/ada/notes
```

A consumer taking the first name-shaped line gets nothing from those, which is
correct: nothing failed that has a name. Held by a test that walks the stderr
of every non-zero exit and requires any leading identifier to be a catalogued
kind.

Protocol errors (JSON-RPC `error`, code `-32602`) are for malformed *requests*
— a missing required parameter, a string where a boolean was needed. Those are
the caller's bug, not the corpus's state.

## Protection applied after `render`

A consumer may run the rendered prompt through a redactor tsumugi never sees —
`sora` uses `mamori serve` over loopback and does not import it. It then holds
a protection record and a package whose `provenance.protection` is empty.

**Do not write the record into the package.** `package_id` is computed over the
package; a package edited afterwards has an id that no longer describes it. The
package is tsumugi's record of what it built; what happened to the prompt
afterwards is the record of whoever did it, and belongs beside the package —
in `akashi`'s `protection_by`, in your own job record — not inside it.
[ADR-0027](adr/0027-a-package-records-what-tsumugi-built.md) has the reasoning.

## Preferring newer passages

`--ordering recent`, or `TSUMUGI_ORDERING=recent`, with
`TSUMUGI_FRESHNESS` (default `0.3`) deciding how much of the ordering recency
gets. A document states its own date in front matter — `observed_at`,
`published`, `date`, `updated`, `fetched_at`, in that order of preference.

**It is relative, and that is a requirement rather than a simplification.**
Ranking by age needs a *now*, and then the same question over the same corpus
produces a different package on Wednesday than on Tuesday: `package_id` would
stop identifying a package (ADR-0003). So candidates are compared only against
each other, and the newest of them is the newest whenever you ask.

Two consequences to plan around:

- **A document that states no date is neither rewarded nor punished.** It takes
  its own relevance rank for the recency term. Pushing undated passages to the
  bottom would let one dated file rearrange a folder of notes.
- **With no dates anywhere it is exactly `score`.** Measured on the labelled
  corpus: recall 87.2%, precision 98.2%, trap 5.0% under both — identical,
  because **zero of that corpus's documents state a date.** So that measurement
  proves the reduction holds and says nothing at all about whether preferring
  newer passages is any good. Nobody here can answer that yet; a corpus with
  dates and a judgement about which answer is better would be needed.

The file's mtime is deliberately not used. A synced folder rewrites what it
touches, so mtime says when the sync ran, and a corpus arriving over Dropbox
would date every document to the same minute.

## Front matter is not evidence

A file that opens with

```
---
source_url: https://example.com/a
fetched_at: 2026-09-07
---
```

has its front matter read into `metadata` and **excluded from the index**. It
used to be indexed as ordinary text, and a package would hand `source_url:
https://example.com/a` back as a citable item. Front matter is what a document
says *about itself*; a citation resolving to it is a citation to bookkeeping.

The stored document is unchanged — the store keeps the file as it was read, so
anchors still resolve against the real bytes. Only what is searchable narrows.

**An index built before this change still holds those lines**, and its terms
would never say so, so the index now records the rule that built it as well as
its tokenizer. An older index is refused with a message naming `tsumugi ingest --rebuild`
rather than quietly searched.

## What is not here yet, honestly

- **`recency` is now `--ordering recent`**, and what it does *not* do is the
  part worth reading. See "Preferring newer passages" above. What is still
  missing: any measurement of whether it helps, because the labelled corpus
  contains **zero** documents that state a date.
- **Batch `search`.** One query per call. A process holding the connection open
  pays nothing for the round trip, so this is a convenience rather than a
  speed issue; ask if the round trips add up.
- **`omissions[].reason` is English.** It is prose for a person, written once.
  A consumer that shows it in another language translates `rule` (a closed
  enum) and shows `reason` as is; that is the intended division.
