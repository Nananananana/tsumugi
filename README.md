# tsumugi (紡ぎ)

**Send a model the parts of your notes that bear on the question — with a record
of where each part came from, and an account of what was left out.**

[![CI](https://github.com/Nananananana/tsumugi/actions/workflows/ci.yml/badge.svg)](https://github.com/Nananananana/tsumugi/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Runtime dependencies: 0](https://img.shields.io/badge/runtime%20dependencies-0-brightgreen)](#zero-dependencies-checked)

Retrieval gives you passages. **tsumugi gives you passages you can check** — an
anchor into the file each one came from, and the list of what it decided not to
send and why. Local-first, offline, and deterministic: the same question over
the same corpus builds the same package, byte for byte.

```bash
pip install "tsumugi @ git+https://github.com/Nananananana/tsumugi"
tsumugi demo          # a whole walk-through in a throwaway directory
```

```python
import tsumugi

connection = tsumugi.connect("index.db")
store, index = tsumugi.SqliteDocumentStore(connection), tsumugi.FtsIndex(connection)
tsumugi.ingest_paths(paths, root=root, store=store, index=index, parser_for=tsumugi.parser_for)

package = tsumugi.build_context(
    "how heavy is the tent",
    store=store,
    index=index,
    budget=tsumugi.Budget.characters(4000),
    cost_model=tsumugi.CharacterCost(),
)

for item in package.items:
    print(item.anchor.source_path, item.text)
for left_out in package.omissions:
    print(left_out.rule, left_out.reason)  # what it did not send, and why
```

## Why this and not a retriever

|  |  |
|---|---|
| **Every passage is anchored** | document id, offsets and two hashes. `verify` resolves a model's citations back to the file on disk and says *supported*, *unsupported*, *uncited* or *unverifiable* |
| **It says what it left out** | a budget that binds, a candidate that could not be confirmed, a near-duplicate — each becomes an `omission` with a rule and a reason ([ADR-0005](docs/adr/0005-selection-is-a-report.md)) |
| **The contract is frozen** | `tsumugi.context-package/1` is a JSON document with a published schema, not a Python class. Another program in another language can read it |
| **It measures itself** | 240 labelled cases, and every number in [docs/measurements.md](docs/measurements.md) is reproducible with one command — including the ones that say something did not work |
| **Nothing is installed** | zero runtime dependencies, asserted in CI by installing into a clean environment |

Search works in Japanese, Chinese, Korean and English. It **does not** decide
whether a claim is true — see [what it does not do](#what-it-does-not-do).

## Where to start

| | |
|---|---|
| Try it | `tsumugi demo` |
| Use it from Python | [From Python](#from-python), or [`examples/library.py`](examples/library.py) |
| Plug it into an existing pipeline | [Already using LangChain or LlamaIndex](#already-using-langchain-or-llamaindex) |
| Give it to an agent | [For an agent](#for-an-agent) — a read-only MCP server |
| Read the numbers | [docs/measurements.md](docs/measurements.md) |
| Read the decisions | [docs/adr/](docs/adr/README.md) — every one carries what it cost |

---

> ### Status: v0.2 in progress
>
> The ContextPackage contract is **frozen at version 1**. Everything described
> above works today; both sibling adapters (`mamori`, `kiseki`) exist and are
> optional. The public surface is pinned by a test and changes on notice
> ([ADR-0023](docs/adr/0023-the-public-surface-changes-on-notice.md)).
>
> - **What the code does today** — [docs/architecture.md](docs/architecture.md)
> - **What it is meant to become** — [docs/proposals/0003-what-running-it-taught.md](docs/proposals/0003-what-running-it-taught.md)
> - **The threat model** — [docs/threat-model.md](docs/threat-model.md)
>
> [docs/README.md](docs/README.md) explains which documents describe what exists
> and which describe what is planned, and why the two are never mixed.

---

## See it work

```bash
tsumugi demo
```

The whole pipeline in a throwaway directory: a small rigged corpus, a question,
what was left out and why, an answer whose citations get checked, and a
quotation traced back to a line in a file. No model, no network, and it does not
touch whatever index you already have.

Then [`examples/library.py`](examples/library.py) is the same shape as ordinary
Python, commented for *why* rather than *what*.

## The problem

You have a folder of notes. When a language model could answer something from it,
you have three bad options: send everything and pay for it while the answer gets
diluted; paste three files by hand and bias every answer towards what you
remembered; or hand the folder to a hosted service and lose both the privacy and
the ability to check the result.

## What works now

```bash
pip install -e .

tsumugi ingest ~/notes
tsumugi search  "東京"
tsumugi context "テントの重量は?" --budget tokens:4000 --why
tsumugi verify  answer.json --package package.json
tsumugi ask     "テントの重量は?"   # the loop closed: build, ask a local model, check
tsumugi trace   "テントは 2.4kg"
tsumugi ledger  --since 2026-08-01
tsumugi mcp     # speak MCP on stdio, so an agent can use the corpus
tsumugi eval    # score the selection against a labelled corpus
tsumugi doctor
```

```console
$ tsumugi ingest ~/notes
index:  /home/you/.tsumugi/index.db
corpus: /home/you/notes

412 new, 0 revised, 0 unchanged, 7 skipped, 0 failed
  refused  .config/.env  (looks like a credential store (.env))
  6 more skipped; --show-skipped to list them
```

```console
$ tsumugi context "テントの重量は?" --budget tokens:400
# SYSTEM
Answer the question using only the context provided below.
- Quote the exact text you rely on. Do not report character offsets.
...
# NOT INCLUDED
19 relevant-looking passages were considered and left out of this context.
Do not assume what you have been given is complete.

--- 1a78a34e8a99 ---
20 items, 398/400 tokens via heuristic/cjk-aware@1
estimated, not counted: p50 5.0% p95 18.3% against cl100k_base
```

Three things in that output are the whole design. The model is told the
selection **has edges**, because it cannot see them otherwise. The token count
says it is an **estimate and how wrong it is** — a number with no stated error
misleads a caller exactly once, expensively. And `--why` names every dropped
candidate and the rule that dropped it, so a silent truncation is not possible.

Running it twice produces the same `package_id`: same corpus, same query, same
settings, byte-identical package. That buys caching, diffing and regression
tests at once.

When the answer comes back, its citations get checked against the text that was
actually sent:

```console
$ tsumugi verify answer.json --package package.json
supported     The trust boundary is about a side of a line, not a machine.
              -> src/mamori/domain/trust.py[531:591]
unsupported   A quotation the model invented.
              x  この文はどこにも存在しない
                 not found in the text that was sent
uncited       A claim with no citation at all.

2 supported, 1 unsupported, 1 uncited

A supported claim means the quoted text is where the model said it was.
It does not mean the claim is true.
```

Four outcomes, kept apart on purpose. `uncited` is not `unsupported` — a model
that cites nothing has failed differently from one that cites something that
does not exist. **The model quotes; tsumugi resolves the offsets.** Models
cannot count characters, so asking them for positions produces coordinates that
are plausible, self-consistent, and wrong by enough to point at a different
sentence.

Every `context` opens a ledger entry and every `verify` closes it, so after a
few weeks:

```console
$ tsumugi ledger --since 2026-08-01
41 packages, 23 verified, 612 candidates left out (588 of them for budget)
Of the context that was sent and checked, 71% was never cited (196 of 274 items).
```

The ledger holds identifiers, offsets and counts — never the question, the
document or the answer. A test greps the database file to prove it. It holds a
*hash* of the question, which groups repeats and, because questions are short
ordinary strings, lets anyone holding the file confirm a guessed one. `tsumugi
context --no-ledger` declines to write it at all.

```console
$ tsumugi trace "テントは 2.4kg"
resolved  notes/mountain.md:7 (装備)

$ tsumugi trace "テントは 3.9kg"
unsupported: that text does not appear in this corpus.
A quotation either resolves or it does not. There is no fuzzy match here.
```

Search works in Japanese, which is less obvious than it sounds. SQLite's default
full-text tokenizer indexes an entire Japanese sentence as a single token, so a
search returns nothing forever and raises nothing; `trigram` cannot match a
two-character query at all, and two-character compounds — 東京, 会議, 開発, 方針 —
are the backbone of the written language. Both were measured before anything was
built, and the numbers are in
[ADR 0007](docs/adr/0007-index-japanese-by-bigram.md).

**Chinese and Korean work less well, and here is how much less.** Evidence
recall over the whole evaluation corpus, by the language of the question:

| | recall | cases |
|---|---|---|
| English | 90.7% | 54 |
| Japanese | 88.3% | 60 |
| Korean | 83.3% | 30 |
| Chinese | 83.3% | 36 |

Every miss is the same shape: a question that shares no contiguous phrase with
the document. Japanese has kana between its content words and Korean has
spaces, so something in the question can be dropped and the rest still
confirmed. **Chinese has neither**, so a whole question is one content term and
nothing can be dropped — and the fix that would help is a dictionary, which
[ADR 0007](docs/adr/0007-index-japanese-by-bigram.md), ADR-0018 and ADR-0019
each refused for the same reason: a word list is a thing that has to be
maintained, per language, forever.

**Three honest limits on those numbers**, and the third one matters most.

They come from a corpus this project generated, and the Chinese and Korean
genres in it were **all drafted by a model whose name was not recorded** — so
they measure vocabulary that is not mine, drafted by something I cannot name.

Retrieval is lexical throughout, so a question sharing no word with its document
is a miss by construction, not a bug.

And **every question in the corpus was written by someone reading the document
it answers**, so the table above says how well near-verbatim questions are
served and nothing about how people ask. Where that shows is Chinese: asked for
`菜园` — the document's own word for the subject — a package comes back with one
item that does not contain the answer. Not empty: *wrong*. Evidence is located
where a question's terms crowd together, and a language whose questions are
always one term has nothing to crowd, so the item lands on the heading. Run
`python tools/measure_paraphrase.py` to see the boundary in three languages;
the diagnosis is in [measurements](docs/measurements.md) and it is the open half
of roadmap item 4.

## For an agent

`tsumugi mcp` is a **read-only** MCP server on JSON-RPC over stdio — four tools,
no dependency, and nothing that can write to your corpus or your index is
reachable from it.

```json
{"mcpServers": {"tsumugi": {"command": "tsumugi", "args": ["mcp"]}}}
```

| Tool | |
|---|---|
| `search` | ranked passages with anchors |
| `context` | a full ContextPackage, **including what was left out** |
| `trace` | from a quotation back to document, section and line |
| `verify` | claim classifications for an answer |

The agent is the reason the contract is a document rather than a Python class:
`context` builds a package in one process, the agent answers, and `verify`
resolves the citations in another — through JSON, with no shared objects. That
round trip is what the contract was frozen on.

## Already using LangChain or LlamaIndex

The barrier to trying a retrieval library is the plumbing, so this speaks both
shapes and **imports neither**:

```python
from langchain_core.documents import Document

pages = tsumugi.as_documents(package)  # dicts both constructors accept
documents = [Document(**page) for page in pages]

tsumugi.texts_from(their_documents)  # and the other direction
```

`TextNode(**page)` works the same way — each page carries `page_content` *and*
`text`, so a consumer does not have to know which library it was converted for.
Both are exercised in CI against the **real** classes, because `Document` and
`TextNode` belong to other people and other people change shapes.

**What survives the trip is the point.** Each page's metadata carries the whole
anchor — document id, offsets, both hashes — so a chain can hand an answer back
to `verify` and get citations checked, having only ever handled `Document`
objects. A retriever gives a pipeline text and a source; this gives it evidence.

And the omissions travel, as pages with `kind: "omission"` and **empty text**,
so concatenating `page_content` cannot send a reason to a model as though it
were evidence. A pipeline that drops them has thrown away the half of the
package that makes it a package.

## From Python

The whole job from one import. `examples/library.py` is the same path with the
reasons written out, and a test runs it — it opened with **fourteen** deep
imports until this was written, which is how the library was usable and how
nobody should have had to use it.

```python
import tsumugi

connection = tsumugi.connect("index.db")
store, index = tsumugi.SqliteDocumentStore(connection), tsumugi.FtsIndex(connection)

tsumugi.ingest_paths(paths, root=root, store=store, index=index, parser_for=tsumugi.parser_for)

package = tsumugi.build_context(
    "テントは",
    store=store,
    index=index,
    budget=tsumugi.Budget.characters(4000),
    cost_model=tsumugi.CharacterCost(),
)

for item in package.items:
    print(item.anchor.source_path, item.text)
for left_out in package.omissions:  # ADR-0005: an empty package still explains itself
    print(left_out.rule, left_out.reason)

connection.close()  # the caller owns it; the CLI has its own registry
```

Algorithms that are choices are settings, not edits. The ordering candidates
are offered to the budget in is `score` (descending relevance) or `mmr`
(Carbonell & Goldstein 1998, relevance traded against novelty), selected the
way everything else here is — built-in default, config file, `TSUMUGI_ORDERING`,
then `--ordering`, with an unknown name **refused rather than ignored**:

```bash
tsumugi context "the warranty coverage period" --ordering mmr --diversity 0.5
```

A third, `rerank`, asks a cross-encoder — what most retrieval libraries reach
for. It needs `pip install "tsumugi[research]"`, downloads a model, and is the
only thing here that is neither offline nor deterministic. **It reorders
candidates confirmation has already accepted and cannot add one**, because
measured as a *gate* it ranks a forbidden document first in 8.3% of trap cases
against this library's 4.2% ([ADR-0025](docs/adr/0025-outside-the-domain-a-library-may-help.md)).

The default is `score` because that is what the numbers were measured on, and
because MMR changes the contents of 5 packages in 240 here
([ADR-0024](docs/adr/0024-the-ordering-is-a-setting.md)). It is a setting
because that measurement is on one corpus, and this project's own corpus is the
thing it least trusts.

**Which numbers are safe to leave alone** is measured rather than asserted.
`python tools/measure_sensitivity.py` moves every constant that decides
something across its plausible range and re-scores the whole corpus with the
evaluator's own scoring, so a value fitted to this data shows up as a swing:

| | range tried | what moves |
|---|---|---|
| `INFLECTION_TAIL` = 2 | 0 – 3 | recall, by **16.7 points** — and all of it Chinese |
| `RELATIVE_MATCH_FLOOR` = 0.8 | 0.5 – 0.95 | trap rate, 5.0% → 21.7% going down |
| `COVERAGE_THRESHOLD` = 1.0 | 0.6 – 1.0 | trap rate, 5.0% → 18.3% going down |
| `redundancy_threshold` = 0.75 | 0.5 – 0.9 | nothing measured |

The two precision values sit where their curve has already flattened — 0.8 and
0.95 give the same trap rate — so they are conservative rather than balanced on
an edge. `redundancy_threshold` moves no number on this corpus, which is exactly
why it is now a setting (`TSUMUGI_REDUNDANCY_THRESHOLD`) instead of a constant:
a number that this corpus cannot justify is a number somebody else's corpus
should be allowed to change. It is not inert — it decides whether a package says
`redundant_with:` and whether a crowded-out passage is explained as a duplicate
or as a budget overflow.

`tsumugi.__all__` and the CLI verbs are pinned by a test
([ADR-0023](docs/adr/0023-the-public-surface-changes-on-notice.md)), so a
rename arrives as an edit to a list of public names rather than as a surprise
in your build.

## The one thing to be clear about

**A resolved quotation means the text exists where it was said to. It does not
mean the claim built around it is true.**

tsumugi does not eliminate hallucination and will never claim to. It makes the
relationship between a generated sentence and the evidence behind it checkable,
which is a smaller promise and a keepable one.

## What it does not do

It is **not a redactor** — it has no idea whether a document holds a secret, and
it will put one in front of a model if the secret is relevant. It does **not
encrypt its index**; that is your operating system's job. And it builds a single
file that is as sensitive as your whole notes folder.
[The threat model](docs/threat-model.md) says all of this in detail, and
`tsumugi doctor` says it again against your own machine.

## A local model, if you want one

Everything above runs with no model and no network. But the loop does not close
without one — `verify` needs an answer, and an answer needs something that
answers — so there is one adapter, for [ollama](https://ollama.com):

```bash
ollama pull qwen2.5:7b-instruct
tsumugi ask  "テントの重量は?"
tsumugi demo --model qwen2.5:7b-instruct   # the same walk-through, with a real one
```

```console
$ tsumugi ask "テントの重量は?" --model qwen2.5:14b-instruct
sending to ollama/qwen2.5:14b-instruct at http://127.0.0.1:11434 (local)
テントの重量は2.4kgです。

--- fd045294137b ---
  supported    テントの重量は2.4kgです。
  1 supported
```

That corpus also held an older note saying 3.1kg. The model quoted the current
one; had it quoted the old one, the citation would still have resolved —
**being fooled by a superseded passage and inventing one are different
failures, and only the second is something verification can catch.**

The model is asked for text and never for a decision. It does not rank, does
not choose what is sent, and does not resolve a citation — so the worst a
hallucinating model can do is write a claim that verification then reports as
`unsupported`, which is the system working rather than failing.

`--protect` runs the prompt through [mamori](https://github.com/Nananananana/mamori)
on the way out and restores before verifying — in that order, which is the
whole of [ADR 0009](docs/adr/0009-restore-before-you-verify.md). Get it wrong
and every honest citation reports as unsupported. Against real mamori and a
real model, a claim citing `<PERSON_001>との打ち合わせは金曜。` resolves back to
the name in the file and reports **supported**.

It **refuses a host that is not this machine** unless `--allow-remote` says so
in as many words. Your index holds a copy of your corpus; a mistyped URL should
not be enough to post it somewhere. And it installs nothing: the adapter is
`urllib` and `json`, like everything else here.

[ADR-0016](docs/adr/0016-the-network-lives-in-one-place.md) records where the
boundary is and what drawing it there cost.

## Zero dependencies, checked

Installing this package without extras installs **one thing**. That is asserted
on every push: CI installs it into a clean environment and fails if anything
came with it.

There is one extra, `tsumugi[research]`, and nothing needs it. The rule
([ADR-0025](docs/adr/0025-outside-the-domain-a-library-may-help.md)) is that
the domain still imports only the standard library — an architecture test
enforces that — while `infrastructure/` and `tools/` may reach for a library
**once it has been measured on this corpus, including when the answer is no.**
Two of the three candidates measured on the day that rule changed were refused:
a Japanese and a Chinese segmenter recovered 0 and 2 of 23 missed cases, because
the residual is paraphrase rather than word boundaries.

*(The sentence used to say `pip install tsumugi`, which is wrong in a way worth
naming: `tsumugi` on PyPI is somebody else's project — a gene-network tool from
the University of Tsukuba — and nothing here is published yet. The check CI
runs is real; the name it was written around is not ours. The distribution name
is undecided.)*

The domain layer imports nothing outside the standard library, and the network
lives in one named file that nothing else may import. Both are `import-linter`
contracts and an executable table in `tests/test_architecture.py` — not README
claims.

## Measured, not asserted

Every number this project states is measured, ships with the script that
produced it, and says what it does **not** say. They are in
[docs/measurements.md](docs/measurements.md).

`tsumugi eval` scores selection against thirty labelled cases — a small corpus
per case with a planted answer and planted adversaries. On its first run it
found a defect four commits old that unit tests, plausible CLI output and
well-formed packages had all missed: **unconfirmed candidates were entering
packages**, dragging whole unrelated documents in. The near-miss trap rate went
96.7% → 10.0% across two fixes, and train and held-out agree, so the number is
not fitted to the cases it came from.

One metric reads **0%**, and that is the most useful number here. *Omission
correctness* asks whether the reason given for dropping a candidate was right;
every case expects a superseded document under `redundant_candidate`, and
nothing reports that because redundancy marking is not built. It is the first
number in this project that asks for a feature rather than permitting one — and
that is what decides what gets built next
([docs/proposals/0002](docs/proposals/0002-what-building-it-taught.md)).

## Six projects

tsumugi is one of six local-first libraries that share a constitution and share
no code. The question each one answers is the whole of the difference between
them.

| | | Answers |
|---|---|---|
| [kiseki](https://github.com/Nananananana/kiseki) | **Remember** | What happened, and what it suggests you care about |
| [musubi](https://github.com/Nananananana/musubi) | **Convert** | What a PDF or a page says — and where in the original each piece was |
| **tsumugi** | **Connect** | What is worth sending, and where it came from |
| [mamori](https://github.com/Nananananana/mamori) | **Protect** | Whether it is safe to send |
| [akashi](https://github.com/Nananananana/akashi) | **Check** | Whether the particulars — numbers, dates, units — hold up |
| [iriguchi](https://github.com/Nananananana/iriguchi) | **Route** | Whether a prompt goes to a local model, out through mamori, or nowhere |

They compose in one direction and nothing enforces that they must: musubi
converts, tsumugi selects, akashi checks the particulars, mamori decides what
may leave, iriguchi decides whether to ask at all. `akashi` verifies
*particulars* where tsumugi's `verify` resolves *citations*, which is what
keeps the two from being the same tool.

**tsumugi's core imports none of them and works with none installed.** `kiseki`
and `mamori` have optional adapters under `infrastructure/adapters/`; that they
are the only place allowed to name a sibling is a hard constraint checked by the
build, not an aspiration — see [docs/concept.md](docs/concept.md).

## Contributing

`AGENTS.md` holds the rules and the current state. Before any commit:

```bash
uv run python tools/gates.py
```

That runs exactly what CI gates — ruff, `mypy --strict`, five `import-linter`
contracts, the suite, the fixture oracle and the evaluation floors — and judges
each by its exit code. One spelling, because a command list maintained by hand
beside a config drifts: `AGENTS.md` said `uv run mypy src` while the config
checked `src` *and* `tools`, and the narrower one printed `Success`.

## License

Apache-2.0. See [LICENSE](LICENSE).
