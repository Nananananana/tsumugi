# tsumugi (紡ぎ)

**Send a model the parts of your notes that bear on the question — with a record
of where each part came from, and an account of what was left out.**

Local-first. No network in the core. **Zero runtime dependencies.**

---

> ### Status: v0.2 in progress
>
> Reading a corpus, searching it, building a **ContextPackage** under a budget,
> checking a model's citations against it, recording what was sent and what was
> used, serving all of it to an agent over MCP, and scoring the selection
> against a labelled corpus all work. Redundancy marking, prompt templates and
> both sibling adapters do not exist yet.
>
> The ContextPackage contract is **frozen at version 1**.
>
> - **What the code does today** — [docs/architecture.md](docs/architecture.md)
> - **What it is meant to become** — [docs/proposals/0001-the-design.md](docs/proposals/0001-the-design.md)
> - **The decisions** — [docs/adr/](docs/adr/README.md)
> - **The threat model** — [docs/threat-model.md](docs/threat-model.md)
>
> [docs/README.md](docs/README.md) explains which documents describe what exists
> and which describe what is planned, and why the two are never mixed.

---

## The problem

You have a folder of notes. When a language model could answer something from it,
you have three bad options: send everything and pay for it while the answer gets
diluted; paste three files by hand and bias every answer towards what you
remembered; or hand the folder to a hosted service and lose both the privacy and
the ability to check the result.

## What works now

```bash
pip install -e .

tsumugi ingest ~/notes
tsumugi search  "東京"
tsumugi context "テントの重量は?" --budget tokens:4000 --why
tsumugi verify  answer.json --package package.json
tsumugi trace   "テントは 2.4kg"
tsumugi ledger  --since 2026-08-01
tsumugi mcp     # speak MCP on stdio, so an agent can use the corpus
tsumugi eval    # score the selection against a labelled corpus
tsumugi doctor
```

```console
$ tsumugi ingest ~/notes
index:  /home/you/.tsumugi/index.db
corpus: /home/you/notes

412 new, 0 revised, 0 unchanged, 7 skipped, 0 failed
  refused  .config/.env  (looks like a credential store (.env))
  6 more skipped; --show-skipped to list them
```

```console
$ tsumugi context "テントの重量は?" --budget tokens:400
# SYSTEM
Answer the question using only the context provided below.
- Quote the exact text you rely on. Do not report character offsets.
...
# NOT INCLUDED
19 relevant-looking passages were considered and left out of this context.
Do not assume what you have been given is complete.

--- 1a78a34e8a99 ---
20 items, 398/400 tokens via heuristic/cjk-aware@1
estimated, not counted: p50 5.0% p95 18.3% against cl100k_base
```

Three things in that output are the whole design. The model is told the
selection **has edges**, because it cannot see them otherwise. The token count
says it is an **estimate and how wrong it is** — a number with no stated error
misleads a caller exactly once, expensively. And `--why` names every dropped
candidate and the rule that dropped it, so a silent truncation is not possible.

Running it twice produces the same `package_id`: same corpus, same query, same
settings, byte-identical package. That buys caching, diffing and regression
tests at once.

When the answer comes back, its citations get checked against the text that was
actually sent:

```console
$ tsumugi verify answer.json --package package.json
supported     The trust boundary is about a side of a line, not a machine.
              -> src/mamori/domain/trust.py[531:591]
unsupported   A quotation the model invented.
              x  この文はどこにも存在しない
                 not found in the text that was sent
uncited       A claim with no citation at all.

2 supported, 1 unsupported, 1 uncited

A supported claim means the quoted text is where the model said it was.
It does not mean the claim is true.
```

Four outcomes, kept apart on purpose. `uncited` is not `unsupported` — a model
that cites nothing has failed differently from one that cites something that
does not exist. **The model quotes; tsumugi resolves the offsets.** Models
cannot count characters, so asking them for positions produces coordinates that
are plausible, self-consistent, and wrong by enough to point at a different
sentence.

Every `context` opens a ledger entry and every `verify` closes it, so after a
few weeks:

```console
$ tsumugi ledger --since 2026-08-01
41 packages, 23 verified, 612 candidates left out (588 of them for budget)
Of the context that was sent and checked, 71% was never cited (196 of 274 items).
```

The ledger holds identifiers, offsets and counts — never the question, the
document or the answer. A test greps the database file to prove it.

