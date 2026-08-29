# 2. The ContextPackage is a document, not a type

**Status:** accepted

## Context

The draft specification described `ContextPackage` as a Python dataclass with
eight fields. As a description of the data it was right. As a description of what
the object *is*, it quietly settled the project's ceiling.

A Python class can only be used by Python programs that import tsumugi. Everything
the draft claims about being "Context Infrastructure" — that kiseki feeds it, that
mamori guards it, that it is the interchange between local knowledge and an
external model — requires something a program can *hold* without importing
anything.

The sibling project `kiseki` already ran this experiment. Its `PhotoRecord v1` is
a published document contract with a JSON Schema and a producer conformance
suite. The result is that `kiseki-ingest` is merely the *reference* producer:
anything that emits a conforming record is a source, and adding one costs no
change to the core. That is a different kind of project from one with a plugin
interface.

## Decision

`ContextPackage` is a versioned, serializable document contract:
`tsumugi.context-package/1`.

- It has a published JSON Schema in `schemas/`.
- It has a conformance suite that any producer can run.
- It is complete: a consumer holding a package needs nothing else from tsumugi.
- A consumer that does not recognise `contract` refuses the package rather than
  guessing at it.

The Python dataclass still exists. It is a *rendering* of the contract, not the
contract.

The full contract is [docs/context-package.md](../context-package.md).

## Consequences

The boundary with `mamori` becomes possible. A package can be serialized, passed
through a redactor, and reconstructed, because it was a document all along. A
class would have needed a serialization format invented for that one crossing,
and it would have been this one.

Other producers become possible: a package assembled from a wiki, a ticket system
or another machine is a first-class citizen if it conforms.

Versioning becomes explicit rather than accidental. A field added in a hurry is
visible in a schema diff.

The contract can be reviewed independently of the implementation, which is why it
is being reviewed now, before there is one.

## What it costs

Two representations to keep in step — the schema and the dataclasses — with no
pydantic to derive one from the other ([ADR 0001](0001-the-domain-depends-on-nothing.md)).
A conformance test that validates real output against the published schema is the
only thing standing between them, and it has to be written before the first
package is emitted, not after.

A contract that is public is a contract that is expensive to change. This is
mitigated by freezing at v0.2 rather than v0.1, and by marking the version
`1-draft` until then — but only partly. The first version of a public contract is
always a little wrong, and the cost of that is paid later, by everyone.
