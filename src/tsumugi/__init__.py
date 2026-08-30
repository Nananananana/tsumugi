"""tsumugi -- local-first context infrastructure for generative AI.

Read a folder of documents, keep track of where every piece of text came from,
select the parts that bear on a question, fit them to a budget you chose, and
hand over a ContextPackage that says what is being sent, where each piece came
from, what was left out, and why.

A verified citation means the quoted text exists where the model said it does.
**It does not mean the claim is true.** See ``docs/concept.md``.

Nothing here is stable. The version is a development one and the public
surface will change.
"""

from __future__ import annotations

from .contract import CONTRACT_SCHEMA_NAME, contract_schema, contract_schema_text
from .domain.anchor import Anchor, Resolution, ResolutionStatus, resolve
from .domain.budget import Budget, Unit
from .domain.document import Block, Document, Section, register_block_kind
from .domain.hashing import ContentHash
from .domain.package import UnsupportedContractError
from .domain.span import Span
from .errors import (
    ConfigurationError,
    IngestionError,
    StorageError,
    TsumugiError,
)
from .version import __version__

__all__ = [
    "CONTRACT_SCHEMA_NAME",
    "Anchor",
    "Block",
    "Budget",
    "ConfigurationError",
    "ContentHash",
    "Document",
    "IngestionError",
    "Resolution",
    "ResolutionStatus",
    "Section",
    "Span",
    "StorageError",
    "TsumugiError",
    "Unit",
    "UnsupportedContractError",
    "__version__",
    "contract_schema",
    "contract_schema_text",
    "register_block_kind",
    "resolve",
]
