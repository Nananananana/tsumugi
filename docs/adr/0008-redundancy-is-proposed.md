# 8. Redundancy is proposed, never removed

**Status:** accepted

## Context

A personal corpus is full of near-duplicates. The same decision written in a note,
restated in a summary, quoted in a later document, and pasted into a message. Sent
whole to a model, it wastes budget and makes one idea look like four independent
sources — which is worse than the waste, because repetition reads as corroboration.

The draft specification proposed "Structural Max Purge" for this, while noting it
should be validated before adoption. That instinct was right and the name was not.
A component called *purge* will eventually purge.

The problem is asymmetric. Removing a genuinely redundant passage saves a few
hundred tokens. Removing a passage that looked redundant and was not — the version
with the exception, the later note that reversed the decision, the one that
contains the number — silently removes the answer. The user cannot see what
happened, and the model cannot know it is missing.

Deduplication also has a cousin failure that is subtle: near-duplicates often
differ in exactly the part that matters. Two paragraphs 94% identical are 94%
identical because the interesting 6% is a correction.

## Decision

**tsumugi marks redundancy. It does not act on it.**

- Near-duplicate detection is deterministic and structural: normalized-shingle
  overlap, with a stated threshold. No model.
- A detected near-duplicate becomes an entry in `omissions[]` with rule
  `redundant_candidate` and a reason naming the item it overlaps and by how much.
- The item is still **considered for inclusion**. If the budget allows both, both
  are sent. Redundancy lowers priority; it does not veto.
- Which of a duplicate pair is preferred follows a stated, deterministic rule
  (earliest-dated source, then lowest `document_id`) so the choice is
  reproducible ([ADR 0003](0003-a-package-is-reproducible.md)) and explainable.

`tsumugi context --why` shows the redundancy findings so the operator can judge
them.

This is the same posture as `mamori`'s ADR-0027 — say why, and say why not — and
the same posture `kiseki` takes on corrections: judgement happens at reading time
and the stored data is never quietly altered.

## Consequences

The compression that redundancy detection was for still happens, because a
lower-priority near-duplicate loses to a fresh document when the budget binds.
What does not happen is a duplicate being dropped while budget remains.

Redundancy findings are visible, which means they can be evaluated. After enough
real use, `omissions[]` and the ledger together will show whether marked
duplicates were ever cited — and that is the measurement that would justify
automatic removal.

The threshold can be tuned aggressively without risk, since a wrong mark costs
priority rather than existence.

## What it costs

Less compression than an aggressive deduplicator, and the difference is real on
corpora full of copies. Someone comparing token counts against a tool that
silently deduplicates will see tsumugi look worse on that number.

That comparison is accepted. A tool that scores better on compression by
occasionally deleting the answer is not better, and the only way to know it is
happening is to have measured it — which is what the marking phase is for.

Automatic removal is not ruled out forever. It is placed behind evidence: the
ledger has to show that marked duplicates go uncited, over a real corpus, before a
superseding ADR can turn it on.
