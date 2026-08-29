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

from .domain.anchor import Anchor, Resolution, ResolutionStatus, resolve
from .domain.document import Block, Document, Section, register_block_kind
from .domain.hashing import ContentHash
from .domain.span import Span
from .errors import (
    AnchorError,
    ConfigurationError,
    ContractError,
    IngestionError,
    StaleAnchorError,
    StorageError,
    TsumugiError,
    UnresolvableAnchorError,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "Anchor",
    "AnchorError",
    "Block",
    "ConfigurationError",
    "ContentHash",
    "ContractError",
    "Document",
    "IngestionError",
    "Resolution",
    "ResolutionStatus",
    "Section",
    "Span",
    "StaleAnchorError",
    "StorageError",
    "TsumugiError",
    "UnresolvableAnchorError",
    "__version__",
    "register_block_kind",
    "resolve",
]
