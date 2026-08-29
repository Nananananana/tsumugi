# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v0.1 in progress

Nothing is released. The version is `0.1.0.dev0` and the public API is not
stable.

### Added — the evidence core

- **Domain**: `Span` (raises past the end rather than clamping), `ContentHash`
  (carries its algorithm), `Document`/`Section`/`Block` with identity split
  between path and revision, `Anchor` and three-way resolution, and
  offset-preserving NFKC normalization.
- **Anchors resolve, go stale, or fail.** Staleness is a distinct outcome:
  evidence taken before an edit was true when it was taken, and is reported as
  historical rather than as wrong or silently re-anchored.
- **Storage**: SQLite, schema 1, explicit migrations. Versions are append-only,
  so an anchor into an old revision keeps resolving. `forget` vacuums, and a
  test greps the database file afterwards to prove it.
- **Search in Japanese.** Script-aware character-bigram tokenization into FTS5.
  SQLite's default tokenizer scores 0/6 on a Japanese probe and `trigram` 4/6,
  failing on exactly the two-character compounds the language is built from;
  bigrams score 6/6. Measured first, in ADR-0007.
- **Search is two stages**: the index over-generates on purpose and every
  candidate is confirmed against the anchored text. A candidate that could not
  be confirmed is reported as such rather than dropped.
- **Parsers** for Markdown, plain text, source code and JSON, behind a registry
  a new format joins with one call. Every parser reports spans over the original
  string and never rewrites it — asserted by a property test over generated
  documents.
- **Ingestion reports everything**: added, revised, unchanged, skipped and
  failed. Files that look like credential stores are refused and named whether
  or not they were asked about.
- **CLI**: `ingest`, `search`, `trace`, `doctor`. Every command that touches the
  index prints where the index is.
- **`doctor`** separates what is measured, what is true by construction, and
  what is the operator's responsibility — and names the test behind each
  by-construction claim.

### Added — the gates, from the first commit

- `tests/test_architecture.py`: the layer table as an executable assertion, plus
  the stdlib-only rule for the domain, the no-network rule for the core, and the
  rule that only adapters may know about a sibling project.
- Five `import-linter` contracts, `mypy --strict`, `ruff`.
- CI asserts the runtime dependency count is zero by installing without extras
  into a clean environment.
- CI checks the runner's SQLite has FTS5 before running anything, so a build
  without it fails with one sentence rather than forty confusing errors.
- 305 tests, 93% coverage, no network and no model anywhere in the suite.

### Added — the documents

- The design, [docs/proposals/0001-the-design.md](docs/proposals/0001-the-design.md),
  and fourteen decisions in [docs/adr/](docs/adr/README.md).
- The ContextPackage contract, `tsumugi.context-package/1-draft`, in
  [docs/context-package.md](docs/context-package.md). Freezes at v0.2. Not
  implemented.
- [docs/threat-model.md](docs/threat-model.md). The index is a complete
  plaintext copy of whatever corpus it was built from, and that is the first
  thing it says.
- [docs/evaluation-corpus.md](docs/evaluation-corpus.md): the corpus is
  labelled, the ideal output is not. Not implemented.
- [docs/architecture.md](docs/architecture.md), written only once there was an
  architecture to describe.

### Decided

- Apache-2.0, for the explicit patent grant.
- Python 3.12+.
- The index lives at `~/.tsumugi/index.db` and never inside the corpus
  (ADR-0014).

### Not built yet

ContextPackage, budgets, selection, omission reporting, the ledger, claim
verification, the MCP server, and the kiseki and mamori adapters. A `forget`
command, and `--rebuild` for a tokenizer change.
