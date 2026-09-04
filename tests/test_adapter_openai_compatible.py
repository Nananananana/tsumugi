"""One adapter for vLLM, llama.cpp, LM Studio and TGI -- and the boundary.

Every test here runs with no network (`conftest`'s autouse fence), so what is
checked is the code around the socket: which URL is chosen, what is refused
before a byte is sent, and what happens to a body that is not the shape the
adapter expects.

The three that matter:

1. **a non-local endpoint is refused before anything is sent.** This library
   reads a whole notes folder; a mistyped URL must not be able to post it.
2. **`--server openai` does not inherit Ollama's port.** Pointing an
   OpenAI-compatible adapter at 11434 produces a 404 that reads exactly like a
   missing model, and somebody would spend an afternoon on it.
3. **an unexpected body raises where it happened**, not three frames away as an
   `IndexError`.
"""

from __future__ import annotations

import pytest

from tsumugi.errors import ConfigurationError
from tsumugi.infrastructure.adapters.ollama import ProviderError
from tsumugi.infrastructure.adapters.openai_compatible import (
    DEFAULT_URL,
    OpenAICompatibleProvider,
    _text_of,
)
from tsumugi.interfaces.cli.main import build_parser


class TestTheBoundary:
    def test_a_remote_host_is_refused_and_the_message_says_why(self) -> None:
        with pytest.raises(ConfigurationError) as raised:
            OpenAICompatibleProvider("m", url="https://api.example.com/v1")
        assert "not this machine" in str(raised.value)
        assert "allow_remote" in str(raised.value)

    def test_a_remote_host_is_allowed_when_the_caller_insists(self) -> None:
        provider = OpenAICompatibleProvider(
            "m", url="https://api.example.com/v1", allow_remote=True
        )
        assert provider.endpoint.is_local is False
        assert "REMOTE" in provider.endpoint.describe()

    @pytest.mark.parametrize("url", ["http://127.0.0.1:8000/v1", "http://localhost:8000/v1"])
    def test_this_machine_needs_no_permission(self, url: str) -> None:
        assert OpenAICompatibleProvider("m", url=url).endpoint.is_local is True

    def test_a_url_that_is_not_http_is_refused(self) -> None:
        with pytest.raises(ConfigurationError):
            OpenAICompatibleProvider("m", url="file:///etc/passwd")


class TestWhatItCallsItself:
    def test_the_name_carries_the_runtime_as_well_as_the_model(self) -> None:
        """The same model served by vLLM and by Ollama is not the same thing.

        This string is recorded with an answer, so a reader can tell what
        produced the claims that were checked.
        """
        assert OpenAICompatibleProvider("qwen2.5:7b", label="vllm").name == "vllm/qwen2.5:7b"

    def test_a_model_must_be_named(self) -> None:
        """A server serves what it was started with and 404s anything else."""
        with pytest.raises(ConfigurationError) as raised:
            OpenAICompatibleProvider("  ")
        assert "named model" in str(raised.value)


class TestTheCommandLineChoosesTheRightPort:
    def test_openai_does_not_inherit_ollamas_url(self) -> None:
        parsed = build_parser().parse_args(["ask", "q", "--server", "openai", "--model", "m"])
        assert parsed.url is None, "a concrete default here would be one server's port for both"

        from tsumugi.interfaces.cli.main import _provider

        assert _provider(parsed).endpoint.url == DEFAULT_URL
        assert ":8000" in DEFAULT_URL

    def test_ollama_stays_the_default(self) -> None:
        parsed = build_parser().parse_args(["ask", "q"])
        assert parsed.server == "ollama"

        from tsumugi.interfaces.cli.main import _provider

        assert ":11434" in _provider(parsed).endpoint.url

    def test_an_explicit_url_wins_for_either_server(self) -> None:
        from tsumugi.interfaces.cli.main import _provider

        parsed = build_parser().parse_args(
            ["ask", "q", "--server", "openai", "--model", "m", "--url", "http://127.0.0.1:1234/v1"]
        )
        assert _provider(parsed).endpoint.url == "http://127.0.0.1:1234/v1"


class TestABodyThatIsNotWhatWeExpected:
    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("a string", "not an object"),
            ({}, "no choices"),
            ({"choices": []}, "no choices"),
            ({"choices": [{"message": {"content": ""}}]}, "no text"),
            ({"choices": [{"message": {"content": "   "}}]}, "no text"),
            ({"choices": [{"message": {}}]}, "no text"),
            ({"choices": [{"message": {"content": 7}}]}, "no text"),
        ],
    )
    def test_it_raises_where_it_happened(self, body: object, expected: str) -> None:
        with pytest.raises(ProviderError) as raised:
            _text_of(body, "test/model")
        assert expected in str(raised.value)

    def test_a_well_formed_body_comes_back_whole(self) -> None:
        """The positive control. Without it every row above could pass on a
        function that raised unconditionally."""
        body = {"choices": [{"message": {"content": "the tent weighs 2.4kg"}}]}
        assert _text_of(body, "test/model") == "the tent weighs 2.4kg"
