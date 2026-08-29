"""The agent-facing surface: four read-only tools over the same use cases.

The thing that most wants a ContextPackage is not a person composing a prompt.
It is an agent already holding a conversation, which needs a slice of local
knowledge with its provenance and cannot pause to have a human run a CLI
(ADR-0012).

Three constraints make this safe to run inside somebody else's agent loop:

**Read-only.** ``ingest`` and ``forget`` are not exposed. A tool an agent can
call must not be able to rewrite the corpus or the index. That is the rule that
bounds the damage rather than trying to prevent every case, and adding a fifth
tool that writes would end it.

**The full package, including omissions.** An agent that cannot see the edge of
a selection has the same problem as a person who cannot.

**The same application layer as the CLI.** Both are thin shells over the same
use cases; a behaviour available in one and not the other is a defect.

Document text goes out to the caller. Nothing that comes back is ever executed,
fetched or written: the tools do not shell out, do not open sockets, and do not
touch the store.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import IO, Any, Final

from ... import __version__
from ...application.build_context import build_context
from ...application.search import search as run_search
from ...application.trace import trace_quotation
from ...application.verify import verify_answer
from ...config import TsumugiConfig
from ...domain.budget import Budget, Unit
from ...domain.package import ContextPackage
from ...errors import TsumugiError
from ...infrastructure.cost.heuristic import ByteCost, CharacterCost, HeuristicTokenCost
from ...infrastructure.index.fts import FtsIndex
from ...infrastructure.storage.database import connect
from ...infrastructure.storage.ledger import SqliteLedger
from ...infrastructure.storage.sqlite import SqliteDocumentStore
from ...ports.cost import CostModel
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    Request,
    RpcError,
    read_requests,
    write_message,
)

__all__ = ["TOOLS", "McpServer", "serve"]

#: The version of the MCP spec this speaks. A client asking for another one is
#: answered with this rather than refused: the handshake is a negotiation, and
#: refusing an unknown string would break against every future client.
PROTOCOL_VERSION: Final = "2025-06-18"

_BUDGET_HELP = (
    "tokens:8000, characters:20000 or bytes:65536. The unit is required. Tokens are "
    "estimated and the package states the estimator's measured error; characters and "
    "bytes are counted exactly."
)

TOOLS: Final[list[dict[str, Any]]] = [
    {
        "name": "search",
        "description": (
            "Find passages of the local corpus that bear on a query. Returns spans with "
            "the document, section and character offsets they came from. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "context",
        "description": (
            "Build a ContextPackage for a question: the passages that bear on it, fitted "
            "to a budget, each anchored to the document it came from. The result also "
            "lists what was CONSIDERED AND LEFT OUT, under omissions[], with the rule "
            "that dropped each candidate. Read that field: what did not fit is often "
            "more important than what did, and the selection has edges you cannot "
            "otherwise see. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "budget": {
                    "type": "string",
                    "default": "characters:4000",
                    "description": _BUDGET_HELP,
                },
                "min_score": {"type": "number", "default": 0.0},
            },
        },
    },
    {
        "name": "trace",
        "description": (
            "Find where a quotation came from. Exact matching only: a quotation either "
            "occurs in the corpus or it does not, and there is no fuzzy match. Several "
            "occurrences are all reported. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["quotation"],
            "properties": {
                "quotation": {"type": "string"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "verify",
        "description": (
            "Check an answer's citations against the package it was built from. Each "
            "claim comes back supported, unsupported, uncited or unverifiable. NOTE: "
            "a supported claim means the quoted text is where you said it was. It does "
            "NOT mean the claim is true. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["answer", "package"],
            "properties": {
                "answer": {
                    "type": "string",
                    "description": (
                        'JSON: {"claims": [{"text": "...", "citations": ["quoted text"]}]}. '
                        "Quote the text you relied on; do not report character offsets."
                    ),
                },
                "package": {
                    "type": "string",
                    "description": "The ContextPackage JSON that `context` returned.",
                },
            },
        },
    },
]


def _cost_model(unit: Unit) -> CostModel:
    if unit is Unit.TOKENS:
        return HeuristicTokenCost()
    if unit is Unit.BYTES:
        return ByteCost()
    return CharacterCost()


class McpServer:
    """One session. Opens the index lazily, so an empty corpus is a tool error
    rather than a server that will not start."""

    def __init__(self, config: TsumugiConfig) -> None:
        self._config = config
        self._connection: sqlite3.Connection | None = None

    # -- wiring ----------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = connect(self._config.resolved_index_path(), create=False)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # -- dispatch --------------------------------------------------------

    def handle(self, request: Request) -> dict[str, Any] | None:
        """Answer one request. ``None`` for a notification."""
        if request.method == "initialize":
            result = self._initialize(request)
        elif request.method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        elif request.method == "ping":
            result = {}
        elif request.method == "tools/list":
            result = {"tools": TOOLS}
        elif request.method == "tools/call":
            result = self._call(request)
        else:
            raise RpcError(METHOD_NOT_FOUND, f"unknown method {request.method!r}")
        return result

    def _initialize(self, request: Request) -> dict[str, Any]:
        asked = request.params.get("protocolVersion")
        return {
            "protocolVersion": asked if isinstance(asked, str) else PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tsumugi", "version": __version__},
            "instructions": (
                "Local knowledge with its evidence attached. Use `context` to get "
                "passages for a question -- and read its omissions[], which names what "
                "was considered and left out. Use `trace` to check where a quotation "
                "came from, and `verify` to check an answer's citations. A resolved "
                "citation means the text is where it was said to be; it does not mean "
                "the claim is true."
            ),
        }

    def _call(self, request: Request) -> dict[str, Any]:
        name = request.string("name")
        arguments = request.params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "'arguments' must be an object")
        call = Request(method=name, params=arguments, id=request.id)

        handlers = {
            "search": self._search,
            "context": self._context,
            "trace": self._trace,
            "verify": self._verify,
        }
        handler = handlers.get(name)
        if handler is None:
            # Named explicitly so that an agent reaching for a write tool is
            # told it does not exist here, rather than getting a generic error.
            raise RpcError(
                INVALID_PARAMS,
                f"unknown tool {name!r}. This server is read-only and offers "
                f"{', '.join(sorted(handlers))}.",
            )

        try:
            return _content(handler(call))
        except TsumugiError as error:
            # A tool failure is a result with isError, not a protocol error:
            # the request was well-formed and the agent can act on the message.
            return _content(str(error), is_error=True)
        except (ValueError, sqlite3.DatabaseError) as error:
            return _content(str(error), is_error=True)

    # -- the four tools --------------------------------------------------

    def _search(self, call: Request) -> Any:
        connection = self._open()
        results, truncation = run_search(
            call.string("query"),
            store=SqliteDocumentStore(connection),
            index=FtsIndex(connection),
            limit=call.integer("limit", 10),
            candidate_limit=self._config.candidate_limit,
        )
        return {
            "results": [
                {
                    "text": result.text,
                    "source_path": result.source_path,
                    "section": result.section,
                    "document_id": result.anchor.document_id,
                    "start": result.anchor.span.start,
                    "end": result.anchor.span.end,
                    "score": round(result.score, 4),
                    "confirmed": not result.unconfirmed,
                }
                for result in results
            ],
            "truncated": None if truncation is None else truncation.as_omission_reason(),
        }

    def _context(self, call: Request) -> Any:
        budget = Budget.parse(call.string("budget", "characters:4000"))
        connection = self._open()
        package = build_context(
            call.string("query"),
            store=SqliteDocumentStore(connection),
            index=FtsIndex(connection),
            cost_model=_cost_model(budget.unit),
            budget=budget,
            candidate_limit=self._config.candidate_limit,
            minimum_score=call.number("min_score", 0.0),
            version=__version__,
        )
        SqliteLedger(connection).open(package)
        return json.loads(package.to_json())

    def _trace(self, call: Request) -> Any:
        connection = self._open()
        traces = trace_quotation(
            call.string("quotation"),
            SqliteDocumentStore(connection),
            limit=call.integer("limit", 20),
        )
        return {
            "found": len(traces),
            "occurrences": [
                {
                    "source_path": trace.source_path,
                    "section": trace.section,
                    "line": trace.line,
                    "status": trace.status.value,
                    "detail": trace.resolution.detail,
                }
                for trace in traces
            ],
            "note": (
                "Exact matching only. Nothing found means the text is not in this "
                "corpus, not that it is nearly there."
            ),
        }

    def _verify(self, call: Request) -> Any:
        package = ContextPackage.from_json(call.string("package"))
        report = verify_answer(call.string("answer"), package)

        connection = self._open()
        SqliteLedger(connection).close(report)

        # The report's own serialisation, not a third hand-written copy. This
        # one had already drifted: it omitted `unverifiable_because`, so an
        # agent was told a claim was unverifiable and not why -- which is the
        # distinction ADR-0009 exists to preserve.
        return {
            **report.to_dict(),
            "note": (
                "A supported claim means the quoted text is where it was said to be. "
                "It does not mean the claim is true."
            ),
        }


def _content(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    text = (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    )
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def serve(
    config: TsumugiConfig,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> int:
    """Run one session until the input ends."""
    server = McpServer(config)
    try:
        for message in read_requests(stdin):
            if isinstance(message, RpcError):
                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": message.code, "message": message.message},
                    },
                    stdout,
                )
                continue

            try:
                result = server.handle(message)
            except RpcError as error:
                if not message.is_notification:
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message.id,
                            "error": {"code": error.code, "message": error.message},
                        },
                        stdout,
                    )
                continue
            except Exception as error:
                # One unexpected failure must not end the session. Diagnostics
                # to stderr; stdout carries responses and nothing else.
                print(f"tsumugi mcp: {type(error).__name__}: {error}", file=sys.stderr)
                if not message.is_notification:
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message.id,
                            "error": {"code": INTERNAL_ERROR, "message": str(error)},
                        },
                        stdout,
                    )
                continue

            if not message.is_notification:
                write_message({"jsonrpc": "2.0", "id": message.id, "result": result}, stdout)
    finally:
        server.close()
    return 0
