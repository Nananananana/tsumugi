# The seam fixture

One corpus, one question, and one real `ContextPackage` — for consumers of the
[ContextPackage contract](../../schemas/context-package-1.json) that want to
test against tsumugi's output **without importing tsumugi**.

```
corpus/               four small Markdown documents, invented
question.txt          the question, one line
context-package.json  what `tsumugi context --json` emits for them
```

Vendor `context-package.json` and `schemas/context-package-1.json` together,
with a note saying where they came from and at what version. They are a pair:
the schema says what the shape is, and the fixture is one instance of it that a
producer really produced.

## It is byte-identical across runs

`package_id` is a hash of everything that determined the package
([ADR 0003](../../docs/adr/0003-a-package-is-reproducible.md)), so it is
deterministic already. The one field that is not is `created_at` — and it is
the one field deliberately **excluded from `package_id`**, which is exactly
what makes it safe to pin.

So it is pinned, to `2026-08-30T00:00:00+00:00`, and the fixture carries the
`package_id` these inputs really produce. Compare the whole document; nothing
needs to be skipped.

The two alternatives were considered and rejected:

- **Stripping `created_at`** would publish a document tsumugi does not emit,
  and the point of a seam fixture is that it is real output.
- **Excluding it from comparison** pushes the same decision onto every
  consumer, and one of them will forget.

`tsumugi context --json --at <ISO8601>` pins it from the command line, so a
consumer can regenerate this without reading the tool.

## What it exercises

Deliberately more than the happy path:

- **two items and one omission.** The budget is 40 characters, which binds.
  `omissions[]` is the half of this contract a consumer is most likely to get
  wrong, and a fixture that never shows one never tests it.
- **a superseded passage carried, not dropped** — `gear-older.md` says 3.1kg
  where `gear.md` says 2.4kg, and both are sent
  ([ADR 0008](../../docs/adr/0008-redundancy-is-proposed.md) marks, it does not
  remove). A consumer that assumes a package holds one answer per question is
  wrong, and this is where it finds out.
- **an adjacent subject left out under a named rule**, with its reason in
  prose.

## Regenerating

```bash
python tools/make_seam_fixture.py
```

It builds twice and refuses to write if the two differ. Re-run after any change
to selection, rendering or the contract, and **commit the diff**: a fixture
that has drifted from its producer is worse than no fixture, because it looks
like agreement.

`tests/test_contract_conformance.py` validates this file against the published
schema on every run, so a drift that breaks the contract fails the build here
before it reaches anyone downstream.
