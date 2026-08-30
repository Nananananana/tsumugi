"""The published contract, readable from the installed package.

The ContextPackage schema ships inside the wheel so that a consumer validating
a package does not have to fetch anything from the internet. That promise used
to live in a comment in ``pyproject.toml`` and nowhere else: no code read the
packaged copy, so deleting the line that shipped it would have broken the
promise silently and permanently, in a repository where every test still
passed. `musubi` found the shape; this is the answer to it.

So the promise has an API now, and the API is what the tests use. A schema that
stops shipping stops being importable, and a file that moves inside the package
breaks the import rather than the wheel.

The file is also readable in the repository at
``src/tsumugi/schemas/context-package-1.json``. It moved there from the
repository root when this module was written -- inside the package is where a
file has to be to be packaged, and two copies would have drifted.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any, Final

from .domain.package import UnsupportedContractError

__all__ = ["CONTRACT_SCHEMA_NAME", "contract_schema", "contract_schema_text"]

#: The one schema published today. Version 1 is frozen and **closed**: nothing
#: may be added, removed, or change meaning (ADR-0022), and a change a consumer
#: must notice takes a new version and a new file beside this one.
CONTRACT_SCHEMA_NAME: Final = "context-package-1.json"


def _filename(name: str) -> str:
    """A schema filename, from either a filename or a contract identifier.

    ``tsumugi.context-package/1`` -> ``context-package-1.json``. A name with no
    ``/`` is already a filename and is returned as it came.
    """
    if "/" not in name:
        return name
    # `-draft` is a state of the *document*, not a different schema: the
    # pattern in `context-package-1.json` is `...1(-draft)?$`, so that file
    # describes both. Deriving `context-package-1-draft.json` would refuse a
    # pre-freeze package using the very schema that covers it -- and those are
    # exactly the packages a consumer still has lying around.
    return name.removeprefix("tsumugi.").removesuffix("-draft").replace("/", "-") + ".json"


def contract_schema_text(name: str = CONTRACT_SCHEMA_NAME) -> str:
    """The schema as it ships, verbatim.

    Takes a filename **or a contract identifier** -- pass
    ``package["contract"]`` straight through, and never build the mapping
    yourself.

    `seam` asked for this after writing that mapping. One line today, because
    there is one contract; the reason it should not live in a consumer is what
    happens when there are two. A consumer whose table still says
    ``/1 -> context-package-1.json`` when a ``/2`` package arrives validates
    that package against the wrong schema **and it passes**, because the parts
    ``/2`` shares with ``/1`` are most of it. The family's rule -- refuse a name
    you do not know -- gets switched off inside the consumer's own lookup.

    Bytes rather than a parsed object, for a caller that wants to hash it,
    vendor it, or hand it to a validator in another language.
    """
    try:
        return (resources.files("tsumugi") / "schemas" / _filename(name)).read_text(
            encoding="utf-8"
        )
    except FileNotFoundError:
        if "/" not in name:
            # A filename that is not there is a path mistake, and
            # `FileNotFoundError` names it better than anything this module
            # could invent. Only an *identifier* is a contract situation.
            raise
        raise UnsupportedContractError(
            f"tsumugi does not publish a schema for {name!r}. Refuse the "
            "package rather than validating it against another version"
        ) from None


def contract_schema(name: str = CONTRACT_SCHEMA_NAME) -> dict[str, Any]:
    """The schema, parsed.

    tsumugi does not validate against it -- there is no JSON Schema
    implementation in the core and there will not be one (ADR-0001). This is
    for the consumer that has one, and the point is that they do not need the
    network to get the schema.
    """
    parsed: dict[str, Any] = json.loads(contract_schema_text(name))

    # Derivation is a guess until the schema agrees with it. Asking the file
    # whether it really describes the contract that was requested is what makes
    # this safe to hand a value read out of a document: a `/2` identifier can
    # never come back holding `/1`'s schema, because `/1`'s own pattern refuses
    # it. `seam` wrote this check defensively on the consumer side; it belongs
    # here, where every consumer gets it.
    if "/" in name:
        pattern = parsed.get("properties", {}).get("contract", {}).get("pattern")
        if pattern is None or not re.fullmatch(pattern, name):
            raise UnsupportedContractError(
                f"the schema published for {name!r} does not describe it. Refuse the package"
            )
    return parsed
