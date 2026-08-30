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
from importlib import resources
from typing import Any, Final

__all__ = ["CONTRACT_SCHEMA_NAME", "contract_schema", "contract_schema_text"]

#: The one schema published today. Version 1 is frozen: a field may be added,
#: none will be removed or change meaning, and a change a consumer must notice
#: takes a new version and a new file beside this one.
CONTRACT_SCHEMA_NAME: Final = "context-package-1.json"


def contract_schema_text(name: str = CONTRACT_SCHEMA_NAME) -> str:
    """The schema as it ships, verbatim.

    Bytes rather than a parsed object, for a caller that wants to hash it,
    vendor it, or hand it to a validator in another language.
    """
    return (resources.files("tsumugi") / "schemas" / name).read_text(encoding="utf-8")


def contract_schema(name: str = CONTRACT_SCHEMA_NAME) -> dict[str, Any]:
    """The schema, parsed.

    tsumugi does not validate against it -- there is no JSON Schema
    implementation in the core and there will not be one (ADR-0001). This is
    for the consumer that has one, and the point is that they do not need the
    network to get the schema.
    """
    parsed: dict[str, Any] = json.loads(contract_schema_text(name))
    return parsed
