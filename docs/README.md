# The documents, and what each one is for

tsumugi's documentation is written so that three different things never get
mistaken for one another:

- **what is true now** — the current architecture and rules;
- **why it became true** — the decisions, as they were made;
- **what might become true** — proposed and planned work.

A reader who cannot tell these apart will implement a proposal as though it
shipped, or "fix" an ADR to match today's code and erase the reasoning that
produced it. Both have a cost that grows with the project, so the separation is
structural: each document says at the top which of the three it is.

This convention is taken from the sibling `kiseki` project, which learned it the
expensive way.

## Responsibilities

| Document | Responsibility |
|---|---|
| `README.md` | For anyone outside: what tsumugi is, what it solves, what it can do |
| `AGENTS.md` | For contributors and AI agents: the current rules, constraints and state |
| `docs/concept.md` | The conceptual model, and the whole picture across three projects |
| `docs/architecture.md` | The current architecture, its dependencies and its principles |
| `docs/context-package.md` | The ContextPackage contract, for producers and consumers |
| `docs/threat-model.md` | What tsumugi protects, what it does not, and what it becomes |
| `docs/evaluation-corpus.md` | The labelled dataset: its shape, its traps, and what it cannot tell you |
| `docs/measurements.md` | What the index costs and what the estimator is wrong by, on real corpora, with the tools that produced them |
| `docs/adr/` | Decisions as they were made, with their reasons — history |
| `docs/proposals/` | Proposed or planned work — not necessarily implemented |
| `CHANGELOG.md` | The released history, briefly |

## The rules that keep them apart

- An ADR is not edited to match the present. A decision that no longer holds is
  superseded by a later ADR that says so; the original stays as it was written,
  because the reasoning is the point.
- A proposal is never cited as evidence that something exists. When a proposal
  lands, the current-state documents change and the proposal stays where it is,
  describing what was proposed.
- The current-state documents describe what the code does today. If one of them
  disagrees with the code, one of the two is wrong and the disagreement is a
  defect — not a difference of opinion.
- An architecture document says why, not only what. A rule without its reason is
  a rule the next reader will break for good reasons of their own.

## Where the project is right now

**v0.1 is done and v0.2 is in progress.** Everything the v0.1 note used to
list as missing now exists: verification, the ledger, the MCP server, both
sibling adapters, the forgetting path, the evaluation corpus and its runner,
and — most recently — an optional `LLMProvider` with one ollama adapter, which
closes the loop from a folder of notes to a checked answer.

Ten commands: `ingest`, `search`, `context`, `verify`, `ask`, `trace`,
`forget`, `ledger`, `mcp`, `eval`, plus `demo` and `doctor`. Nine of them run
with no model and no network; `ask` is the tenth and says where it is sending
before it sends.

`proposals/0002` deferred prompt templates until a second use needed a second
shape. `ask` was that use, so `build_context` now takes an instruction set and
two ship ([ADR 0017](adr/0017-the-instruction-set-is-a-parameter.md)). Two named
dictionaries, not a template language — the deferral rule still holds for the
third shape.

[`architecture.md`](architecture.md) describes what the code does today and is
kept honest by `tests/test_architecture.py`.

Three proposals, and the order matters:
[`0001-the-design.md`](proposals/0001-the-design.md) is the design as it was
written before any code existed, and it stays that way.
[`0002-what-building-it-taught.md`](proposals/0002-what-building-it-taught.md)
revises its roadmap from what building actually cost and what the evaluation
corpus found.
[`0003-what-running-it-taught.md`](proposals/0003-what-running-it-taught.md)
revises it again from what *running* it cost — against real models, against a
corpus not written to suit it, and against what other people have published
since. None of the three is evidence that anything exists.

`architecture.md` was deliberately not written until there was an architecture to
describe. An ADR before the code is legitimate, because it records a decision
that has been made. A current-state document before the code is fiction.
