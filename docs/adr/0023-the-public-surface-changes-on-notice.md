# ADR-0023: The public surface changes on notice, and the notice is a diff

*Accepted 2026-09-01. The second half of what v1.0 means
([proposal 0003](../proposals/0003-what-running-it-taught.md)); the first half
is about measurement and waits on a corpus this project did not write.*

## The question

`tsumugi.context-package/1` is frozen and closed ([ADR-0022](0022-an-unconfirmed-candidate-is-an-omission-not-an-item.md)),
so a consumer reading packages is safe. A consumer *calling the library* had no
such promise: any name could be renamed in any commit, and nothing would notice
except their build.

## What is public

Three surfaces, and they are public because somebody outside can depend on
them, not because they are in a `docs/` page:

1. **`tsumugi.__all__`** — every name `import tsumugi` publishes.
2. **The CLI verbs** — `ingest`, `search`, `context`, `verify`, `ask`, `trace`,
   `forget`, `ledger`, `mcp`, `eval`, `demo`, `doctor`. A script broken by a
   rename is broken exactly as surely as an import is, and the CLI is the
   surface most people meet.
3. **The contract**, which is already frozen and has its own ADR and its own
   byte-level pin.

Everything under `tsumugi.application`, `tsumugi.infrastructure`,
`tsumugi.domain` and `tsumugi.ports` reached by its full path is **not**
public. Importing `tsumugi.application.build_context` still works and always
will, in the sense that nothing will go out of its way to break it; it is
simply not covered by this.

## The decision

**A name leaves the public surface only after a release in which it still
works and says it is going.** Concretely, removing or renaming anything in the
three lists above takes three things in the same commit:

- the old name keeps working, delegating to the new one;
- calling it emits a `DeprecationWarning` naming the replacement;
- `CHANGELOG.md` records it under the version that introduced the warning.

It may then be deleted in the next minor version, and not before.

**Adding is free.** A new export is one line in the pin and breaks nobody.

**Before 1.0 the removal rule is advisory and the mechanism is not.** This is
`0.1.0.dev0`; the point of writing it now is that the machinery exists and is
exercised before there is anyone to hurt.

## The mechanism, which is the part that matters

`tests/test_public_surface.py` pins both lists. A rename fails it, so the
change arrives as an edit to a list of public names — in a diff, in a review —
rather than as a surprise in somebody's build.

This is the same shape as the schema's `FROZEN_SCHEMA_SHA256`: **it cannot stop
a change and does not try to. It makes the change deliberate.** A pin that
someone updates on purpose has done its whole job.

The CLI verbs are read off the parser rather than from a list in a document,
because a hand-maintained list beside a generated one is two lists, and this
repository deleted three stale test counts — 798, 741 and 305, none of them
right — on exactly that argument.

## What writing it down found first

The promise in `proposals/0003` named `build_context`, `search`, `verify` and
`ask`. **None of the four were exported.** `import tsumugi` gave you the
contract accessors, some domain types, and no way to build a package.

That was found by running it, not by reading it, and it came out one layer at a
time — each missing export only visible after the previous was fixed:

| | |
|---|---|
| `ingest_paths()` | missing keyword-only argument `parser_for` |
| `build_context()` | missing keyword-only argument `cost_model` |
| `cost_model_for()` | returns a **name**, not a model |

The last one is the interesting failure. `cost_model_for` is public-looking and
returns the string `"characters"`, because its own docstring says *"wiring
lives in the interfaces layer"* — a correct architectural decision that had the
side effect that **the library's central function required an object the
library's public surface could not construct**. Six lines in
`interfaces/cli/main.py` were the only thing that knew how.

So the surface being pinned is one that grew by nine names first, and the
walk-through in `test_public_surface.py` is written the way a reader would
write it — one `import tsumugi`, nothing deeper — because that is the only
version of it that can fail.

## What it costs

**Renaming gets slower, on purpose.** A better name for something public now
costs a deprecation cycle. That is the entire content of the promise, and the
cost lands on the maintainer rather than the consumer, which is the right way
round.

**A larger surface is a larger commitment.** Nine names were added to make the
library usable from the top level, and each is now something to keep. Exporting
`SqliteDocumentStore` and `FtsIndex` in particular commits to *concrete
infrastructure classes* by name — the ports exist so that the core does not
depend on them, and the public surface now does. The alternative was a factory
this project would have had to invent under time pressure, and a name invented
to avoid a commitment is a worse commitment.

**`connect` hands back a raw `sqlite3.Connection` and the caller must close
it.** The CLI has a registry that closes everything at exit; a library caller
has `try/finally`. The first draft of the walk-through leaked it and Windows
refused to delete the directory — recorded in a test, because it is a rough
edge that is now promised rather than fixed, and a promise is harder to change
than a rough edge.
