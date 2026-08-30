# ContextPackage

**Contract:** `tsumugi.context-package/1`
**Status: frozen.** A field may be added; none will be removed or change
meaning inside version 1. A change a consumer must notice takes a new version.

It was frozen once a **second program had produced and consumed a package**,
not once the calendar said v0.2. The MCP server builds one in one process,
hands it to an agent, and verifies it in another — through JSON, with no shared
objects. That round trip is the evidence
[ADR 0002](adr/0002-the-context-package-is-a-document.md) wanted: *a class other
programs can import is a different kind of object from a document other programs
can produce.* Freezing before that would have been freezing on a schedule.

Packages written before the freeze carry `tsumugi.context-package/1-draft`.
Readers still accept it — refusing evidence over a version string would be the
wrong trade — and nothing produces it any more.

`tsumugi context --json` and the MCP `context` tool produce this. The schema is
[`src/tsumugi/schemas/context-package-1.json`](../src/tsumugi/schemas/context-package-1.json) and ships
with the package; the conformance suite is
`tests/test_contract_conformance.py`.

*This document is the contract, for producers and consumers alike. It is not a
description of tsumugi's internals, and a change to tsumugi that is not visible
here is not a change to the contract.*

---

## What it is

A ContextPackage is everything a language model needs for one question, plus an
account of where it came from and what was left behind.

It is a **document**, not an object: JSON, portable, versioned, and readable by a
program that has never heard of Python. tsumugi is the reference producer. It is
not required to be the only one ([ADR 0002](adr/0002-the-context-package-is-a-document.md)).

A consumer that holds a package needs nothing else. There is no hidden state, no
callback, no second request. That completeness is the property that makes the
package worth passing between programs — including across the boundary into
`mamori` and back.

---

## Shape

```json
{
  "contract": "tsumugi.context-package/1",
  "package_id": "sha256:9f2c...",
  "created_at": "2026-08-30T11:04:22+09:00",

  "query": "what did I decide about context budgets?",

  "instructions": {
    "role": "You answer from the provided context only.",
    "rules": [
      "Quote the text you rely on. Do not report offsets.",
      "If the context does not answer the question, say so."
    ]
  },

  "items": [
    {
      "item_id": "itm_01",
      "kind": "document_span",
      "text": "The budget unit is explicit at the call site...",
      "anchor": {
        "document_id": "doc_4b1e",
        "source_path": "notes/design/budgets.md",
        "section": "Budget",
        "start": 1204,
        "end": 1391,
        "text_hash": "sha256:...",
        "document_hash": "sha256:..."
      },
      "provenance": {
        "layer": "fact",
        "producer": "tsumugi.ingest/1",
        "observed_at": "2026-08-14T09:11:02+09:00"
      },
      "selection": {
        "rank": 1,
        "score": 0.81,
        "signals": ["heading_match", "term_density", "recency"]
      },
      "cost": 142
    }
  ],

  "omissions": [
    {
      "anchor": {"document_id": "doc_77a2", "start": 0, "end": 2210,
                 "source_path": "notes/archive/2024-budgets.md"},
      "rule": "budget_exhausted",
      "reason": "ranked 7th; 2210 estimated tokens would exceed the 8000 limit",
      "score": 0.44,
      "cost": 2210
    },
    {
      "anchor": {"document_id": "doc_11c9", "start": 300, "end": 480},
      "rule": "redundant_candidate",
      "reason": "94% overlap with itm_01; kept the earlier-dated source",
      "score": 0.79
    }
  ],

  "constraints": {
    "max_words": 400,
    "must_cite": true
  },

  "output_schema": {
    "claims": [{"text": "string", "citations": ["quoted string"]}]
  },

  "budget": {
    "unit": "tokens",
    "limit": 8000,
    "estimate": 7412,
    "estimator": "heuristic/cjk-aware@1",
    "measured_error": {"p50": 0.03, "p95": 0.11,
                       "against": "cl100k_base", "dataset": "ja-mixed-500"}
  },

  "provenance": {
    "tsumugi_version": "0.2.0",
    "corpus_state": "sha256:...",
    "settings_hash": "sha256:...",
    "providers": ["filesystem", "kiseki@0.10.0"],
    "protection": null
  }
}
```

