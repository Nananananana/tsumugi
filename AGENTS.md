# AGENTS.md

Context for AI assistants (and future humans) working on tsumugi. Read this whole
file before proposing or writing any change.

This file is current state and current rules. It is not a history: why a thing is
the way it is lives in `docs/adr/`, and what might happen next lives in
`docs/proposals/`. `docs/README.md` explains that separation and why it matters.
**A statement here that disagrees with the code is a defect.**

## What tsumugi is

A local-first Python library that turns a folder of documents into context a
language model can use, keeping the evidence attached. Zero runtime dependencies.
No network anywhere but `infrastructure/adapters/`, and only in a file named in `NETWORKED_ADAPTERS`.

The constitution, to be enforced by construction rather than by promise:

- **Evidence first.** Every piece of context names the document, the offset and
  the hash it came from. Text that cannot is not context, it is a guess.
- **The deterministic core decides; a model may only propose.** Selection,
  anchoring, budgeting and verification contain no model. Same inputs, same
  package, byte for byte (ADR-0003).
- **A verified citation is not a true claim.** It means the string is where the
  model said it was. Any wording that blurs this is a defect, not a style choice
  (ADR-0004).
- **Say what you did not do.** `omissions[]` is required, and every cap that
  bounds coverage appears in it. A silent truncation reads as completeness
  (ADR-0005).
- **Nothing is removed on a guess.** Redundancy is marked, never purged (ADR-0008).
- **The source is the truth.** Indexes, scores, summaries and the ledger are
  derived: rebuildable, deletable, never confused with the document.
- **Fail closed.** An unresolvable citation is `unsupported`. An unrecognised
  contract version is refused. A protected package with no restorer refuses to
  verify rather than reporting nonsense (ADR-0009).
- **The index is as sensitive as the corpus.** It stores the text it anchored
  (ADR-0010). Everything in `docs/threat-model.md` follows from that.
- **Measure against labelled evidence, never against an ideal output.** There is
  no single correct structured prompt; a metric that scores distance to one
  measures conformity (ADR-0013).

## Architecture map

Current state is `docs/architecture.md`. What is planned and unbuilt is
`docs/proposals/0001-the-design.md` — most of it, still.

```text
interfaces ──> application ──> domain
                    │              ▲
                    │              │
                    └──> ports <───┴── infrastructure
```

| Layer | May import |
|---|---|
| `domain/` | **stdlib only** — and never `kiseki` or `mamori` |
| `errors.py` | nothing |
| `ports/` | `domain`, `errors` |
| `application/` | `domain`, `ports`, `errors` |
| `infrastructure/` | `domain`, `ports`, `errors` |
| `config.py` | everything above |
| `interfaces/` | everything above |

This table is executable: `tests/test_architecture.py` parses every module and
asserts it, and `import-linter` asserts the direction across five contracts. A
diagram that stops matching the code turns the build red rather than quietly
becoming fiction.

Search is two stages and the split is the design: the bigram index
over-generates, and confirmation against the anchored text is what turns a
candidate into a result (ADR-0007). Anything that makes the index *smarter* is
allowed; anything that skips the confirmation is not.

## Conventions

Taken from `kiseki` and `mamori`, which paid for them.

- **Everything in the repository is English.** Conversation language may differ;
  committed text may not.
- TDD. One issue, one PR, squash merge, close the issue after.
- **All tests must pass before any commit.** One failure means stop and
  investigate, not proceed.
- **An adapter may name a sibling; it may not import one at module level.**
  `infrastructure/adapters/` is the only place allowed to know `mamori` or
  `kiseki` exist, and the CLI imports those adapters unconditionally -- so a
  module-level `import mamori`, which is what tidying up looks like, makes the
  whole CLI require it. Both halves are checked: `test_architecture.py` proves
  the import graph, and `test_leakage.py` proves the property it stands for by
  importing everything with the siblings blocked. The first fails on a machine
  that *has* the sibling, which is every developer's.
- **`pytest.importorskip` is for genuinely optional dependencies, and nothing
  else.** `mamori` is optional and lives in its own `siblings` extra, so
  skipping is right. `jsonschema` is in `[dev]` beside `pytest`, so a file that
  is running already has it — skipping there guarded a state that cannot happen
  and hid one that can: the extra losing it, and the contract suite vanishing
  green. The question is whether the suite is entitled to assume the
  dependency.
- Test file names are unique across the repository — tests are not a package and
  duplicate basenames break collection.
- Any test that invokes the CLI isolates itself: chdir to `tmp_path` and strip
  `TSUMUGI_*`. A CLI test that writes into a developer's real index is a bug
  waiting in every future test file.
- Checks before every green commit: `uv run pytest -q`, `uv run mypy src`,
  `uv run lint-imports`, `uv run ruff check --fix .`, `uv run ruff format .`,
  `uv run pre-commit run --all-files`. If pre-commit rewrites anything,
  `git add` and run it again — a commit whose hooks failed did not happen.
- Windows: set `PYTHONUTF8=1`. This project handles Japanese text in every test.
- Read-only dumps for an assistant go **outside** the working tree.

## Rules particular to this project

- **Never write an architecture document for code that does not exist.** ADRs
  before code are legitimate; a current-state document before code is fiction.
- **A number in a document is measured or it is not written.** If a claim needs a
  measurement, run it, record the script and the environment, and cite it.
  ADR-0007 is the pattern: a table of hit counts, a probe script, a named SQLite
  version.
- **State the residual.** Every measurement ships with what it does *not* say:
  the estimator names its tokenizer, the index measurement names its corpus as
  source code rather than notes, the trap rate names the three cases it misses
  and why.
