# ADR-0016: The network lives in one place

*Accepted 2026-08-30.*

## The question

tsumugi builds a package and checks an answer. Both halves are deterministic
and neither needs a model. But the loop does not close without one: `verify`
takes an answer, and an answer takes something that answers. Until now the
library handed you a rendered prompt and wished you luck, which meant that the
one step where a mistake is expensive — deciding where the text goes — was
the one step tsumugi had nothing to say about.

The alternative to shipping a provider is not "no network". It is a network
call written by whoever needed one, in a script, with the URL from a tutorial.

## The decision

There is an `LLMProvider` port and one adapter for it, `OllamaProvider`, and
**`infrastructure/adapters/` is the only package in tsumugi permitted to import
`socket`, `ssl`, `http`, `urllib` or `asyncio`.** The rule is enforced by an
import-linter contract and by an assertion in `tests/test_architecture.py`
that walks the source and fails on a network import anywhere else.

The contract lists the infrastructure sub-packages one by one rather than
excluding `adapters` from the parent. That is more to maintain — a new
sub-package has to be added — and that is the point: the same obligation the
layer table carries, for the same reason. A carve-out written as an exclusion
grows silently. One written as an allow-list has to be argued for.

Three things follow from where the boundary was drawn:

**The provider is asked for text and never for a decision.** It does not rank
candidates, does not choose what goes into a package, and does not resolve a
citation. The worst a hallucinating model can do inside tsumugi is produce a
claim that verification then reports as `unsupported` — the system working,
not failing.

**A non-local endpoint is refused unless the caller says otherwise in as many
words.** tsumugi reads a person's entire notes folder. A default that posts
that to a host on the internet because a URL was mistyped is not a default a
local-first library gets to have. `mamori` reached the same conclusion in its
ADR-0015 and this borrows the shape: the check is on the *boundary*, not on
the spelling of "localhost".

**Nothing is installed for it.** The adapter is `urllib` and `json`. tsumugi
still has zero runtime dependencies, and CI still installs it with no extras
and runs the suite.

## What it costs

**The threat model's flattest sentence is gone.** It used to say the core
opens no socket, full stop, and a reader could stop reading there. It now
says the core opens no socket and one named adapter does, only when
configured. That is still true and still checkable, but it is a sentence with
a clause in it, and clauses are where trust leaks. `docs/threat-model.md`
carries the amended claim and the test that holds it up.

**Ollama is a choice about somebody else's roadmap.** Its `/api/generate`
endpoint is the version this was written against. If it changes, the adapter
breaks and the library does not — which is the compensation for putting it
behind a port, but it is compensation, not immunity.

**`ask()` is a fifth way to do something that already had four.** Build,
protect, send, verify, record were all callable separately, and now there is
one function that does them in the right order. It exists because two of those
brackets are the ones people get wrong — protect the text and not the
package, restore before you verify — and a use case is a cheaper place to
encode an ordering than a paragraph of documentation. The cost is that the
separate functions now have a preferred combination, and a preferred
combination is a thing that can drift from the parts it composes.

**Determinism is requested, not guaranteed.** The adapter sends
`temperature: 0` and `seed: 0`. A package is reproducible by construction
(ADR-0003); an answer is reproducible only as far as the model cooperates.
The ledger records what the model was called, not what it would say again.

## What was not decided

Streaming, tool calls, chat history, and multiple providers per run. None of
them are needed to close the loop, and each is easier to add to a port with
one implementation than to remove from one with four.
