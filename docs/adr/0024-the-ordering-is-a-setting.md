# ADR-0024: The ordering is a setting, and the default is the measured one

*Accepted 2026-09-01.*

## The question

`fit_to_budget` fills a budget best-first, and *best* meant one thing:
descending score. That is an algorithm, and it was written down nowhere as a
choice — it was the shape of a `sorted()` call.

It also has a measurable weakness. **113 of 240 packages built from the
evaluation corpus contain two items sharing a twelve-character window.**
Duplicates are marked (ADR-0008) and still spend budget, because marking is a
report and ordering is a decision, and ADR-0005 does not let the report make
the decision.

## The decision

**The ordering is a parameter, and the default does not change.**

Two are provided, both deterministic, both stdlib-only, both nameable in
configuration:

| | |
|---|---|
| `score` | descending relevance, ties broken by path and offset. **The default** |
| `mmr` | Maximal Marginal Relevance (Carbonell & Goldstein, SIGIR 1998), `diversity` between relevance and novelty |

Selected the way every other setting here is selected — built-in default, then
the config file, then `TSUMUGI_ORDERING`, then `--ordering`. A name that is not
known **raises** rather than falling back to the default, because a setting
that looks applied and is not is this project's own failure class.

MMR reuses `redundancy.similarity` — character shingles and set containment,
already used for the duplicate marks. It introduces no second notion of
*alike*, so a package's marks and its ordering cannot disagree about which
passages repeat each other.

## Why the default does not change

Because the measurement does not support changing it.

| | |
|---|---|
| packages whose contents differ under `mmr` | **5 of 240** |
| packages whose order differs | 20 of 240 |
| recall, precision, trap rate | **identical** |

**MMR is not broken** — a test holds it demoting a near-duplicate below a
distinct passage, and `diversity=1.0` reduces exactly to `score`. Two other
things are true instead:

- **the budget rarely binds.** In 208 of 240 cases everything that confirms
  also fits, and an ordering cannot change what is sent when everything is
  sent;
- **bm25 has usually separated the duplicates already.** MMR earns its keep
  when a near-duplicate ranks *directly behind its twin*, and term frequency
  over documents of different lengths usually puts something else between them.

Changing the default on a 1998 paper and no local evidence would be exactly the
move this project spent a week removing from its own documents.

## What it costs

**A second algorithm to keep.** `mmr` is now public API and covered by
ADR-0023's deprecation rule, so it cannot quietly leave. It is tested, and
untested code that ships is worse than code that does not — but a second path
is a second path.

**A choice a reader now has to make**, and the honest answer for most readers
is *leave it alone*. A setting whose right value is almost always the default
is a small tax on everybody reading the configuration to help the few for whom
it is not.

**Diversity is a loss the reader cannot see.** A near-duplicate is sometimes
the second witness that makes a fact checkable, and dropping it to buy variety
removes a corroboration without saying so — `omissions` will record
*redundant_candidate*, which explains the mechanism and not the cost.

**And the measurement is on one corpus**, the same self-generated corpus that
is v1.0's open problem. `mmr` may matter a great deal on a corpus with real
duplication — export folders, mail archives, minutes that quote each other —
and this project has none of those. **The setting exists partly because the
measurement cannot generalise**, which is a better reason for a setting than
for a default.
