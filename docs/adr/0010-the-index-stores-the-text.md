# 10. The index stores the text it anchored

**Status:** accepted

## Context

An anchor is a document id, an offset pair and a hash. Resolving one needs the
text. Two ways to have it:

**Read from disk on demand.** The index stays small and holds no content, so the
database is far less sensitive than the corpus. But an anchor is only resolvable
while the file is where it was, unchanged. Edit a paragraph and every anchor into
that document becomes unresolvable — including anchors in packages already built,
already sent, already cited. Evidence that evaporates when you edit a note is not
evidence.

**Store the text.** Anchors resolve forever. The cost is that the index becomes a
complete plaintext copy of the corpus in one file.

The second cost is not hypothetical, and it is the subject of
[threat-model.md](../threat-model.md): a folder of a thousand notes scattered
across a disk becomes one artefact that is complete, indexed, portable and
unencrypted. It removes the attacker's work.

## Decision

**The index stores the text it anchored.**

- A document's content is stored with its `content_hash` at ingest time.
- An anchor resolves against the stored text, not against the file.
- On each read, the file's current hash is compared. A mismatch marks the anchor
  **stale**: still resolvable, reported as historical, never presented as current.
- Stale anchors are surfaced by `tsumugi doctor` and become `omissions[]` entries
  with rule `stale_anchor` when a stale span is a selection candidate.
- **The index is never re-anchored silently.** Re-ingest is explicit, and it
  creates a new document version rather than mutating the old one.

Following from this, and not optional:

- The index does **not** live inside the corpus folder by default. Corpus folders
  get synced, shared and committed; a complete plaintext index sitting in one is
  a one-line accident.
- `tsumugi ingest` prints where the index is, every time.
- `tsumugi forget <document>` removes content and vacuums, and a test greps the
  database file afterwards for the fixture text.
- The threat model says all of this in plain words, in the README's line of sight.

## Consequences

Evidence survives editing, which is the property that makes citation meaningful
over a corpus a person actually works in. Notes get edited constantly; that is
what notes are.

Stale detection becomes possible and useful. "This was true in the version you
indexed in May; the file has changed since" is a genuinely helpful thing for a
knowledge tool to say, and it is only sayable because the old text is still there.

Search operates on stored text, so it works when a file is temporarily
unavailable — a network drive, an unmounted volume, a deleted file that is still
worth finding.

Reproducibility ([ADR 0003](0003-a-package-is-reproducible.md)) becomes achievable.
A `corpus_state` hash means something because the corpus state is inside the
database rather than spread across a filesystem that changes underneath.

## What it costs

**A second complete copy of your notes, in one unencrypted file.** This is the
real price and it is paid in full. The mitigations — location, printing the path,
ignore rules, `forget`, a threat model that states it plainly — reduce the chance
of an accident. None of them reduce the consequence if the file is read.

Disk usage roughly doubles, plus the bigram index
([ADR 0007](0007-index-japanese-by-bigram.md)), which is several times the text
again. For a text corpus this is small in absolute terms and it should still be
measured rather than waved at.

Document versioning is now a design problem that cannot be deferred: re-ingesting
an edited document has to keep the old content for old anchors while making the
new content current. That is more schema than a single-version store, and it is
required from v0.1 because retrofitting versioning into a store that assumed one
version per document is a migration nobody enjoys.
