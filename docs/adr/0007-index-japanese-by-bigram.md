# 7. Index Japanese by bigram, and confirm against the text

**Status:** accepted

## Context

tsumugi needs full-text search with zero runtime dependencies. SQLite's FTS5 ships
in CPython's `sqlite3`, so the extension is available. The question was whether it
can actually find Japanese.

This was measured rather than assumed. One document, six queries, three indexing
strategies, on CPython 3.12.8 with SQLite 3.47.1. Hit counts:

| query | `unicode61` (default) | `trigram` | bigram + `unicode61` |
|---|---|---|---|
| 東京 | 0 | **0** | 1 |
| 会議 | 0 | **0** | 1 |
| 東京の会議 | 0 | 1 | 1 |
| 開発方針 | 0 | 1 | 1 |
| KISEKI | 0 | 1 | 1 |
| ローカル | 0 | 1 | 1 |
| | **0 / 6** | 4 / 6 | **6 / 6** |

**The default tokenizer indexes nothing usable.** Inspecting the index directly
through `fts5vocab` shows why:

```
['kisekiの開発方針について話し合う', 'tsumugiはローカルファースト', '東京の会議は明日です']
```

Whole sentences become single tokens. `unicode61` splits on Unicode category
changes, and kanji, kana and katakana are all "letters", so the only break is the
full stop. This is FTS5's default. `CREATE VIRTUAL TABLE d USING fts5(body)`
written without thinking produces a Japanese search that returns zero results
forever, with no error — the worst available failure mode.

`trigram` works for queries of three characters or more and **cannot, in
principle, match a two-character query**. Two-character compounds are the backbone
of written Japanese: 東京, 会議, 開発, 方針, 検索, 文書, 設計, 要約. What trigram
loses is not an exotic tail, it is the middle of the language.

## Decision

**Index by character bigram.** At index time the text is normalized (NFKC) and
emitted as overlapping two-character tokens joined by spaces — `東京の会議` becomes
`東京 京の の会 会議` — into an `unicode61` FTS5 table. The same transform is
applied to the query.

Roughly fifteen lines of Python, standard library only.

**The bigram index generates candidates. It does not decide matches.** Every
candidate is confirmed against the anchored original text before it can enter a
package.

That second half is not a patch for a weakness — it is the reason this approach
suits tsumugi specifically. A bigram index does not know where words end, so it
will return documents containing `京の` when the query was `東京`. In a search
engine that is a precision problem to be tuned away. In tsumugi, offsets and
original text are already held for evidence, so exact confirmation costs one
string comparison against text that is already loaded. **Approximate retrieval
confirmed by exact evidence is the same shape as the rest of the design.**

Script-aware tokenization: runs of Latin text are indexed as words, not as
two-character fragments. Bigramming `budget` into `bu ud dg ge et` loses precision
for nothing. This mirrors `mamori`'s script-driven language-pack selection.

## Consequences

Search works in Japanese, English and mixed text, with no dependency and no
external index.

The retrieval stage can be tuned for recall without hurting precision, because
precision is recovered by confirmation. That is the right trade for an evidence
system: a missed document is invisible, a false candidate is filtered.

The full-stack claim holds — a thousand-document corpus is ingestible, searchable
and traceable with nothing installed but Python.

## What it costs

**Index size.** An n-character document yields n−1 tokens. The FTS5 index will be
several times larger than a naive one. Acceptable for a single local file, but it
is a number, so it gets measured on a real corpus in v0.1 and recorded rather than
assumed.

**A confirmation pass on every query.** Retrieval is two stages instead of one.
The design needs the second stage regardless, so the marginal cost is small — but
it is not zero, and a candidate set tuned too wide will make it felt.

**No morphological analysis.** Bigrams do not know that 開発する and 開発 share a
stem. A proper analyzer (MeCab, Sudachi) would do better and is a dependency with
a dictionary. If the golden retrieval dataset ever shows this costing real recall,
it comes back as an optional adapter — never as a core dependency.

The measurement is recorded outside this repository, in the working notes, along
with the probe script that produced it.
