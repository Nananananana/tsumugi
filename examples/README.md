# Examples

Three ways in.

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

```bash
ollama pull qwen2.5:7b-instruct
python examples/ask.py                  # or: python examples/ask.py llama3.1:8b
```

The loop closed: build, send to a model on this machine, check what comes back.
Prints why and exits cleanly if ollama is not running, because everything except
the last step works without it. `tsumugi demo --model NAME` shows the same thing
inside the walk-through.

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
| An answer, end to end | `+ ask` and a local model |

Only the last row needs a model, and it is the only row with an outbound path.
It refuses a host that is not this machine unless you say `--allow-remote` in as
many words: this index holds a copy of your corpus, and a mistyped URL should not
be enough to post it somewhere.
