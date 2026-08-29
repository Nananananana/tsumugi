# 5. Selection is a report, not a promise

**Status:** accepted

The tsumugi form of `mamori`'s ADR-0019, "Privacy is a report, not a promise",
which came in turn from `kiseki`'s ADR-0046.

## Context

Every retrieval system silently discards more than it returns. Ask for the top
eight of a hundred and ninety-two candidates, and the interesting question is
almost never about the eight.

The failure this causes is specific. A user asks a question, gets a confident
answer built from three documents, and never learns that a fourth document — the
one that contradicted the other three — ranked ninth and did not fit the budget.
The answer is not wrong because of a bug. It is wrong because the tool decided
something on the user's behalf and did not mention it.

This is worse in tsumugi than in a search engine, because a search engine's user
can see the result list is truncated. A ContextPackage goes to a model, and the
model has no idea anything is missing. It will answer with the confidence of
complete information over a selection it cannot see the edges of.

## Decision

**`omissions[]` is a required field, and every candidate that was considered and
not included appears in it, with the rule that dropped it and a reason in
prose.**

The defined rules are `budget_exhausted`, `below_threshold`,
`redundant_candidate`, `stale_anchor`, `excluded_by_filter` and
`truncated_by_cap`.

`truncated_by_cap` is the important one. **Any implementation limit that bounds
coverage must appear here.** If the ranker only scored the top two hundred
candidates from the index, the package says so. A cap that does not appear in the
output is indistinguishable, to the reader, from having considered everything.

An omission carries an anchor, a score and a reason. It does **not** carry the
omitted text: copying what was deliberately not sent into the thing being sent
would defeat the purpose.

`tsumugi context --why` prints the omissions in full. The most useful line it can
print is "three documents scored above 0.6 and did not fit your budget".

## Consequences

The user can see the edge of the selection, which is the only way to know whether
the budget is the right one.

A ranking bug becomes visible rather than merely disappointing: a relevant
document dropped under `below_threshold` with a score of 0.12 is a specific,
reportable failure.

`omissions[]` gives the ledger ([ADR 0011](0011-record-what-was-sent-and-what-was-used.md))
something to learn from. Recording only what was sent measures half of the
decision.

It creates a maintenance obligation. A future feature that discards candidates has
to add a rule here or the report quietly becomes a lie. That obligation is the
point: it is cheaper to remember when the requirement is a required schema field
than when it is a paragraph in a README nobody re-reads.

## What it costs

Packages are larger, sometimes much larger. A corpus where two hundred candidates
were considered and eight kept produces a package that is mostly omissions. The
mitigation is that omissions are metadata, not text, and that a package is not
what gets sent — `render()` emits the items.

Every discarding path must carry its reason to the end. That is a real constraint
on the internal design: a filter cannot simply return a shorter list, it has to
return a shorter list and an account. Written after the fact this is a painful
refactor, which is why it is decided now.
