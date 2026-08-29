# Threat model

*This is a current-state document. Until there is code, it describes what the
design commits to. Every claim here becomes a test or stops being written.*

The short version: **tsumugi builds a single file that is as sensitive as your
entire notes folder, and it is not encrypted.** Everything below follows from
that sentence.

---

## What tsumugi is, from an attacker's point of view

You point tsumugi at a folder. It reads every document, keeps the text, records
offsets, and writes all of it into one SQLite file. A folder of a thousand notes
scattered across a disk becomes one artefact that is:

- **complete** — nothing was left behind
- **indexed** — searchable in milliseconds
- **portable** — a single file, easy to copy, easy to sync by accident
- **plaintext** — SQLite is not encrypted

The folder was already readable by anyone with the disk. The index is worse than
the folder in exactly one way, and it is the way that matters: **it removes the
work.** An attacker who had to grep a filesystem now runs one query.

This is the honest framing, and it is not an argument against building it. It is
an argument for saying it out loud in the README and for the defaults below.

---

## What tsumugi protects

**Your text does not leave the machine on its own.** The core has no network code
and no runtime dependencies, so there is no supply chain that could add one
quietly. Ingest, index, search, select and verify all run locally.

**Nothing is sent without a caller sending it.** tsumugi builds a package. It does
not have an outbound path. If text reaches an external service, a caller put it
there with an LLM adapter it chose and configured.

**A package says what it contains.** Before anything is sent, `items[]` and
`omissions[]` are inspectable. This is the only privacy control tsumugi provides
directly, and it is a real one: you can look at exactly what is about to go.

**Derived data is separable.** The ledger, the index and any cache are derived and
can be deleted without losing the corpus. Deleting them costs history and nothing
else.

---

## What tsumugi does not protect

Stated as flatly as possible, because a threat model that reads as reassurance is
a marketing document.

**The index is not encrypted.** Disk encryption is your operating system's job.
tsumugi does not implement its own, and a library that rolled its own crypto to
look thorough would be less safe, not more.

**tsumugi is not a redactor.** It has no idea whether a document holds a password
or a medical record. That is `mamori`'s question, and `mamori` is optional and not
enabled by default. **A default tsumugi setup will happily place a secret into a
ContextPackage if the secret is relevant to the query.**

**Relevance ranking can surface what you forgot you wrote.** This is not a bug —
it is the feature — but it means the index will find the sensitive note about a
colleague that you had, in practice, forgotten. The set of things tsumugi can put
in front of a model is the set of things you ever wrote down.

**The index records where your corpus lives.** Since schema 2 it stores the
absolute path each document was read from, so that staleness can be checked
without the caller supplying the folder again — a check nobody remembers to
turn on is a check that is off. The path usually contains a username and a
directory layout. It is a small addition to a file that already holds the
documents themselves, and it is stated here rather than discovered.

**A ledger is a record of your questions.** It holds no document text, but a list
of what you asked and when is itself revealing. It can be disabled and it can be
deleted.

**Deleting a source file does not delete the evidence.** The index keeps the text
it anchored ([ADR 0010](adr/0010-the-index-stores-the-text.md)). `tsumugi forget`
must exist for this reason and must actually vacuum, not just unlink rows.

**Nothing here covers where you send the package.** Whether a service retains
what you send it is not knowable from this machine, and a claim otherwise would be
worse than silence.

---

## Defaults, and why each one

| Default | Reason |
|---|---|
| The index lives in a stated path, printed on every `ingest` | A file you do not know about is a file you cannot protect |
| The index is **not** written inside the corpus folder | Corpus folders get synced, shared and committed. Cloud-syncing a complete plaintext index of your notes is a one-line mistake, and putting the file there invites it |
| `.tsumugiignore`, plus `.gitignore` semantics, honoured by default | The patterns people already wrote to keep secrets out of git are the patterns they meant |
| Files matching credential patterns (`.env`, `*.pem`, `id_*`, `*.key`) are skipped, and the skip is **reported** | Silent skipping and silent inclusion are both wrong. Say what was not read |
| The ledger is on, and holds no text | The feedback loop is the point of the design; the text is not needed for it |
| `mamori` is off unless configured | An optional dependency that turns itself on is not optional. But see the README wording below |
| No telemetry, ever | Not a setting |

The `mamori` default is uncomfortable and is chosen deliberately. Enabling a
redaction pass that the user did not ask for, using detectors they have not
measured, would give false confidence — the exact failure `mamori` names in its
own ADR-0019. Instead, the README and `tsumugi doctor` state plainly that no
redaction is running and how to turn it on.

---

## Where the security decisions live

All of them in `domain/`, none in a swappable component. This is `mamori`'s
structure and the reason is the same: a component that can be replaced must not be
able to change a guarantee.

| Decision | Module |
|---|---|
| Whether an anchor resolves | `domain/anchor.py` |
| Whether a citation is supported | `domain/claim.py` |
| What is included, and what is recorded as omitted | `domain/selection.py` |
| Whether a package is well-formed before it can be rendered | `domain/package.py` |

A parser, an index or a ranker is a *proposer*. Swapping any of them — or adding a
model — cannot change any of the above. That separation is why a hallucinating
model cannot turn into a false "verified": the worst it can do is propose a
candidate or withhold one.

---

## The claims, and the tests that hold them

Each of these becomes a named test. A claim here without a test behind it is a
defect in this document.

| Claim | Held by |
|---|---|
| The core opens no socket | An import-graph test forbidding `socket`, `http`, `urllib` in core |
| The domain imports only the standard library | `tests/test_architecture.py` |
| No document text is written to a log or a traceback | A test that greps logs and reprs for fixture values |
| A package's omissions account for every considered candidate | A property test over generated corpora |
| `forget` leaves no recoverable text | A test that vacuums and then greps the database file |
| A protected package cannot be verified without restoration | A test asserting the loud failure |

`tsumugi doctor` prints these against the loaded configuration, separating what is
**measured** from what is true **by construction** from what is **your
responsibility** — mamori's ADR-0019, adopted whole. Each by-construction claim
prints the name of the test that fails if it stops being true.
