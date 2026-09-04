"""One adapter, most local servers: vLLM, llama.cpp, LM Studio, TGI, Ollama.

`POST /v1/chat/completions` is the shape almost every local inference server
speaks, whatever it is underneath. Writing an adapter per server would be four
adapters that differ in their base URL, so this is one adapter and the URL is
the parameter.

**vLLM is why this exists**, and the reason is throughput rather than a
different API. Ollama serves one request at a time; vLLM batches continuously,
so the same model on the same GPU answers a queue several times faster. Nothing
in tsumugi has to know that -- it asks for text and gets text
([ports/llm.py](../../ports/llm.py)) -- which is the point of the port. Pointing
this at `http://127.0.0.1:8000/v1` is the whole of the change.

Same boundary rule as `ollama.py`, and for the same reason: this library reads
a person's entire notes folder, and a default that would post a package to a
host on the internet because a URL was mistyped is not a default a local-first
library gets to have. The check is on the boundary rather than on the spelling
of "localhost".

Stdlib only. An OpenAI-compatible request is a JSON body and a bearer token,
and the `openai` package would be a dependency for the part that is already
easy ([ADR 0025](../../../docs/adr/0025-outside-the-domain-a-library-may-help.md)
asks what a library buys before it is added; here it buys retries and a typed
client for four lines of `urllib`).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Final

from ...errors import ConfigurationError
from ...ports.llm import Endpoint
from .ollama import ProviderError

__all__ = ["DEFAULT_URL", "OpenAICompatibleProvider"]

#: vLLM's default. llama.cpp's server uses 8080, LM Studio 1234, and Ollama
#: exposes the same API at 11434/v1 -- all of them this class, another URL.
DEFAULT_URL: Final = "http://127.0.0.1:8000/v1"

#: Hosts that are this machine. Anything else is outside the boundary.
_LOCAL: Final = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", ""})


class OpenAICompatibleProvider:
    """Satisfies :class:`~tsumugi.ports.llm.LLMProvider`.

    ``model`` has no default. vLLM serves whatever it was started with and
    rejects any other name, and guessing one would produce a 404 that reads
    like the server being down.
    """

    def __init__(
        self,
        model: str,
        *,
        url: str = DEFAULT_URL,
        timeout: float = 120.0,
        allow_remote: bool = False,
        api_key: str = "",
        label: str = "openai-compatible",
    ) -> None:
        if not model.strip():
            raise ConfigurationError(
                "an OpenAI-compatible server serves a named model and refuses any "
                "other name; pass the one it was started with (--model)"
            )
        self._model = model
        self._endpoint = _endpoint(url)
        self._timeout = timeout
        self._api_key = api_key
        self._label = label

        if not self._endpoint.is_local and not allow_remote:
            raise ConfigurationError(
                f"{url} is not this machine, and sending a ContextPackage there would "
                f"put your notes on somebody else's host. Pass allow_remote=True (or "
                f"--allow-remote) if that is genuinely what you want."
            )

    @property
    def name(self) -> str:
        """Carries the server label as well as the model.

        The same model name means different things served by different
        runtimes, and this string is recorded with an answer so a reader can
        tell what produced the claims that were checked.
        """
        return f"{self._label}/{self._model}"

    @property
    def endpoint(self) -> Endpoint:
        return self._endpoint

    def generate(self, prompt: str) -> str:
        """Ask, and hand back exactly what came back.

        Raises rather than returning an empty string: an empty answer verifies
        as zero claims, and zero claims reads as success.
        """
        payload = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                # Deterministic where the server allows it. A package is
                # reproducible (ADR-0003) and an answer that is not makes the
                # ledger's picture noisier than it needs to be.
                "temperature": 0.0,
                "seed": 0,
                "stream": False,
            }
        ).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        request = urllib.request.Request(  # noqa: S310 - scheme checked in _endpoint
            f"{self._endpoint.url.rstrip('/')}/chat/completions",
            data=payload,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                body: Any = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ProviderError(
                f"{self.name} answered {error.code}. A 404 here usually means the "
                f"server is serving a different model than {self._model!r}."
            ) from error
        except urllib.error.URLError as error:
            raise ProviderError(
                f"cannot reach {self._endpoint.url} ({error.reason}). Is the server "
                f"running? vLLM: `vllm serve <model> --port 8000`."
            ) from error
        except (TimeoutError, OSError) as error:
            raise ProviderError(f"{self.name} did not answer in {self._timeout:.0f}s") from error
        except json.JSONDecodeError as error:
            raise ProviderError(f"{self.name} returned something that is not JSON") from error

        return _text_of(body, self.name)


def _text_of(body: Any, name: str) -> str:
    """Dig the message out, and refuse anything that is not text.

    Every step is checked rather than chained, because a server that returns a
    differently-shaped body should say so here and not raise an `IndexError`
    three frames away.
    """
    if not isinstance(body, dict):
        raise ProviderError(f"{name} returned {type(body).__name__}, not an object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError(f"{name} returned no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    answer = message.get("content") if isinstance(message, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        raise ProviderError(f"{name} returned no text")
    return answer


def _endpoint(url: str) -> Endpoint:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ConfigurationError(f"{url!r} is not an http or https URL")
    return Endpoint(url=url, is_local=(parsed.hostname or "") in _LOCAL)
