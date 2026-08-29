# 6. The budget is an estimate whose error is measured

**Status:** accepted

## Context

A context budget is naturally expressed in tokens, because that is what the model
charges for and what its window is measured in. But a tokenizer is a dependency
with a vocabulary file, it differs per model family, and it changes when the
vendor says so. [ADR 0001](0001-the-domain-depends-on-nothing.md) forbids it in
the core.

The tempting resolution is a rule of thumb — "four characters per token" — applied
silently. For a Japanese-first library this is not approximately right, it is
wrong by a factor. Latin prose runs near four characters per token; Japanese runs
closer to one, and CJK text under an English-tuned tokenizer can go below that.
Using one coefficient for both produces a budget that is comfortable in English
and blows the window in Japanese, which is the direction that actually hurts.

The second temptation is to hide the estimate. A field named `tokens: 7412`
implies a measurement. If it is a guess, saying so is the whole difference between
a number a caller can use and a number that will mislead them exactly once,
expensively.

## Decision

`CostModel` is a port. The default implementation is a heuristic in the standard
library with **per-script coefficients** — Latin, CJK ideographs, kana, Hangul,
digits and punctuation counted separately, because they do not cost the same.

The unit is explicit at the call site:

```python
budget = Budget.tokens(8000)  # estimated
budget = Budget.characters(20000)  # exact
budget = Budget.bytes(65536)  # exact
```

**The heuristic's error is measured against real tokenizers in development-only
tests, over a mixed-script dataset, and the measured error travels in the
package:**

```json
"budget": {"unit": "tokens", "limit": 8000, "estimate": 7412,
           "estimator": "heuristic/cjk-aware@1",
           "measured_error": {"p50": 0.03, "p95": 0.11,
                              "against": "cl100k_base", "dataset": "ja-mixed-500"}}
```

When `unit` is `tokens` and `estimator` names a heuristic, `measured_error` is
**required** by the schema. A token count with no stated error is refused by the
conformance suite.

A caller who needs exactness installs a tokenizer adapter, and then
`measured_error` is absent because there is no error to report.

This follows `mamori`'s ADR-0023, where pointing the evaluation harness at a
feature that had only ever had a paragraph written about it showed the paragraph
was wrong. The lesson taken is not "measure more" but "publish the number,
including when it is unflattering".

## Consequences

A caller can size their own safety margin from a number rather than a feeling. A
p95 error of 11% means an 8000-token budget should be set at 7200 if overrun is
unacceptable.

Character and byte budgets are exact and always available, which makes them the
honest default for anyone who does not want to reason about a model's tokenizer.

The estimator is a versioned, named thing (`heuristic/cjk-aware@1`), so a package
built under an old estimator is identifiable and a change to it is a change to
`package_id` ([ADR 0003](0003-a-package-is-reproducible.md)).

## What it costs

A development-time dependency on at least one real tokenizer, and a dataset to
measure against — plus the discipline to re-run it when the heuristic changes.
Measured numbers that are never re-measured become stale claims, which is the
failure this ADR exists to prevent, arriving by a slower route.

The measurement is against *a* tokenizer, and there are several in use. The p95
against `cl100k_base` says little about a model tokenizing differently. Naming the
tokenizer in the field is the mitigation, and it is honest rather than sufficient.

Per-script coefficients are more code than one constant, and they are a model of
tokenizer behaviour that will drift as tokenizers change.
