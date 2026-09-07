# 0005 — What breaking it on purpose taught

*Written 2026-09-08, after a systematic mutation sweep of the assembly,
verification and retrieval path. `0004` said the next thing this project needed
was **a corpus it did not write**, because four defects came from one outside
reader in a week and the corpus had produced none in a month. That is still
true and still blocked. This is what the second-best instrument found while
waiting.*

---

## The instrument

`tools/mutate.py` edits one expression in a module — a comparison swapped for
its neighbour, a boolean operator inverted, a small integer raised by one — and
runs the tests. **A surviving mutant is a change to shipped code that no test
objects to.**

Not every survivor is a defect. Some are equivalent programs, and a few are
behaviour nobody promised. But every one is a question the suite cannot answer,
and this is the second time asking those questions has produced something
shipped: the tool exists because `_confirm` once computed anchor offsets in
folded space and applied them to the original, and the tests were green because
0 of 780 corpus documents change length under NFKC.

## What it found

Nine modules swept. Four defects, and they share a shape worth naming.

**1. An account that named a rank the candidate did not have.**
`domain/assembly.py`

A `budget_exhausted` omission says "ranked N". N was a count of what had been
considered so far, which reads the same as the rank right up until a
near-duplicate appears — those are held back to a second pass (ADR-0008 marks,
never vetoes), so from the first duplicate onward every later candidate was
reported one place better than it ranked. Measured: with a copy at rank 2, the
third-best passage came back as "ranked 2".

**2. An anchor that quoted `0` as the evidence for `0株`.**
`application/search.py`

One source character can fold to several — `ﬁ` to `fi`, `㍿` to `株式会社` — so
a match ending part-way through an expansion mapped its end to the origin of a
character it already covered, and the source span was cut short. In the extreme
the span was empty: an anchor whose text is `""`, whose `text_hash` is the hash
of nothing, and which resolves **RESOLVED**, because the empty string really is
at that offset.

Found by a property test, not by a mutant. The mutants got the offset map
right; the property got the *rule*.

**3. Every unconfirmed hit named the first heading in its file.**
`application/search.py`

The confirmed path names the section a match sits in. The unconfirmed path
named the section at offset 0. `section` is published — it is in every hit over
MCP and in every anchor a package carries — and a reader follows it to a
heading.

**4. The contract document promised a judgement the library refuses to make.**
`docs/context-package.md`

Two of the three signal names in the published example had never been emitted.
An `item_id` format that could not match a real one. And a `redundant_candidate`
reason reading "kept the earlier-dated source", which is precisely the choice
ADR-0015 says redundancy has no way to make and does not guess at.

### The shape they share

**Every one of them is invisible in the case the corpus contains.**

| defect | invisible when |
|---|---|
| the rank | no near-duplicates in the candidate list |
| the offset map | the document folds one character for one character |
| the section | the document has one section |
| the example | nobody parses the prose |

That is the same sentence four times, and it is the argument of `0004`'s item 1
restated from the inside. A corpus of one shape cannot find a defect that only
appears in another, and **the suite had been written from the corpus**.

## What the sweep produced besides defects

**A rule, where there had been the same test written five times.** Eight
per-class immutability tests were killing eight `frozen=True` mutants and
leaving the ninth class undefended. Replacing them with one invariant that
walks every dataclass in the package — two exemptions, each carrying a tested
reason — then killed four more mutants in `domain/assembly.py`, a module it was
never written for.

That is the difference worth keeping from this exercise: **a survivor is a
prompt to ask what the library is, not only to write one more assertion.**

**A hazard in the instrument.** The mutated file is `ast.unparse` output: every
comment gone, every docstring reflowed. A run that is *killed* rather than
interrupted never reaches its `finally`, and what it leaves behind still
imports and still passes the whole suite. It happened once during this work.
The tool now keeps a backup for the length of a run and refuses a stale one
loudly, because which of the two files is the real one is a question only a
person can answer.

## The roadmap this leaves

### Next — in the order I would do them

**1. A corpus this project did not write.** *(v1.0 condition 1, unchanged, and
the table above is four more arguments for it.)*

**Blocked**, and still the only blocked item. Its two unrecoverable
constraints are in `0003`: a licence recorded per document *at intake*, and the
writer of each genre recorded *at intake*. Neither can be reconstructed later.
This needs a decision about procurement before any of it can start.

**2. Finish the sweep.** *(New, and unblocked.)*

Nine modules of about twenty. The remaining ones in the order their defects
would cost most:

| module | why it is next |
|---|---|
| `parsers/markdown.py` | every offset in the system starts here |
| `domain/document.py` | `section_at` decides where a reader is sent |
| `index/tokenization.py` | decides what the index can find at all |
| `storage/ledger.py` | the record of what was sent and what was used |
| `domain/redundancy.py` | ADR-0008 and ADR-0015 live in it |
| `application/ingest.py` | what enters the corpus |

**Closes** when every module has been swept once and every survivor is either
killed or written down in the source as an equivalent program. Roughly a dozen
survivors are already documented that way; an undocumented survivor is
indistinguishable from one nobody has looked at.

**3. Property tests where the mutants ran out.** *(New.)*

Defect 2 was found by a property and missed by the mutants, which is a fact
about the two instruments rather than about that function. Mutation asks *does
any test object to this change*; a property asks *is the rule true*. The places
worth a property are the ones where a rule is stated in a docstring and checked
nowhere: the offset map has one now, and the budget invariant and the "every
candidate leaves as an item or an omission" rule already did.

**4. The paraphrase residual, through leads.** *(Carried from `0004`,
unchanged, and still likely undecidable at n=16 until item 1 lands.)*

### Answered since 0004

- **`connect` ergonomics** — done, `tsumugi.opened`.
- **Whether the FTS query cost needs work** — no. 593 ms at 10,000 documents
  against sora's one-second budget.

### Not planned, and why

Everything in `0004`'s list, unchanged, plus:

- **Reporting `_OCCURRENCE_CAP` as an omission.** A term repeated more than 32
  times is anchored on a bounded view of where it appears, and by this
  project's own rule a cap the package does not mention is indistinguishable
  from having considered everything. It is not planned because the cap bounds
  *where a term is*, not *which documents were seen* — the reader is not being
  told about a document that was skipped. Written down here so that the
  argument exists rather than the omission being an oversight.

## What v1.0 would mean

Unchanged from `0003`. Condition 2 is done; **condition 1 is the whole of what
is left**, and this proposal is four more reasons it is the right condition —
each one a defect that a differently-shaped corpus would have found first.
