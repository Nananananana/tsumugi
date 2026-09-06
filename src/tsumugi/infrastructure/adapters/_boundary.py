"""Where a provider sends, and whether that is this machine.

Two adapters carried this check, word for word, and a third would have carried
it again. The rule they share is the one that matters in a local-first library:
**a mistyped URL must not be able to post a person's notes to a host on the
internet.** The check is on the boundary rather than on the spelling of
"localhost" -- borrowed from `mamori`'s ADR-0015, for the same reason.
"""

from __future__ import annotations

import urllib.parse
from typing import Final

from ...errors import ConfigurationError
from ...ports.llm import Endpoint

__all__ = ["endpoint_of", "refuse_remote_unless_allowed"]

#: Hosts that are this machine. Anything else is outside the boundary.
_LOCAL: Final = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", ""})


def endpoint_of(url: str) -> Endpoint:
    """Parse a provider URL and say which side of the boundary it is on."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ConfigurationError(f"{url!r} is not an http or https URL")
    return Endpoint(url=url, is_local=(parsed.hostname or "") in _LOCAL)


def refuse_remote_unless_allowed(endpoint: Endpoint, allow_remote: bool) -> None:
    """Raise before a byte is sent, unless the caller said so in as many words."""
    if not endpoint.is_local and not allow_remote:
        raise ConfigurationError(
            f"{endpoint.url} is not this machine, and sending a ContextPackage there "
            f"would put your notes on somebody else's host. Pass allow_remote=True (or "
            f"--allow-remote) if that is genuinely what you want."
        )
