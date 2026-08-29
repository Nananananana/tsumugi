# The concept

*This is a current-state document: it describes the idea as it stands today.
See [docs/README.md](README.md) for what that means.*

## The problem

You have a folder of notes. Some of it is years old. When you ask a language
model something that folder could answer, you have three bad options.

**Send everything.** It does not fit, and where it fits it costs. Worse, the
answer gets diluted: a model given sixty documents attends to the wrong ones,
and you cannot tell which ones it used.

**Send nothing and paste by hand.** This works and it is what most people
actually do. It does not scale past about three documents, and it silently
biases every answer towards whatever you happened to remember.

**Send it through a hosted RAG service.** Now your notes are on someone else's
disk, the retrieval is a black box, and when the model states something that is
not in your notes you have no way to tell.

## What tsumugi does

tsumugi sits between the folder and the model. It reads your local documents,
keeps track of where every piece of text came from, selects the parts that bear
on the question, fits them to a stated budget, and hands over a **ContextPackage**:
a structured, portable object that says what is being sent, where each piece came
from, what was left out, and why.

When the answer comes back, tsumugi checks the model's citations against the
text it actually sent. A claim whose quotation resolves to a real span in a real
document is `supported`. A claim whose quotation resolves to nothing is
`unsupported`, and says so.

```text
      your folder                                          the model
          │                                                    ▲
          ▼                                                    │
   ┌──────────────┐   ┌───────────┐   ┌──────────┐   ┌─────────┴────────┐
   │  ingest      │──>│  select   │──>│  fit to  │──>│  ContextPackage  │
   │  anchor      │   │  rank     │   │  budget  │   │  + what was left │
   │  index       │   │           │   │          │   │    out, and why  │
   └──────────────┘   └───────────┘   └──────────┘   └─────────┬────────┘
          ▲                                                    │
          │                                                    ▼
   ┌──────┴───────────────────────────────────────────────────────────┐
   │  verify: every citation resolved against the text that was sent  │
   └──────────────────────────────────────────────────────────────────┘
```

## The one thing to be clear about

**A verified citation means the quoted text exists where the model said it does.
It does not mean the claim is true.**

tsumugi does not eliminate hallucination and does not claim to. It makes the
relationship between a generated sentence and the evidence behind it *checkable*,
which is a smaller promise and a keepable one. A model can quote your notes
accurately and still draw a wrong conclusion from them. tsumugi will tell you the
quote is real. Judging the conclusion is still your job.

Any wording that suggests otherwise is a defect, not a marketing choice.

## Three projects, one boundary each

tsumugi is the middle of three local-first libraries that share a constitution
and share no code.

```text
                 ┌───────────────┐
                 │    KISEKI     │   Remember
                 │               │   your past, from what you recorded
                 └───────┬───────┘
                         │
                         ▼
┌─────────────┐   ┌───────────────┐
│ Local files │──▶│    TSUMUGI    │   Connect
│ Notes       │   │               │   what you know, with its evidence
│ Code        │   └───────┬───────┘
│ Research    │           │
└─────────────┘     ContextPackage
                          │
                          ▼
                  ┌───────────────┐
                  │    MAMORI     │   Protect
                  │               │   the boundary you send it across
                  └───────┬───────┘
                          │
                          ▼
                     external AI
```

| | Answers | Owns |
|---|---|---|
| **kiseki** | What happened, and what it suggests you care about | Personal history |
| **tsumugi** | What is worth sending, and where it came from | Knowledge, evidence, context |
| **mamori** | Whether it is safe to send | The privacy boundary |

**tsumugi decides what to send. mamori decides whether it may leave.** These are
different questions, and merging them produces a tool that is bad at both: a
selector that also redacts will start selecting for redactability.

The connections are adapters, and the direction of dependency is one-way:
**tsumugi's core does not import kiseki or mamori, and works without either
installed.** A user who wants only a context builder gets one. This is a hard
constraint, checked by the build, not an aspiration.

## Principles

**Evidence first.** Every piece of context can name the document, the offset and
the hash it came from. A piece of text that cannot is not context, it is a guess.

**Deterministic core.** Selection, anchoring, verification and budgeting are
ordinary code with no model in them. A model may propose; it never decides.
Same corpus, same query, same settings, same package — byte for byte
([ADR 0003](adr/0003-a-package-is-reproducible.md)).

**Local first.** The core needs no network. Not "works offline as a fallback" —
the network is not in the design.

**Runtime dependencies: zero.** The core is the standard library. SQLite and its
FTS5 extension are in `sqlite3`; hashing is in `hashlib`; JSON is in `json`. Test
and development tools are not runtime dependencies and are kept separate.

**Fail closed.** Data whose provenance cannot be verified is not marked verified.
An unresolvable citation is `unsupported`, never silently dropped and never
optimistically accepted.

**The source is the truth.** Summaries, embeddings, scores and indexes are
derived data: replaceable, rebuildable, and never confused with the document.

**Say what you did not do.** A package reports what it excluded and why with the
same care as what it included ([ADR 0005](adr/0005-selection-is-a-report.md)).
The most valuable thing tsumugi can tell you is that something relevant did not
fit.
