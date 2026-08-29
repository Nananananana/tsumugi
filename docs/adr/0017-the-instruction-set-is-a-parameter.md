# ADR-0017: The instruction set is a parameter, and the prompt is the package

*Accepted 2026-08-30.*

## The question

`proposals/0002` demoted prompt templates and named the condition for building
them: **when a second use needs a second shape.** For a long time that
condition did not fire, and saying so in the roadmap each time was the whole
value of having written it down.

It fired with `ask`. A program is going to check the answer, so the answer has
to be machine-readable; a person pasting a package into a chat window neither
needs that nor wants to read it. Two consumers, two shapes.

The first implementation appended a paragraph to `render()` on the way out.
That worked, and it was wrong in a way worth naming.

## What was wrong with appending

A package is **the record of a prompt**. The ledger stores its id. `--json`
publishes it. The README invites a reader to look at exactly what is about to
be sent, and calls that the only privacy control tsumugi offers directly.

A prompt assembled as `render()` plus something else makes all three of those
slightly false. Not dramatically — the appended paragraph held no document
text and no secret. But "the package describes what was sent" is either true
or it is a sentence that needs a footnote, and a claim with a footnote is the
kind this project spends ADRs removing.

There was a second, quieter cost. `package_id` is a hash of the package, and
two runs that produced *different prompts* were producing the same id. The
reproducibility guarantee (ADR-0003) was intact by its own terms and no longer
meant what a reader would take it to mean.

## The decision

`build_context` takes `instructions` and `output_schema`. Two sets ship, in
`application/instructions.py`:

- **`DEFAULT`** — a person is going to read the answer. No output format.
- **`ANSWERING`** — a program is going to check it. Adds the rules about what
  a citation is, and pairs with `ANSWER_SCHEMA`.

`ask` passes the second pair. `package.render()` is the entire prompt, and a
test asserts `asked.prompt == asked.package.render()` rather than
`render() in prompt`.

Both fields already existed in the frozen v1 contract. Nothing was added to it,
nothing changed meaning, and a consumer written against `1` reads these
packages unchanged.

**Two dictionaries, not a template language.** A template language is a thing
to maintain from the day it exists; two named dictionaries are not, and the
third shape can argue for itself when someone has one.

### The rules that were earned

`ANSWERING` says a citation is not a filename, not a heading and not a `[c1]`
label, and shows the two lines with the header marked. That is four sentences
longer than it looks like it needs to be, and every one of them was paid for:
`qwen2.5:14b-instruct` answered a Japanese question perfectly and cited
`notes/持ち物リスト.md (持ち物リスト（控え）)` — the header line above the
passage, which is what "citation" means everywhere outside this library. Every
claim reported unsupported and the answer was right.

## What it costs

**`package_id` now distinguishes the two.** A package built for a person and
one built for a model have different ids over identical evidence. That is
correct — they are different prompts — but it means an id is no longer a
handle for "the selection for this question", and anything that wanted that
must compare items and omissions instead. The test that asserts a provider
changes nothing was rewritten to do exactly that, and is a better test for it:
what must not vary is *which evidence was chosen*, and that is what it now
says.

**Two sets is a number that wants to grow.** The first person with a genuine
third shape will be right, and the second will be adding a variant because
variants are easy to add. `proposals/0002`'s rule still applies and is the only
thing holding the line: a shape ships when a use needs it, not when someone can
imagine one.

**The instruction text is now part of a reproducibility claim.** Editing a rule
changes every subsequent `package_id`. That is honest and it is also a
maintenance obligation nobody had before: a wording improvement is now a change
with a blast radius, and the ledger will show a discontinuity on the day it
lands.

## What was not decided

Per-language instruction sets, user-supplied templates on the CLI, and
instructions that vary with what was selected. All three are plausible; none
has a use asking for it.
