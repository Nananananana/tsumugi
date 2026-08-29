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
[`schemas/context-package-1.json`](../schemas/context-package-1.json) and ships
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

## Conformance

The conformance suite is `tests/test_contract_conformance.py`. It checks a
package against:

1. The JSON Schema in `schemas/context-package-1.json`
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
