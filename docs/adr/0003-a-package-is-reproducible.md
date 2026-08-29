# 3. A package is reproducible, and its id is its inputs

**Status:** accepted

## Context

Retrieval systems are hard to trust because they are hard to re-run. You change a
ranking weight, the answers change, and you cannot tell whether the change was an
improvement, a different random tie-break, or a document that happened to be
edited that morning.

Three things people will want, all of which are the same thing:

- **caching** — do not rebuild a package that would come out identical
- **diffing** — show what changed between two runs, and why
- **regression testing** — assert that a ranking change did what was intended

Each is trivial with a deterministic build and each is impossible without one.

## Decision

Same corpus state, same query, same settings, same version → **byte-identical
package**.

`package_id` is `sha256` over the canonical serialization of everything that
determined the output: the query, the corpus state hash, the settings hash, the
provider outputs, and the tsumugi version. `created_at` is excluded from the hash,
which is the only reason a timestamp is allowed in the document at all.

Concretely, this forbids:

- iteration over unordered containers where order reaches the output
- score ties broken by anything other than a stated deterministic rule
- wall-clock time as a ranking signal, except by way of an explicit corpus-state
  timestamp that is itself part of the hash
- any model in the selection path

The last one is the significant one. It means the ranker cannot call an LLM, and
that is a feature, not a limitation to be lifted later: a selection that changes
between identical runs cannot be reasoned about.

## Consequences

`tsumugi context` twice returns the same `package_id`, and a differing id means
something in the corpus or the settings actually changed. That is a diagnostic,
not just a hash.

Caching is a lookup on `package_id`. There is no cache-invalidation problem
because there is nothing to invalidate.

Evaluation can compare two rankers over the same corpus and attribute every
difference to the change under test.

A conformance rule falls out for free: run the producer twice and compare.

## What it costs

Ordering discipline everywhere, forever. Every dict iteration, every `set` that
reaches an output, every sort without a full key is a latent violation, and the
one that slips through will produce a flaky test in an unrelated part of the
suite. The property test that runs a build twice is the only reliable guard, and
it has to be cheap enough to run always.

Ranking signals that would genuinely help — a model's judgement of relevance, an
embedding computed on the fly — are excluded from the deterministic path. If they
are ever wanted, they will have to enter as a *precomputed, hashed* input, which
is more work than calling a model in the ranker.
