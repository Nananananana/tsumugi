# 12. An agent-facing surface, on the standard library

**Status:** accepted

## Context

The draft specification named the CLI as the primary interface and made
`tsumugi context` and `tsumugi prompt` the important user experience. That
assumes the user is a person, composing a prompt, who will read a rendered
context package and paste it somewhere.

That person exists. They are no longer the main consumer.

The thing that most wants a ContextPackage is a coding agent, a research agent or
an assistant already holding a conversation, which needs a slice of local
knowledge with its provenance and cannot pause to have a human run a CLI. For
that consumer, `tsumugi context "..."` on a terminal is the wrong shape entirely:
it needs to call, get structure back, and continue.

There is a second argument, about distribution. A library needs users to be worth
maintaining. A local knowledge tool with a CLI needs someone to decide to adopt a
workflow. The same tool speaking a protocol its user's assistant already knows
needs no adoption decision — the assistant discovers the tools and uses them.

MCP is that protocol, and its transport is JSON-RPC over stdio. No HTTP, no
framework, no dependency. Roughly the same amount of standard-library code as an
`argparse` CLI.

## Decision

**tsumugi ships an MCP server as a first-class interface, not an example.**

`tsumugi mcp` speaks JSON-RPC 2.0 over stdio, implemented on `json` and `sys.stdin`,
and exposes:

| Tool | Returns |
|---|---|
| `search` | ranked spans with anchors |
| `context` | a full ContextPackage, including `omissions[]` |
| `trace` | from a quotation back to document, offset and hash |
| `verify` | claim classifications for an answer |

Three constraints that make it safe to run inside somebody else's agent loop:

1. **Read-only by default.** `ingest` and `forget` are not exposed. A tool that
   an agent can call must not be able to rewrite the corpus or the index.
2. **The full package, including omissions.** The agent gets what the CLI shows,
   including what was left out and why. An agent that cannot see the edge of the
   selection has the same problem as a person who cannot
   ([ADR 0005](0005-selection-is-a-report.md)).
3. **The same application layer as the CLI.** Both interfaces are thin shells over
   the same use cases. A behaviour available in one and not the other is a defect.

`interfaces/mcp/` sits beside `interfaces/cli/`, with the same permission to
import everything below and nothing above.

## Consequences

Any MCP client — an assistant, an editor, an agent framework — becomes a user
without either side writing an integration.

The design's central claim gets tested by the harshest available reviewer. An
agent that asks for context and then answers with citations exercises build,
render, verify and trace in one loop, repeatedly, on real questions. That is a
better test than any fixture.

The ContextPackage contract earns its keep. `context` returns the document
described in [docs/context-package.md](../context-package.md), and a consumer
outside Python parses it because it is JSON with a schema
([ADR 0002](0002-the-context-package-is-a-document.md)).

## What it costs

A protocol to track. MCP is young and moving, and a spec change is maintenance
this project did not choose the timing of. The mitigation is that the surface is
four read-only tools over an application layer that exists anyway — if the
protocol changes badly, what is lost is the shell, not the library.

Two interfaces to keep in step. A conformance test that drives the same scenarios
through both is the guard, and it is a real cost.

A server reading untrusted input on stdin. The input is JSON-RPC from a client the
user configured, but "the user configured it" is not a security argument. Strict
parsing, no `eval`, refuse unknown methods, and the read-only rule above — which
is the one that actually matters, because it bounds the damage rather than trying
to prevent every case.

An MCP server is also a place where a request could arrive carrying text designed
to be read as an instruction. tsumugi returns document text to its caller; it
never acts on it. The tools do not write, do not shell out, and do not fetch. That
is a property of the four tools chosen, and adding a fifth that writes would end
it — which is the reason the read-only rule is in this ADR rather than in a
comment.

---

## Amendment, 2026-08-30: the 2026-07-28 revision

MCP went **stateless**. The `initialize`/`initialized` handshake and the
connection-scoped session are retired; every request now carries its own
protocol version and client capabilities in `_meta`, every result names its
`resultType`, and list results may declare how long they keep.

The interesting part is how little of this server changed. `handle()` never
read anything established by an earlier message -- that was a choice made
because a dispatch with no session is easier to test, and it turned out to be
the requirement three revisions later. What was added is what the new shape
*says*: `resultType`, `serverInfo` in a result's `_meta`, `ttlMs` on
`tools/list`, and `server/discover` as a second name for the facts
`initialize` used to return.

**Both eras are answered, and that is a decision rather than politeness.** A
server that dropped the handshake would stop working with every client shipped
before this revision, which is most of them today. The specification expects
implementations to detect the counterpart's era and fall back; here the tell is
whether a request carries `_meta.io.modelcontextprotocol/protocolVersion`. An
older client is told the version *it* asked for, because answering `2026-07-28`
to a client that does not implement it would be worse than answering nothing.

The cost is a second code path that has to keep working, and a date in this
repository that will go stale again. Both are cheaper than the alternative,
which is an agent surface that quietly stops being one.