---

## Field rules

### `contract`

Required, first. A consumer that does not recognise the value **refuses the
package** rather than guessing at it. Fail closed.

### `package_id`

`sha256` over the canonical serialization of everything that determined the
package: the query, the corpus state, the settings, the provider outputs and the
tsumugi version — but not `created_at`, which is why timestamps are excluded from
the hash.

Two runs with the same inputs produce the same `package_id` and byte-identical
output ([ADR 0003](adr/0003-a-package-is-reproducible.md)).

**`instructions` and `output_schema` are among those inputs.** A package built
for a person to read and one built for a program to check carry different
instruction sets, so they hash differently over identical evidence. That is the
intended reading: they are different prompts. Anything that wants "the
selection for this question" regardless of who it was addressed to should
compare `items[]` and `omissions[]`, which do not vary
([ADR 0017](adr/0017-the-instruction-set-is-a-parameter.md)).

### `instructions` and `output_schema`

What the package tells the model, as data rather than as string-building at the
edge. **`render()` emits the whole prompt**, including these — a consumer that
appends its own paragraph on the way out has a package that no longer describes
what was sent, and the ledger, `--json` and every "look at what is about to go"
claim quietly stop being exact.

Two sets ship: a default for a human reader, and an answering set that pairs
with a JSON `output_schema` so `verify` has something to check. Producers may
supply their own; consumers must render whatever is there and must not require
either of these fields to be present.

### `items[]`

Each item is a piece of context that will be sent.

- `text` is what will be rendered. It is a copy, and `anchor` is how it is checked.
- `anchor` locates it. `text_hash` covers `text`; `document_hash` covers the whole
  source document as it was when read.
- `provenance.layer` is one of `fact`, `measure` or `interpretation`. The
  distinction is kiseki's and it survives the crossing: **an interpretation stays
  an interpretation inside a package.** A model asked to reason over the package
  can be told which is which; a package that flattened them would be laundering.
- `selection.signals` names why this item scored what it did. A ranker that cannot
  say why is a ranker nobody can debug.
- `cost` is in the unit named by `budget.unit`.

### `omissions[]`

**Required, and empty only when nothing was dropped.**

Every candidate that was considered and not included appears here with the rule
that dropped it. The defined rules:

| `rule` | Meaning |
|---|---|
| `budget_exhausted` | Ranked, would not fit |
| `below_threshold` | Scored under the relevance floor |
| `redundant_candidate` | Near-duplicate of an included item |
| `stale_anchor` | The source document changed since it was indexed |
| `excluded_by_filter` | Removed by an explicit user filter or ignore rule |
| `truncated_by_cap` | Cut by a top-N or sampling limit |

`truncated_by_cap` exists so that no cap can be silent. If the implementation
looked at only the top 200 candidates, the package says so
([ADR 0005](adr/0005-selection-is-a-report.md)).

An omission carries an anchor and a reason but **not the omitted text**. Copying
what was deliberately not sent into the thing being sent would defeat the point.

### `budget`

`unit` is one of `tokens`, `characters` or `bytes`. When `unit` is `tokens` and
`estimator` names a heuristic rather than a real tokenizer, `measured_error` is
required ([ADR 0006](adr/0006-the-budget-is-an-estimate.md)). A token count with
no stated error is a number pretending to be a measurement.

### `provenance.protection`

`reversible` decides what a verifier *does*, so it is required on the wire and
**defaults to `false`** everywhere a default exists
([ADR 0020](adr/0020-a-protection-is-irreversible-until-it-says-otherwise.md)).
Getting it wrong in the `true` direction reports honest citations as
`unsupported` — a false accusation, and a silent one. Wrong the other way
reports everything as `unverifiable`, with its reason. Only a redactor knows
which it is, so a producer should ask it after protecting rather than assume.

`null` when the package has not passed through a redactor. Otherwise it names the
redactor and the scope:

```json
"protection": {"by": "mamori@0.12.0", "scope": "sess_2f11", "reversible": true}
```

A verifier that sees a non-null `protection` and has no way to restore
**refuses to verify** rather than reporting every citation as unsupported
([ADR 0009](adr/0009-restore-before-you-verify.md)). This is the field that makes
that failure loud.

