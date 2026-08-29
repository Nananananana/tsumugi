# 11. Record what was sent, and what was used

**Status:** accepted

## Context

The draft specification was a one-way pipe. Documents in, structured prompt out.
It had an evaluation chapter listing good metrics — relevant context rate,
redundancy ratio, evidence coverage — with no source of data to compute them from
except a hand-built golden set.

A golden set measures a ranker against someone's idea of the right answer for
twenty questions written once. It cannot answer the questions that decide whether
this tool is worth running:

- Which documents does it send constantly, that the model never uses?
- Is the budget binding, and what falls off the edge when it is?
- Did last week's ranking change help, or just move things around?
- What did a month of context actually cost?

Every one of those is answerable from data the system already handles and
currently throws away. A package knows what it included, what it omitted and under
which rule, and what each item cost. Verification knows which items were cited.
Nothing connects the two.

Meanwhile the whole justification for the project — "do not send everything" —
rests on an unmeasured belief that most of what gets sent is not needed. That
belief is probably right. It is not currently checkable, and a project whose
premise is unchecked has a soft centre.

## Decision

**Every `build_context` opens a ledger entry. Every verification closes one.**

Opened:

- `package_id`, timestamp, the query's hash
- each included item: `document_id`, offsets, score, signals, estimated cost
- each omission: `document_id`, offsets, rule, score
- the budget: unit, limit, estimate

Closed, when an answer is verified:

- which items were cited, and how often
- the claim classification counts

Two rules that are part of the decision, not implementation detail:

1. **The ledger stores identifiers, offsets, scores and counts. Never text.** Not
   the query text, not the document text, not the answer. A hash of the query is
   enough to group repeats. Making it textless means it can default to on without
   creating a second sensitive artefact.
2. **It is derived data.** Deletable at any time, at the cost of history and
   nothing else. It is never an input to a deterministic build
   ([ADR 0003](0003-a-package-is-reproducible.md)) — a ledger that fed back into
   ranking would make packages depend on their own history, and reproducibility
   would be gone.

`tsumugi ledger --since 30d` reports it.

## Consequences

The evaluation chapter gets a real dataset: the owner's own questions, over the
owner's own corpus, rather than twenty synthetic ones.

A ranking change can be evaluated on whether cited-item rate went up, which is
closer to the thing that matters than any offline proxy.

The premise becomes checkable. "Over three months, 41% of what was sent was never
cited" is a sentence no other local tool can say, and it is either a vindication
of the project or a correction to it. Both are worth having.

`omissions[]` ([ADR 0005](0005-selection-is-a-report.md)) becomes evidence rather
than a courtesy: a document repeatedly dropped by `budget_exhausted` and then
manually pasted in by the user is a ranking failure with a paper trail.

Automatic redundancy removal ([ADR 0008](0008-redundancy-is-proposed.md)) gets the
measurement that could one day justify it.

## What it costs

A write on every context build, which makes a read-shaped operation into one that
writes. It can be disabled, and disabling it costs only the loop.

**A list of what you asked and when is revealing, even with no text in it.** The
query hash groups repeats, which means frequency analysis over a person's
questions is possible for anyone holding the file. Textlessness reduces this; it
does not remove it. Named in [threat-model.md](../threat-model.md), deletable,
disableable.

The closing half only exists when the caller verifies. A caller who builds
packages and never calls `verify` gets half a ledger: costs without uses. That is
still useful and it is worth saying that it is half.

Schema growth. The ledger is a second store to migrate and keep in step, and it
buys nothing on day one — the value arrives after weeks of use. It is built early
anyway, because a ledger started in v0.4 has no history in v0.4.