- **Anything that changes selection is gated on `tsumugi eval`.** The corpus
  found a four-commit-old defect on its first run that unit tests, the CLI
  output and well-formed packages all missed. Run it before and after.
- **Floors, not targets.** The eval gate is deliberately looser than the current
  scores. A gate set at today's number makes every honest experiment a build
  failure, and tuning to reach a threshold is what mamori's ADR-0023 records.
- **Every discarding path carries its reason to the end.** A filter returns a
  shorter list *and* an account. This is invasive to retrofit, so it is done from
  the first filter.
- **Ordering discipline.** No unordered iteration reaching an output, no partial
  sort keys, no wall-clock in a ranking signal. A build run twice must be
  byte-identical, and a property test asserts it.
- The `mamori` and `kiseki` integration tests are worth their setup cost: the
  interesting failures are at the seams, and the seams only exist when something
  real is on both sides. ADR-0009 exists because of one.

## Current state

- Version `0.1.0.dev0`. Nothing released, the public API is not stable.
- **License: Apache-2.0. Python: 3.12+. Runtime dependencies: 0**, checked in CI
  by installing without extras and asserting nothing came along.
- Everything CI gates, in one command: `python tools/gates.py`. It runs `ruff`,
  `mypy --strict`, five `import-linter` contracts, the suite, the fixture
  oracle and the evaluation floors, and judges each by exit code.
- **Built:** `ingest` (with `--rebuild`), `search`, `context`, `verify`, `ask`,
  `trace`, `forget`, `ledger`, `mcp`, `eval`, `demo`, `doctor`. Domain (span,
  hash, document, anchor, normalization, budget, omission, selection, package,
  assembly, matching, claim, redundancy, usage), ports (parser, tokenizer,
  store, index, cost, redactor, ledger, freshness, llm), SQLite store with
  append-only versions, FTS5 + bigram index, four parsers with a registry, the
  filesystem walk, three cost models.
- **`ask` is the only command with an outbound path**, and `eval --model` is
  the only other place that constructs a provider. Both are named in an
  allow-list a test asserts. The provider is asked for text and never for a
  decision: it does not rank, does not choose what is sent, and does not
  resolve a citation (ADR-0016).
- **Not built:** a prompt template *language*. Two instruction sets ship
  (ADR-0017) and `render()` emits the whole prompt; a third shape has to argue
  for itself.
- Both sibling adapters exist. ADR-0009 is tested against the real `mamori`
  (`tests/test_adapter_mamori.py`, skipped when it is absent). The `kiseki`
  adapter **imports nothing** — the export is a published JSON contract, so
  reading it is reading JSON, and a test asserts no import creeps in.
- **A producer declares its own layer**, in document metadata. `build_context`
  reads it, so no layer above has to know which producers exist, and an
  unknown layer stops the build rather than laundering the passage into a
  fact. The plan is `docs/proposals/0002-what-building-it-taught.md`, which
  revises 0001's roadmap from what building it cost.
- **Redundancy is marked, never removed** (ADR-0008), and it does not decide
  which duplicate is right (ADR-0015). `omission correctness` went 0% -> 90%
  when it was built -- that number asked for the feature, and building it also
  showed the corpus's expectation had been wrong.
- Schema 2: the index records the corpus root each document came from, so
  staleness is checked without a flag. A check the caller has to remember to
  turn on is a check that is off.
- Contract: ContextPackage `1-draft`. `tsumugi context --json` emits it,
  `src/tsumugi/schemas/context-package-1.json` publishes it, and
  `tests/test_contract_conformance.py` checks six rules against it plus three
  that assert the schema and the enums have not drifted apart. **Frozen at `1`.** A field may be added;
  none will be removed or change meaning. Frozen once the MCP server had built a
  package in one process and verified it in another through JSON -- a second
  consumer, which is the evidence a freeze wants -- rather than when the
  calendar said v0.2. Readers still accept the `-draft` string; nothing writes
  it.
- Evaluation: `docs/evaluation-corpus.md`. **240 cases, all seven trap kinds,
  30 genres across ja/en/zh/ko, eight document shapes**, labelled evidence, a
  150-case CI tier and a held-out split. Genres live in `tools/genres.json`;
  `tools/generate_cases.py` composes cases from them — deterministic, no model
  and no seed, so the same command produces the same fixtures.
  `tools/draft_genres.py` drafts new genres with a local model **for a person
  to review**, and nothing it writes lands unread. Tripling the corpus took the
  trap rate from 6% to 26% with no code change (ADR-0019): a corpus written by
  the same hand as the ranker measures that hand. **CI calls no model.** `eval --model
  NAME` is the opt-in half that does, reporting grounded / on target / trapped
  / abstention, and it is never a floor: those numbers are a property of this
  code *and* whichever model was pulled.
- Working notes, review history and experiments are kept **outside this
  repository** and are not published.
- Settled: no model generates the evaluation corpus. `tools/generate_cases.py`
  composes it from templates, deterministically, and the fixtures are
  committed. What is still thin is genre count and variety, not the mechanism.
- Measured, in `docs/measurements.md`: the index runs **2.6x the corpus** on 666
  real documents, and terms per character tracks the CJK share
  (`CJK + 0.15 x non-CJK`). ADR-0007's flagged cost is real and affordable.
- **Incremental ingestion is not close.** Re-ingest over an unchanged corpus is
  5.5x cheaper than a cold build, so the ten-second trigger arrives near 12,000
  documents rather than 2,200. Watch the re-ingest number, not the cold one.
- **Close what you open.** The CLI closes its connections in a `finally`. A
  process that exits closes its files for you, which is why the leak went
  unnoticed -- but a held read lock stops the next command's
  `wal_checkpoint` from truncating, so `forget` vacuumed and left the text in
  the write-ahead log.
