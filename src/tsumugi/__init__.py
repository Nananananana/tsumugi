"""tsumugi -- local-first context infrastructure for generative AI.

Read a folder of documents, keep track of where every piece of text came from,
select the parts that bear on a question, fit them to a budget you chose, and
hand over a ContextPackage that says what is being sent, where each piece came
from, what was left out, and why.

A verified citation means the quoted text exists where the model said it does.
**It does not mean the claim is true.** See ``docs/concept.md``.

**The public surface is this module's ``__all__``, and the rule it follows is
here rather than only in an ADR.** A name leaves it only after a release in
which it still works and emits a ``DeprecationWarning`` naming its
replacement; adding a name breaks nobody and is free. A test pins the list, so
a rename arrives as an edit to a list of public names rather than as a surprise
in your build.

That test is not in the wheel, and `kiseki` is right that it therefore does not
travel: a downstream reading this has the code, not the suite. So the promise
is written where the code is. What you can check yourself is
``sorted(tsumugi.__all__)`` against what you import.

Nothing here is stable yet. The version is a development one, the rule above is
the mechanism rather than a guarantee that 1.0 has arrived, and the surface is
one day old with two consumers.
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
from .domain.ordering import ORDERINGS, by_score, maximal_marginal_relevance
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
from .interop import as_documents, texts_from
from .version import __version__

__all__ = [
    "CONTRACT_SCHEMA_NAME",
    "ORDERINGS",
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
    "as_documents",
    "ask",
    "build_context",
    "by_score",
    "connect",
    "contract_schema",
    "contract_schema_text",
    "cost_model_for",
    "ingest_paths",
    "maximal_marginal_relevance",
    "parse_answer",
    "parser_for",
    "register_block_kind",
    "remembered_roots",
    "resolve",
    "search",
    "texts_from",
    "trace_quotation",
    "verify_answer",
    "walk",
]
