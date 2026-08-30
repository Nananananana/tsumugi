# The schema moved

`context-package-1.json` now lives at
[`src/tsumugi/schemas/context-package-1.json`](../src/tsumugi/schemas/context-package-1.json).

**There is one copy, and this directory is not it.** That is the decision, not
a consequence of the move: two copies kept equal by a test is a weaker
arrangement, because a test can hold two files identical and still not say
which one is the contract when somebody is reading the wrong one. So there is
nothing here to disagree with, and
`test_there_is_exactly_one_copy_of_the_contract` fails if a `.json` reappears
in this directory — which is the obvious repair when a link breaks, and the one
that would quietly restore the ambiguity. The argument is `musubi`'s.

It moved **into the package** on 2026-08-30, because that is where a file has to
be to be packaged. The contract ships inside the wheel so that a consumer
validating a ContextPackage does not have to fetch a schema from the internet —
and that promise had been living in a build-config comment, kept alive by a
`force-include` rule that no code exercised and that did not apply to editable
installs at all. Deleting it would have broken the promise silently, forever,
with every test still passing.

Two copies would have drifted, so there is one.

## Reading it

From an installed tsumugi, with no network and no path:

```python
import tsumugi

schema = tsumugi.contract_schema()  # parsed
raw = tsumugi.contract_schema_text()  # the bytes, to hash or vendor
```

Vendoring the file directly is still fine and is what the sibling projects do —
take it from the path above, and record the commit it came from.

`$id` inside the document still reads
`https://github.com/Nananananana/tsumugi/schemas/context-package-1.json`. It is
**deliberately unchanged**: `$id` is an identifier rather than a location, it
was never a fetchable URL, and version 1 is frozen — changing it would put a
spurious diff into every vendored copy for no benefit.
