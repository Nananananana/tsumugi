"""JSON-RPC 2.0 over stdio, on the standard library.

MCP's stdio transport is newline-delimited JSON: one object per line, requests
in on ``stdin``, responses out on ``stdout``. That is the whole framing, which
is why an agent-facing surface costs no dependency (ADR-0012).

Two rules that are security properties rather than style:

**Nothing on stdout that is not a response.** A stray ``print`` corrupts the
stream and the client sees a parse error it cannot attribute. Diagnostics go to
``stderr``.

**Strict parsing, and unknown methods are refused.** The input is JSON-RPC from
a client the user configured, but "the user configured it" is not a security
argument.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO, Any, Final

__all__ = [
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "Request",
    "RpcError",
    "read_requests",
    "write_message",
]

PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602
INTERNAL_ERROR: Final = -32603


class RpcError(Exception):
    """An error with a JSON-RPC code, ready to be reported to the client."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True, slots=True)
class Request:
    """One incoming message."""

    method: str
    params: dict[str, Any]
    #: ``None`` for a notification, which takes no response.
    id: str | int | None = None

    @property
    def is_notification(self) -> bool:
        return self.id is None

    @property
    def meta(self) -> dict[str, Any]:
        """``params._meta``, which is where MCP puts protocol metadata now.

        The 2026-07-28 revision made the protocol stateless: there is no
        handshake and no session, so every request carries its own version and
        capabilities here. An older client sends none of it, which is how a
        server tells the two eras apart.
        """
        found = self.params.get("_meta")
        return found if isinstance(found, dict) else {}

    @property
    def protocol_version(self) -> str | None:
        """The revision this request says it speaks, or ``None`` if it did not.

        ``None`` means a client from before the handshake was retired. It is
        not an error here: this server answers both, and refusing the older one
        would refuse most clients shipped today.
        """
        found = self.meta.get("io.modelcontextprotocol/protocolVersion")
        return found if isinstance(found, str) else None

    def require(self, name: str) -> Any:
        if name not in self.params:
            raise RpcError(INVALID_PARAMS, f"missing required parameter {name!r}")
        return self.params[name]

    def string(self, name: str, default: str | None = None) -> str:
        value = self.params.get(name, default)
        if value is None:
            raise RpcError(INVALID_PARAMS, f"missing required parameter {name!r}")
        if not isinstance(value, str):
            raise RpcError(INVALID_PARAMS, f"{name!r} must be a string")
        return value

    def integer(self, name: str, default: int) -> int:
        value = self.params.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise RpcError(INVALID_PARAMS, f"{name!r} must be a whole number")
        return value

    def number(self, name: str, default: float) -> float:
        value = self.params.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise RpcError(INVALID_PARAMS, f"{name!r} must be a number")
        return float(value)


def read_requests(stream: IO[str] | None = None) -> Iterator[Request | RpcError]:
    """Parse messages until the stream ends.

    Yields an :class:`RpcError` rather than raising, so that a malformed line
    is reported to the client and the loop continues. One bad message should
    not end a session.
    """
    source = stream if stream is not None else sys.stdin
    for line in source:
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            yield RpcError(PARSE_ERROR, f"not valid JSON: {error}")
            continue

        if not isinstance(payload, dict):
            yield RpcError(INVALID_REQUEST, "a request is an object")
            continue
        method = payload.get("method")
        if not isinstance(method, str):
            yield RpcError(INVALID_REQUEST, "a request needs a string 'method'")
            continue
        # Absent or null is fine; anything else has to be an object. An empty
        # array is JSON-RPC's positional form, which this does not implement,
        # and quietly reading it as {} would be lenient in the direction that
        # hides a client bug.
        raw = payload.get("params")
        if raw is None:
            params: dict[str, Any] = {}
        elif isinstance(raw, dict):
            params = raw
        else:
            yield RpcError(
                INVALID_PARAMS,
                f"'params' must be an object; positional parameters are not supported "
                f"(got {type(raw).__name__})",
            )
            continue
        identifier = payload.get("id")
        if identifier is not None and not isinstance(identifier, str | int):
            yield RpcError(INVALID_REQUEST, "'id' must be a string or a number")
            continue

        yield Request(method=method, params=params, id=identifier)


def write_message(payload: dict[str, Any], stream: IO[str] | None = None) -> None:
    """Write one message, as a single line.

    Flushed immediately: a client blocked on a response it cannot see is
    indistinguishable from a server that has hung.
    """
    out = stream if stream is not None else sys.stdout
    out.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    out.flush()
