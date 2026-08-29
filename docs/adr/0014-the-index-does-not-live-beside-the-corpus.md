# 14. The index does not live beside the corpus

**Status:** accepted

## Context

The index has to go somewhere, and the three plausible answers all have a real
argument.

**Beside the corpus** (`./​.tsumugi/index.db`) is the most discoverable. The
index belongs to that folder, it moves when the folder moves, and deleting the
folder deletes it. Several tools do this and it reads as tidy.

It is also the one that leaks. A corpus folder is a *notes* folder, and notes
folders are the most-synced directories on a personal machine: Dropbox, iCloud,
OneDrive, Syncthing, a git repository, a backup that goes somewhere else. The
index is a complete plaintext copy of everything in that folder in a single
portable file ([ADR 0010](0010-the-index-stores-the-text.md)). Putting it inside
the thing most likely to be synced or committed makes an accident a matter of
time, and the accident is *the whole corpus, in one file, somewhere else*.

`.gitignore` mitigates the git case and nothing else, and only if the user
writes it.

**A platform data directory** (`%LOCALAPPDATA%`, `$XDG_DATA_HOME`,
`~/Library/Application Support`) is the conventional answer and it is genuinely
correct for most software. It is also three different paths, two of which most
people cannot recite, and one of which is hidden by default in the file manager.

Convention is worth a lot. It is worth less than the owner of a file knowing
where that file is, when the file is a complete copy of their private notes.

## Decision

`~/.tsumugi/index.db`. One rule, one path, on every platform.

- **Never inside the corpus.** Not as a default, and there is no flag that makes
  it a default. A user may point `--index` anywhere, including into the corpus,
  and that is their decision made explicitly.
- **`TSUMUGI_INDEX` and `--index` override it**, in that order of precedence.
- **Every command that touches the index prints where it is**, on the first
  line. Not verbose output; the first line.
- `.gitignore` ships with `.tsumugi/`, `*.tsumugi.db` and `index.db` in it, for
  the case where someone points it somewhere anyway.

The path is not a platform convention, and that is the trade being made
deliberately rather than by omission.

## Consequences

The most likely accident — a complete plaintext index of a person's notes
syncing to a cloud drive because it sat in the notes folder — needs a
deliberate act rather than a default.

One sentence documents the location on every platform, and a user can find,
back up, inspect or delete the file without looking anything up. `tsumugi
doctor` prints the path and the size, so "how much of my life is in there" has
an answer.

Printing the path on every run makes the file's existence unavoidable
knowledge. The threat model can then talk about a file the reader knows they
have.

## What it costs

**It is not the platform convention**, and a reviewer who expects
`$XDG_DATA_HOME` will file an issue. The answer is this ADR, and it is a real
cost paid in explanation rather than in code.

**A dotfile in `$HOME` is mild clutter**, and there is a reasonable position
that home directories should stop accumulating them.

**One index per user, not per corpus.** Indexing two unrelated folders puts them
in one file, which is convenient for search across both and wrong for anyone who
wanted them separate — a work corpus and a personal one, for instance. `--index`
is the answer today and it has to be passed every time. If separate corpora turn
out to be the common case rather than the exception, this decision gets
superseded by one about named corpora; that is a bigger change than a path, and
it is not being guessed at now.

**Deleting the corpus does not delete the index.** The text outlives the folder,
which is exactly what [ADR 0010](0010-the-index-stores-the-text.md) intends and
is also a way to be surprised. `tsumugi doctor` naming the path is the
mitigation; a `forget` command that takes a corpus rather than a document is
still owed.