```console
$ tsumugi trace "テントは 2.4kg"
resolved  notes/mountain.md:7 (装備)

$ tsumugi trace "テントは 3.9kg"
unsupported: that text does not appear in this corpus.
A quotation either resolves or it does not. There is no fuzzy match here.
```

Search works in Japanese, which is less obvious than it sounds. SQLite's default
full-text tokenizer indexes an entire Japanese sentence as a single token, so a
search returns nothing forever and raises nothing; `trigram` cannot match a
two-character query at all, and two-character compounds — 東京, 会議, 開発, 方針 —
are the backbone of the written language. Both were measured before anything was
built, and the numbers are in
[ADR 0007](docs/adr/0007-index-japanese-by-bigram.md).

## For an agent

`tsumugi mcp` is a **read-only** MCP server on JSON-RPC over stdio — four tools,
no dependency, and nothing that can write to your corpus or your index is
reachable from it.

```json
{"mcpServers": {"tsumugi": {"command": "tsumugi", "args": ["mcp"]}}}
```

| Tool | |
|---|---|
| `search` | ranked passages with anchors |
| `context` | a full ContextPackage, **including what was left out** |
| `trace` | from a quotation back to document, section and line |
| `verify` | claim classifications for an answer |

The agent is the reason the contract is a document rather than a Python class:
`context` builds a package in one process, the agent answers, and `verify`
resolves the citations in another — through JSON, with no shared objects. That
round trip is what the contract was frozen on.

## The one thing to be clear about

**A resolved quotation means the text exists where it was said to. It does not
mean the claim built around it is true.**

tsumugi does not eliminate hallucination and will never claim to. It makes the
relationship between a generated sentence and the evidence behind it checkable,
which is a smaller promise and a keepable one.

## What it does not do

It is **not a redactor** — it has no idea whether a document holds a secret, and
it will put one in front of a model if the secret is relevant. It does **not
encrypt its index**; that is your operating system's job. And it builds a single
file that is as sensitive as your whole notes folder.
[The threat model](docs/threat-model.md) says all of this in detail, and
`tsumugi doctor` says it again against your own machine.

## Zero dependencies, checked

`pip install tsumugi` installs one thing. That is asserted on every push: CI
installs the package without extras into a clean environment and fails if
anything came with it.

The domain layer imports nothing outside the standard library, and the whole
core opens no socket. Both are `import-linter` contracts and an executable table
in `tests/test_architecture.py` — not README claims.

## Measured, not asserted

Every number this project states is measured, ships with the script that
produced it, and says what it does **not** say. They are in
[docs/measurements.md](docs/measurements.md).

`tsumugi eval` scores selection against thirty labelled cases — a small corpus
per case with a planted answer and planted adversaries. On its first run it
found a defect four commits old that unit tests, plausible CLI output and
well-formed packages had all missed: **unconfirmed candidates were entering
packages**, dragging whole unrelated documents in. The near-miss trap rate went
96.7% → 10.0% across two fixes, and train and held-out agree, so the number is
not fitted to the cases it came from.

One metric reads **0%**, and that is the most useful number here. *Omission
correctness* asks whether the reason given for dropping a candidate was right;
every case expects a superseded document under `redundant_candidate`, and
nothing reports that because redundancy marking is not built. It is the first
number in this project that asks for a feature rather than permitting one — and
that is what decides what gets built next
([docs/proposals/0002](docs/proposals/0002-what-building-it-taught.md)).

## Three projects

tsumugi is the middle of three local-first libraries that share a constitution
and share no code.

| | | Answers |
|---|---|---|
| [kiseki](https://github.com/Nananananana/kiseki) | **Remember** | What happened, and what it suggests you care about |
| **tsumugi** | **Connect** | What is worth sending, and where it came from |
| [mamori](https://github.com/Nananananana/mamori) | **Protect** | Whether it is safe to send |

**tsumugi's core does not import kiseki or mamori, and works with neither
installed.** They will be optional adapters. That is a hard constraint checked by
the build, not an aspiration — see [docs/concept.md](docs/concept.md).

## Contributing

`AGENTS.md` holds the rules and the current state. Before any commit:

```bash
uv run pytest -q && uv run mypy && uv run lint-imports && uv run ruff check --fix . && uv run ruff format .
```

## License

Apache-2.0. See [LICENSE](LICENSE).
