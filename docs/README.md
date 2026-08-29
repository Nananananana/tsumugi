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
| `docs/measurements.md` | What the index costs, on real corpora, with the tool that produced it |
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

**v0.1 is partly built.** `ingest`, `search`, `trace` and `doctor` work;
everything the design calls a ContextPackage does not exist yet.

[`architecture.md`](architecture.md) describes what the code does today and is
kept honest by `tests/test_architecture.py`. The larger design — packages,
budgets, selection, the ledger, the adapters — is in
[`proposals/0001-the-design.md`](proposals/0001-the-design.md) and is **not
evidence that any of it exists**.

`architecture.md` was deliberately not written until there was an architecture to
describe. An ADR before the code is legitimate, because it records a decision
that has been made. A current-state document before the code is fiction.
