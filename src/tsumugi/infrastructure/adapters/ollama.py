"""A local model, over the standard library.

Ollama speaks HTTP and JSON, so this is `urllib` and `json` and nothing else —
the same trade the rest of the library makes, in the one place where a socket
is allowed to exist ([ADR 0016](../../../docs/adr/0016-the-network-lives-in-one-place.md)).

**It refuses a non-local endpoint unless told otherwise, in as many words.**
tsumugi reads a person's entire notes folder; a default that would post a
package to a host on the internet because a URL was mistyped is not a default a
local-first library gets to have. Borrowed from `mamori`'s ADR-0015, and for
the same reason: the check is on the boundary, not on the spelling of
"localhost".

The model is asked for text and never for a decision. It does not rank, does
not choose what is sent, and does not resolve a citation — so the worst a
hallucinating model can do here is write a claim that verification then reports
as unsupported, which is the system working.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Final

from ...errors import TsumugiError
from ...ports.llm import Endpoint
from ._boundary import endpoint_of, refuse_remote_unless_allowed

__all__ = ["DEFAULT_MODEL", "DEFAULT_URL", "OllamaProvider", "ProviderError"]

DEFAULT_URL: Final = "http://127.0.0.1:11434"
#: Small enough to run on a laptop, and multilingual, which this library needs.
DEFAULT_MODEL: Final = "qwen2.5:7b-instruct"


class ProviderError(TsumugiError):
    """The model could not be reached, or did not answer."""


class OllamaProvider:
    """Satisfies :class:`~tsumugi.ports.llm.LLMProvider`."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        url: str = DEFAULT_URL,
        timeout: float = 120.0,
        allow_remote: bool = False,
    ) -> None:
        self._model = model
        self._endpoint = endpoint_of(url)
        self._timeout = timeout

        refuse_remote_unless_allowed(self._endpoint, allow_remote)

    @property
    def name(self) -> str:
        return f"ollama/{self._model}"

    @property
    def endpoint(self) -> Endpoint:
        return self._endpoint

    def generate(self, prompt: str) -> str:
        """Ask the model, and hand back exactly what it said.

        Raises rather than returning an empty string on failure: an empty
        answer verifies as zero claims, and zero claims reads as success.
        """
        payload = json.dumps(
            {
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                # Deterministic where the model allows it. A package is
                # reproducible (ADR-0003) and an answer that is not makes the
                # ledger's picture noisier than it needs to be.
                "options": {"temperature": 0.0, "seed": 0},
            }
        ).encode("utf-8")

        request = urllib.request.Request(  # noqa: S310 - scheme checked in _endpoint
            f"{self._endpoint.url.rstrip('/')}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                body: Any = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ProviderError(
                f"{self.name} answered {error.code}. If the model is not pulled yet: "
                f"`ollama pull {self._model}`."
            ) from error
        except urllib.error.URLError as error:
            raise ProviderError(
                f"cannot reach {self._endpoint.url} ({error.reason}). Is ollama running?"
            ) from error
        except (TimeoutError, OSError) as error:
            raise ProviderError(f"{self.name} did not answer in {self._timeout:.0f}s") from error
        except json.JSONDecodeError as error:
            raise ProviderError(f"{self.name} returned something that is not JSON") from error

        answer = body.get("response") if isinstance(body, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise ProviderError(f"{self.name} returned no text")
        return answer
