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

from .application.ask import Asked, ask
from .application.build_context import build_context, cost_model_for
from .application.ingest import IngestReport, ingest_paths
from .application.search import SearchResult, search
from .application.trace import trace_quotation
from .application.verify import (
    AnswerFormatError,
    ProtectedPackageError,
    parse_answer,
    verify_answer,
)
from .contract import CONTRACT_SCHEMA_NAME, contract_schema, contract_schema_text
from .domain.anchor import Anchor, Resolution, ResolutionStatus, resolve
from .domain.budget import Budget, Unit
from .domain.claim import Support
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
from .infrastructure.cost.heuristic import ByteCost, CharacterCost, HeuristicTokenCost
from .infrastructure.filesystem import walk
from .infrastructure.freshness import remembered_roots
from .infrastructure.index.fts import FtsIndex
from .infrastructure.parsers import parser_for
from .infrastructure.storage.database import connect
from .infrastructure.storage.ledger import SqliteLedger
from .infrastructure.storage.sqlite import SqliteDocumentStore
from .version import __version__

__all__ = [
    "CONTRACT_SCHEMA_NAME",
    "Anchor",
    "AnswerFormatError",
    "Asked",
    "Block",
    "Budget",
    "ByteCost",
    "CharacterCost",
    "ConfigurationError",
    "ContentHash",
    "Document",
    "FtsIndex",
    "HeuristicTokenCost",
    "IngestReport",
    "IngestionError",
    "ProtectedPackageError",
    "Resolution",
    "ResolutionStatus",
    "SearchResult",
    "Section",
    "Span",
    "SqliteDocumentStore",
    "SqliteLedger",
    "StorageError",
    "Support",
    "TsumugiError",
    "Unit",
    "UnsupportedContractError",
    "__version__",
    "ask",
    "build_context",
    "connect",
    "contract_schema",
    "contract_schema_text",
    "cost_model_for",
    "ingest_paths",
    "parse_answer",
    "parser_for",
    "register_block_kind",
    "remembered_roots",
    "resolve",
    "search",
    "trace_quotation",
    "verify_answer",
    "walk",
]
