# Examples

Two ways in.

```bash
tsumugi demo
```

The whole pipeline in a throwaway directory — a small rigged corpus, a question,
what was left out and why, an answer checked against it, and a quotation traced
back to a line. No model, no network, and it does not touch whatever index you
already have.

```bash
python examples/library.py
```

The same shape as ordinary Python, with every step commented for *why* rather
than *what*. Read this one if you are going to call tsumugi from your own code.

---

## Which pieces you actually need

| You want | You need |
|---|---|
| Search your notes, with offsets | `ingest` + `search` |
| Context for a model, with a budget | `+ build_context` |
| To know what was left out | it is already there — read `omissions` |
| To check an answer's citations | `+ verify_answer` |
| To know what you send and never use | `+ SqliteLedger` |
| An agent to do all of it | `tsumugi mcp` |

Nothing above needs a model. The one thing that does is generating the answer,
and that is a step tsumugi does not take for you unless you ask it to.
