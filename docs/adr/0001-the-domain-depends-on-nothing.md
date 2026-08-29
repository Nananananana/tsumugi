# 1. The domain layer imports only the standard library

**Status:** accepted

Adopted from the sibling `mamori` project's ADR-0001, and `kiseki`'s equivalent
contract. The reasoning below is why it applies here too, not only there.

## Context

The part of tsumugi that matters is small: resolve an anchor, decide what is
selected, record what was not, fit a budget, check whether a quotation is really
where a model said it was. Everything else — parsers, SQLite, an index, an
optional model, adapters to two sibling projects — is machinery around that.

If the core imports a parser library, testing it needs that parser. If it imports
a tokenizer, testing a budget needs a model's vocabulary file. Both make the tests
slow enough that people stop running them, and an invariant nobody re-checks is an
invariant that decays.

There is a second reason particular to this project. tsumugi reads a person's
entire notes folder. Every runtime dependency is code with unsupervised read
access to that folder, arriving through a supply chain nobody in this project
controls. Zero is not minimalism for its own sake; it is the smallest attack
surface available.

## Decision

`src/tsumugi/domain/` imports nothing outside the Python standard library, and
nothing from `kiseki` or `mamori`.

Dependencies point inwards: `interfaces → application → domain`, and
`infrastructure → ports`. The domain knows about neither.

Everything that decides what a package contains, what it admits to leaving out,
and whether a citation resolves lives in `domain/`.

The package as a whole declares zero runtime dependencies. Development and
evaluation tools — pytest, hypothesis, ruff, mypy, import-linter, a real
tokenizer for measuring the cost estimator — are not runtime dependencies and are
declared separately.

## Consequences

The whole core is testable with no model, no network, no database and no
fixtures. That is the reason the suite gets run.

Swapping a parser, an index or a ranker cannot change a guarantee, because the
guarantee is not in the swappable part.

`pip install tsumugi` installs one thing. For a tool that indexes a private
corpus, that is a feature a user can verify in one command.

## What it costs

No pydantic in the domain, so validation is hand-written in `__post_init__`, and
the JSON Schema for the ContextPackage has to be kept in step with the
dataclasses by hand. A conformance test that validates real packages against the
published schema is the mitigation, and it is not free either.

No tokenizer, so the budget is an estimate ([ADR 0006](0006-the-budget-is-an-estimate.md)).

No parsing library, so Markdown structure is read by hand-written code that will
be less complete than a real parser for a long time. The mitigation is the same
one that makes the whole design work: offsets and hashes are computed over the
raw bytes, so a parser that misreads structure produces worse *sections*, never a
wrong *anchor*.