---

## Getting the schema

It ships **inside the package**, so a consumer does not need the network:

```python
import tsumugi

schema = tsumugi.contract_schema()  # parsed
raw = tsumugi.contract_schema_text()  # the bytes, to hash or to vendor
```

That is not a convenience wrapper. The promise used to live in a comment in
`pyproject.toml`, kept by a build rule no code exercised and which did not
apply to editable installs at all — so deleting it would have broken the
promise silently and permanently with every test still green. It has an API
because an API is a thing tests can hold on to.

Vendoring the file directly is fine and is what the sibling projects do. Take
it from `src/tsumugi/schemas/`, and record the commit.

## A worked example

`fixtures/seam/` holds one corpus, one question, and the package tsumugi really
emits for them — for a consumer that wants to test against this contract
without importing tsumugi. Vendor it together with the schema: the schema says
what the shape is, and the fixture is one instance a producer produced.

It is byte-identical across runs. `created_at` is pinned, and it is the one
field deliberately outside `package_id`, which is what makes pinning it safe
rather than a lie — the fixture carries the id these inputs really produce, so
a consumer compares the whole document and skips nothing.
`tsumugi context --json --at <ISO8601>` does the same from the command line.

## What `document_hash` is a hash of

A producer that wants its own hashes to agree with tsumugi's — a sync tool
handing over a corpus, say — needs this stated, and it was not written down
anywhere until a seam test asked.

> **`document_hash` is `sha256` of the file's bytes, with a UTF-8 byte order
> mark removed if one is present. Nothing else is normalised.**

In particular **line endings are preserved**. tsumugi reads bytes and decodes
them; it does not do universal-newline translation, so a CRLF file hashes as
CRLF. A producer that hashes raw bytes agrees with tsumugi on every file that
has no BOM, and on a BOM'd file iff it strips the BOM too.

The BOM is removed because it is an encoding artefact rather than a character
in the document, and leaving it in shifts every offset in the file by one. A
consequence worth knowing: **the same content with and without a BOM has the
same `document_hash`**, which is usually what you want and is a difference a
byte-for-byte comparison would report.

Measured, not asserted: `tests/test_ingest_and_search.py` pins all three cases
against `hashlib.sha256` computed outside tsumugi.

## What the schema cannot say

**`end >= start` is not expressible.** JSON Schema 2020-12 cannot compare two
properties of the same object, so a package whose anchor ends before it starts
**validates**. A consumer that slices text with those offsets has to check for
itself.

The producer cannot emit one — `Span` refuses at construction — so the
invariant lives there, and `tests/test_contract_conformance.py` asserts both
halves so the division of labour is stated rather than assumed.

## Conformance

The conformance suite is `tests/test_contract_conformance.py`. It checks a
package against:

1. The JSON Schema in `src/tsumugi/schemas/context-package-1.json`
2. `sum(item.cost) <= budget.limit`
3. Every `anchor` resolves in the corpus it names, and `text_hash` matches `text`
4. Every omission names a defined `rule` and a non-empty `reason`
5. Re-running the producer with the same inputs yields the same `package_id`
6. No `item.text` appears in `omissions`

Rules 1–4 and 6 can be checked against a package alone. Rules 3 and 5 need the
corpus and the producer.

A seventh check has no number because it is about the repository rather than a
package: three tests assert that the omission rules, the provenance layers and
the budget units in the code are exactly those in the schema. There is no
pydantic to derive one from the other ([ADR 0001](adr/0001-the-domain-depends-on-nothing.md)),
so those tests are the only thing keeping the two representations in step.

A producer that is not tsumugi passes the same suite. That is the whole point of
writing the contract down.

---

## What this contract does not carry

Said plainly, because the omissions in a contract matter as much as the fields:

- **No model output.** A package is what goes *to* a model. Answers, claims and
  verification results are a separate document.
- **No embeddings.** Derived, optional, and large. If a consumer wants them it can
  compute them.
- **No file contents beyond the selected spans.** A package is a selection, not an
  archive.
- **No credentials, endpoints or keys.** Where the package is sent is the caller's
  business and is not recorded here.
- **No promise about truth.** A package says where text came from. It says nothing
  about whether the text is correct.
