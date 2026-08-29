"""The one step that leaves this machine, and the order around it.

Two halves. The first is the provider's refusal to send a person's corpus to a
host that is not theirs -- tested against a real loopback server rather than a
patched ``urlopen``, because the check that matters is the one on the boundary
and a mock cannot get that wrong on your behalf.

The second is ``ask()``, which composes five things that already worked. The
tests are about the *order*: protect the rendered text and never the package,
restore before you verify. Both were bugs once (ADR-0009) and both are silent.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from tsumugi.application.ask import ask
from tsumugi.application.ingest import ingest_paths
from tsumugi.domain.budget import Budget
from tsumugi.domain.claim import Support
from tsumugi.domain.package import ContextPackage, Protection
from tsumugi.errors import ConfigurationError
from tsumugi.infrastructure.adapters.ollama import (
    DEFAULT_MODEL,
    DEFAULT_URL,
    OllamaProvider,
    ProviderError,
)
from tsumugi.infrastructure.cost.heuristic import CharacterCost
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.database import connect
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore
from tsumugi.ports.llm import Endpoint, LLMProvider

NOTES = {
    "budget.md": "# 予算\n\n予算の単位は呼び出し側で明示する。トークンは推定である。\n",
    "other.md": "# 買い物\n\n牛乳とパン。\n",
}
QUESTION = "予算の単位は"
QUOTATION = "予算の単位は呼び出し側で明示する"


# --------------------------------------------------------------------------
# A model that says what the test needs it to say.
# --------------------------------------------------------------------------
@dataclass
class FakeProvider:
    """Satisfies :class:`LLMProvider` and records what it was handed."""

    answer: str = json.dumps(
        {"claims": [{"text": "呼び出し側が決める。", "citations": [QUOTATION]}]},
        ensure_ascii=False,
    )
    prompts: list[str] = field(default_factory=list)
    fail: bool = False

    @property
    def name(self) -> str:
        return "fake/1"

    @property
    def endpoint(self) -> Endpoint:
        return Endpoint(url="memory://fake", is_local=True)

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.fail:
            raise ProviderError("no")
        return self.answer


@dataclass
class ShoutingRedactor:
    """Reversible, and loud enough to see in a prompt."""

    protected: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "shouting/1"

    @property
    def scope(self) -> str:
        return "session-1"

    def protect(self, text: str) -> str:
        self.protected.append(text)
        return text.replace("呼び出し側", "[[WHO]]")

    def restore(self, text: str) -> str:
        self.restored.append(text)
        return text.replace("[[WHO]]", "呼び出し側")


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[SqliteDocumentStore, FtsIndex, sqlite3.Connection]:
    root = tmp_path / "notes"
    root.mkdir()
    for name, text in NOTES.items():
        with (root / name).open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    connection = connect(tmp_path / "index.db")
    store, index = SqliteDocumentStore(connection), FtsIndex(connection)
    ingest_paths(
        sorted(root.rglob("*.md")), root=root, store=store, index=index, parser_for=parser_for
    )
    return store, index, connection


def _ask(corpus: Any, **kwargs: Any) -> Any:
    store, index, _ = corpus
    defaults: dict[str, Any] = {
        "store": store,
        "index": index,
        "cost_model": CharacterCost(),
        "budget": Budget.characters(4000),
        "provider": FakeProvider(),
    }
    return ask(QUESTION, **{**defaults, **kwargs})


class TestTheLoop:
    def test_it_builds_sends_and_checks(self, corpus: Any) -> None:
        asked = _ask(corpus)
        assert asked.package.items
        assert asked.verification.claims
        assert asked.trustworthy

    def test_the_prompt_is_the_package_plus_an_output_contract(self, corpus: Any) -> None:
        provider = FakeProvider()
        asked = _ask(corpus, provider=provider)
        sent = provider.prompts[0]
        assert asked.package.render() in sent
        # Without this the model answers in prose and there is nothing to
        # verify, which reports as zero claims -- and zero claims reads clean.
        assert "claims" in sent and "character for" in sent

    def test_it_says_what_a_citation_is_not(self, corpus: Any) -> None:
        # Earned. qwen2.5:14b answered a Japanese question correctly and cited
        # `notes/持ち物リスト.md (持ち物リスト（控え）)` -- the header line above
        # the passage, which is what "citation" means everywhere else. Every
        # claim reported unsupported and the answer was right.
        provider = FakeProvider()
        _ask(corpus, provider=provider)
        sent = provider.prompts[0]
        assert "not a filename" in sent
        assert "never cite this line" in sent

    def test_it_does_not_ask_the_model_for_offsets(self, corpus: Any) -> None:
        # A model asked for character positions returns positions that are
        # plausible and wrong. It quotes; tsumugi resolves (ADR-0005).
        provider = FakeProvider()
        _ask(corpus, provider=provider)
        assert "Do not report character positions" in provider.prompts[0]

    def test_an_unsupported_claim_is_reported_not_raised(self, corpus: Any) -> None:
        invented = json.dumps(
            {"claims": [{"text": "単位はバイトである。", "citations": ["単位はバイトである"]}]},
            ensure_ascii=False,
        )
        asked = _ask(corpus, provider=FakeProvider(answer=invented))
        # The whole point of the arrangement: a model that makes things up
        # produces a report, not an exception and not a silent pass.
        assert not asked.trustworthy
        assert asked.verification.claims[0].support is Support.UNSUPPORTED

    def test_the_answer_reads_as_prose(self, corpus: Any) -> None:
        assert _ask(corpus).answer_text() == "呼び出し側が決める。"

    def test_it_records_which_model_answered(self, corpus: Any) -> None:
        # A verification report is only as meaningful as the knowledge of what
        # produced the claims it checked.
        assert _ask(corpus).provider == "fake/1"

    def test_a_provider_that_fails_fails_the_call(self, corpus: Any) -> None:
        # Not caught and turned into an empty answer. An empty answer verifies
        # as zero claims, and zero claims reports clean.
        with pytest.raises(ProviderError):
            _ask(corpus, provider=FakeProvider(fail=True))


class TestTheOrderOfProtection:
    def test_the_package_is_never_redacted(self, corpus: Any) -> None:
        redactor = ShoutingRedactor()
        asked = _ask(corpus, redactor=redactor)
        # Items still carry the real text, because their hashes were taken
        # over it and a package whose items do not match its hashes cannot be
        # built at all.
        assert any(QUOTATION in item.text for item in asked.package.items)
        assert "[[WHO]]" not in asked.package.render()

    def test_the_prompt_is_redacted(self, corpus: Any) -> None:
        redactor = ShoutingRedactor()
        asked = _ask(corpus, redactor=redactor)
        assert "[[WHO]]" in asked.prompt
        assert "呼び出し側" not in asked.prompt

    def test_the_package_records_who_protected_it(self, corpus: Any) -> None:
        asked = _ask(corpus, redactor=ShoutingRedactor())
        protection = asked.package.provenance.protection
        assert isinstance(protection, Protection)
        assert protection.by == "shouting/1" and protection.scope == "session-1"

    def test_it_restores_before_it_verifies(self, corpus: Any) -> None:
        # ADR-0009. The model quotes the placeholder it was given; without a
        # restore first, every honest citation reports as unsupported.
        redactor = ShoutingRedactor()
        quoted = json.dumps(
            {
                "claims": [
                    {"text": "[[WHO]]が決める。", "citations": ["予算の単位は[[WHO]]で明示する"]}
                ]
            },
            ensure_ascii=False,
        )
        asked = _ask(corpus, redactor=redactor, provider=FakeProvider(answer=quoted))
        assert redactor.restored, "verification never asked for the real values back"
        assert asked.verification.claims[0].support is Support.SUPPORTED


class TestTheLedger:
    def test_it_opens_and_closes_one_entry(self, corpus: Any) -> None:
        from tsumugi.infrastructure.storage.ledger import SqliteLedger

        _, _, connection = corpus
        ledger = SqliteLedger(connection)
        _ask(corpus, ledger=ledger)
        usage = ledger.usage()
        # Closed, not just opened. An entry that is never closed is a package
        # whose fate is unknown, and the ledger's whole question is what
        # happened to what was sent.
        assert usage.packages == 1 and usage.closed == 1


class TestTheProviderIsAProvider:
    def test_the_fake_satisfies_the_port(self) -> None:
        assert isinstance(FakeProvider(), LLMProvider)

    def test_the_real_one_does_too(self) -> None:
        assert isinstance(OllamaProvider(), LLMProvider)


# --------------------------------------------------------------------------
# The boundary. A real socket, on loopback, because the rule is about hosts.
# --------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    body: ClassVar[dict[str, Any]] = {"response": "ok"}
    status: ClassVar[int] = 200
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _Handler.seen.append(json.loads(self.rfile.read(length).decode("utf-8")))
        payload = json.dumps(_Handler.body).encode("utf-8")
        self.send_response(_Handler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        """Quiet. The test output is not a web server log."""


@pytest.fixture
def ollama() -> Any:
    _Handler.seen = []
    _Handler.body = {"response": "ok"}
    _Handler.status = 200
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


class TestTheOllamaAdapter:
    def test_it_asks_and_returns_what_came_back(self, ollama: str) -> None:
        assert OllamaProvider(url=ollama).generate("hello") == "ok"
        assert _Handler.seen[0]["prompt"] == "hello"

    def test_it_asks_for_determinism(self, ollama: str) -> None:
        # A package is reproducible by construction (ADR-0003). The answer can
        # only be asked to be, but asking costs nothing.
        OllamaProvider(url=ollama).generate("hello")
        options = _Handler.seen[0]["options"]
        assert options["temperature"] == 0.0 and options["seed"] == 0

    def test_it_does_not_stream(self, ollama: str) -> None:
        OllamaProvider(url=ollama).generate("hello")
        assert _Handler.seen[0]["stream"] is False

    def test_an_error_status_says_what_to_do_about_it(self, ollama: str) -> None:
        _Handler.status = 404
        with pytest.raises(ProviderError, match="ollama pull"):
            OllamaProvider(url=ollama).generate("hello")

    def test_an_empty_answer_raises(self, ollama: str) -> None:
        # Rather than returning "". Zero claims verify clean, so a silent
        # empty answer would look like a model that got everything right.
        _Handler.body = {"response": "   "}
        with pytest.raises(ProviderError):
            OllamaProvider(url=ollama).generate("hello")

    def test_nothing_listening_says_so(self) -> None:
        with pytest.raises(ProviderError, match="ollama running"):
            # Port 1 on loopback: reserved, and nothing sane is bound to it.
            OllamaProvider(url="http://127.0.0.1:1", timeout=5.0).generate("hello")

    def test_the_name_carries_the_model(self) -> None:
        assert OllamaProvider(model="llama3.2:1b").name == "ollama/llama3.2:1b"


class TestTheBoundary:
    def test_the_default_is_this_machine(self) -> None:
        assert OllamaProvider().endpoint.is_local
        assert DEFAULT_URL.startswith("http://127.0.0.1")

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com:11434",
            "http://192.168.1.50:11434",
            "https://api.example.com",
            # The one that catches people: a hostname that reads local and is
            # not. The check is on the host, not on the spelling.
            "http://localhost.example.com:11434",
        ],
    )
    def test_it_refuses_a_host_that_is_not_this_machine(self, url: str) -> None:
        with pytest.raises(ConfigurationError, match="somebody else"):
            OllamaProvider(url=url)

    @pytest.mark.parametrize(
        "url", ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"]
    )
    def test_it_allows_this_machine(self, url: str) -> None:
        assert OllamaProvider(url=url).endpoint.is_local

    def test_remote_is_possible_but_has_to_be_said(self, ollama: str) -> None:
        # Refusing outright would be a library deciding for its operator. It
        # is a default, and defaults are what protect people who did not read
        # the documentation.
        provider = OllamaProvider(url="http://example.com:11434", allow_remote=True)
        assert not provider.endpoint.is_local
        assert "REMOTE" in provider.endpoint.describe()

    def test_a_url_that_is_not_http_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="http"):
            OllamaProvider(url="file:///etc/passwd")


class TestTheModelDecidesNothing:
    def test_the_package_is_the_same_with_or_without_a_provider(self, corpus: Any) -> None:
        # The selection is deterministic and the model is downstream of it.
        # If a provider could change what was selected, every guarantee about
        # what was sent would be a guarantee about what a model felt like.
        from tsumugi.application.build_context import build_context

        store, index, _ = corpus
        built = build_context(
            QUESTION,
            store=store,
            index=index,
            cost_model=CharacterCost(),
            budget=Budget.characters(4000),
        )
        asked = _ask(corpus)
        assert asked.package.package_id == built.package_id

    def test_a_package_is_still_usable_without_one(self, corpus: Any) -> None:
        # The whole library works with no model and no network. `ask` is one
        # command out of ten, and this is the sentence the threat model makes.
        from tsumugi.application.build_context import build_context

        store, index, _ = corpus
        built = build_context(
            QUESTION,
            store=store,
            index=index,
            cost_model=CharacterCost(),
            budget=Budget.characters(4000),
        )
        assert isinstance(built, ContextPackage)
        assert built.render()


def test_the_default_model_is_multilingual() -> None:
    # This library is measured on a Japanese corpus (ADR-0007). An
    # English-first default would fail its own evaluation suite.
    assert "qwen" in DEFAULT_MODEL


def test_replacing_provenance_leaves_the_package_valid(corpus: Any) -> None:
    # `ask` rewrites provenance to record the protection. If that could
    # invalidate a package the failure would appear at verification time,
    # which is far away from the cause.
    asked = _ask(corpus, redactor=ShoutingRedactor())
    round_tripped = ContextPackage.from_json(asked.package.to_json())
    assert round_tripped.package_id == asked.package.package_id
    assert replace(asked.package).provenance.protection is not None
