"""The agent-facing surface.

Two things this file is really about. The server is **read-only** -- nothing
that writes to the corpus or the index is reachable, and that is the rule that
bounds the damage rather than trying to prevent every case (ADR-0012). And the
transport survives bad input: one malformed message must not end a session.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from tsumugi.config import TsumugiConfig
from tsumugi.interfaces.cli.main import main
from tsumugi.interfaces.mcp.protocol import Request, RpcError, read_requests, write_message
from tsumugi.interfaces.mcp.server import TOOLS, McpServer, serve


def drive(messages: list[dict[str, Any]], index: Path) -> list[dict[str, Any]]:
    """Run one session over a list of requests and return the responses."""
    stdin = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    stdout = io.StringIO()
    serve(TsumugiConfig(index_path=index), stdin=stdin, stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def call(name: str, arguments: dict[str, Any], identifier: int = 2) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


@pytest.fixture
def index(corpus: Path, tmp_path: Path) -> Path:
    path = tmp_path / "mcp-index.db"
    main(["--index", str(path), "ingest", str(corpus)])
    return path


def body(response: dict[str, Any]) -> Any:
    return json.loads(response["result"]["content"][0]["text"])


class TestTheTransport:
    def test_a_well_formed_request_parses(self) -> None:
        stream = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        (request,) = list(read_requests(stream))
        assert isinstance(request, Request)
        assert request.method == "ping"
        assert not request.is_notification

    def test_a_message_with_no_id_is_a_notification(self) -> None:
        stream = io.StringIO('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        (request,) = list(read_requests(stream))
        assert isinstance(request, Request)
        assert request.is_notification

    def test_a_malformed_line_yields_an_error_rather_than_raising(self) -> None:
        # One bad message must not end a session.
        stream = io.StringIO("not json\n" + '{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        results = list(read_requests(stream))
        assert isinstance(results[0], RpcError)
        assert isinstance(results[1], Request)

    @pytest.mark.parametrize(
        "line",
        ['"a string"', "[1,2,3]", '{"id":1}', '{"method":123}', '{"method":"x","params":[]}'],
    )
    def test_structurally_wrong_messages_are_refused(self, line: str) -> None:
        (result,) = list(read_requests(io.StringIO(line + "\n")))
        assert isinstance(result, RpcError)

    def test_blank_lines_are_skipped(self) -> None:
        stream = io.StringIO('\n\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n\n')
        assert len(list(read_requests(stream))) == 1

    def test_a_message_is_one_line(self) -> None:
        stdout = io.StringIO()
        write_message({"jsonrpc": "2.0", "id": 1, "result": {"a": "多行\nではない"}}, stdout)
        assert len(stdout.getvalue().splitlines()) == 1

    def test_parameters_are_type_checked(self) -> None:
        request = Request(method="x", params={"limit": "ten"})
        with pytest.raises(RpcError, match="whole number"):
            request.integer("limit", 5)
        with pytest.raises(RpcError, match="must be a string"):
            Request(method="x", params={"query": 7}).string("query")


class TestTheHandshake:
    def test_initialize_names_the_server(self, index: Path) -> None:
        (response,) = drive(
            [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}], index
        )
        assert response["result"]["serverInfo"]["name"] == "tsumugi"

    def test_it_echoes_the_protocol_version_the_client_asked_for(self, index: Path) -> None:
        # The handshake is a negotiation; refusing an unknown string would
        # break against every future client.
        (response,) = drive(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2099-01-01"},
                }
            ],
            index,
        )
        assert response["result"]["protocolVersion"] == "2099-01-01"

    def test_a_notification_gets_no_response(self, index: Path) -> None:
        assert drive([{"jsonrpc": "2.0", "method": "notifications/initialized"}], index) == []

    def test_an_unknown_method_is_refused(self, index: Path) -> None:
        (response,) = drive([{"jsonrpc": "2.0", "id": 1, "method": "corpus/delete"}], index)
        assert response["error"]["code"] == -32601

    def test_a_bad_line_is_reported_and_the_session_continues(self, index: Path) -> None:
        stdin = io.StringIO('nonsense\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
        stdout = io.StringIO()
        serve(TsumugiConfig(index_path=index), stdin=stdin, stdout=stdout)

        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert responses[0]["error"]["code"] == -32700
        assert responses[1]["result"] == {}


class TestTheToolsAreReadOnly:
    """The constraint that makes this safe inside somebody else's agent loop."""

    def test_exactly_four_tools_are_offered(self, index: Path) -> None:
        (response,) = drive([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}], index)
        assert {tool["name"] for tool in response["result"]["tools"]} == {
            "search",
            "context",
            "trace",
            "verify",
        }

    @pytest.mark.parametrize("name", ["ingest", "forget", "doctor", "delete", "write"])
    def test_no_tool_that_writes_is_reachable(self, name: str, index: Path) -> None:
        (response,) = drive([call(name, {}, identifier=1)], index)
        assert response["error"]["code"] == -32602
        # Named explicitly, so an agent reaching for a write tool is told this
        # server does not have one rather than getting a generic failure.
        assert "read-only" in response["error"]["message"]

    def test_the_declared_tools_and_the_dispatch_table_agree(self) -> None:
        # A tool advertised and not implemented is worse than one that is
        # missing: an agent will call it.
        server = McpServer(TsumugiConfig())
        for tool in TOOLS:
            assert hasattr(server, f"_{tool['name']}")

    def test_every_tool_declares_a_schema_with_its_required_fields(self) -> None:
        for tool in TOOLS:
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert schema["required"]
            for field in schema["required"]:
                assert field in schema["properties"]


class TestSearch:
    def test_it_returns_anchored_spans(self, index: Path) -> None:
        (response,) = drive([call("search", {"query": "東京"}, identifier=1)], index)
        result = body(response)["results"][0]
        assert result["source_path"] == "notes/mountain.md"
        assert result["end"] > result["start"]

    def test_a_missing_query_is_an_invalid_parameter(self, index: Path) -> None:
        (response,) = drive([call("search", {}, identifier=1)], index)
        assert response["error"]["code"] == -32602


class TestContext:
    def test_it_returns_the_whole_package(self, index: Path) -> None:
        (response,) = drive(
            [call("context", {"query": "テント", "budget": "characters:2000"}, identifier=1)],
            index,
        )
        package = body(response)
        assert package["contract"].startswith("tsumugi.context-package/")
        assert package["package_id"].startswith("sha256:")

    def test_omissions_travel_with_it(self, corpus: Path, tmp_path: Path) -> None:
        # An agent that cannot see the edge of a selection has the same problem
        # as a person who cannot.
        for n in range(6):
            (corpus / "notes" / f"gear-{n}.md").write_text(
                f"# 装備 {n}\n\nテントの候補 {n} を比較する。重量と設営のしやすさ。\n",
                encoding="utf-8",
            )
        path = tmp_path / "crowded.db"
        main(["--index", str(path), "ingest", str(corpus)])

        (response,) = drive(
            [call("context", {"query": "テント", "budget": "characters:60"}, identifier=1)],
            path,
        )
        package = body(response)
        assert package["omissions"]
        assert any(o["rule"] == "budget_exhausted" for o in package["omissions"])

    def test_a_budget_without_a_unit_is_a_tool_error_not_a_crash(self, index: Path) -> None:
        (response,) = drive(
            [call("context", {"query": "テント", "budget": "2000"}, identifier=1)], index
        )
        assert response["result"]["isError"] is True
        assert "unit" in response["result"]["content"][0]["text"]

    def test_the_same_question_twice_gives_the_same_package_id(self, index: Path) -> None:
        ids = [
            body(drive([call("context", {"query": "テント"}, identifier=1)], index)[0])[
                "package_id"
            ]
            for _ in range(2)
        ]
        assert ids[0] == ids[1]


class TestTrace:
    def test_a_present_quotation_is_located(self, index: Path) -> None:
        (response,) = drive([call("trace", {"quotation": "テントは 2.4kg"}, identifier=1)], index)
        found = body(response)
        assert found["found"] == 1
        assert found["occurrences"][0]["source_path"] == "notes/mountain.md"

    def test_an_absent_quotation_says_it_is_not_nearly_there(self, index: Path) -> None:
        (response,) = drive([call("trace", {"quotation": "テントは 3.9kg"}, identifier=1)], index)
        found = body(response)
        assert found["found"] == 0
        assert "not that it is nearly there" in found["note"]


class TestTheLoopThroughMcp:
    """context -> answer -> verify, entirely over the protocol."""

    def test_a_package_from_context_can_be_verified_by_verify(self, index: Path) -> None:
        # The evidence that the contract is a document rather than a type: it
        # goes out as JSON, comes back as JSON, and resolves.
        (built,) = drive(
            [call("context", {"query": "テント", "budget": "characters:2000"}, identifier=1)],
            index,
        )
        package_text = built["result"]["content"][0]["text"]
        package = json.loads(package_text)

        answer = json.dumps(
            {
                "claims": [
                    {"text": "real", "citations": [package["items"][0]["text"].strip()[:10]]},
                    {"text": "invented", "citations": ["この文はどこにも存在しない"]},
                    {"text": "bare", "citations": []},
                ]
            },
            ensure_ascii=False,
        )

        (checked,) = drive(
            [call("verify", {"answer": answer, "package": package_text}, identifier=1)], index
        )
        report = body(checked)
        assert report["counts"] == {
            "supported": 1,
            "unsupported": 1,
            "uncited": 1,
            "unverifiable": 0,
        }

    def test_verify_always_says_that_supported_is_not_true(self, index: Path) -> None:
        (built,) = drive([call("context", {"query": "テント"}, identifier=1)], index)
        package_text = built["result"]["content"][0]["text"]
        answer = json.dumps({"claims": [{"text": "a claim", "citations": []}]})

        (checked,) = drive(
            [call("verify", {"answer": answer, "package": package_text}, identifier=1)], index
        )
        assert "does not mean the claim is true" in body(checked)["note"]

    def test_an_altered_package_is_refused(self, index: Path) -> None:
        (built,) = drive([call("context", {"query": "テント"}, identifier=1)], index)
        package = json.loads(built["result"]["content"][0]["text"])
        package["query"] = "a different question"

        (checked,) = drive(
            [
                call(
                    "verify",
                    {"answer": '{"claims":[]}', "package": json.dumps(package)},
                    identifier=1,
                )
            ],
            index,
        )
        assert checked["result"]["isError"] is True
        assert "altered" in checked["result"]["content"][0]["text"]


class TestFailingSafely:
    def test_a_missing_index_is_a_tool_error_not_a_dead_server(self, tmp_path: Path) -> None:
        # The server starts; the failure arrives as a result the agent can act
        # on, naming what to do about it.
        (response,) = drive([call("search", {"query": "x"}, identifier=1)], tmp_path / "none.db")
        assert response["result"]["isError"] is True
        assert "ingest" in response["result"]["content"][0]["text"]

    def test_arguments_that_are_not_an_object_are_refused(self, index: Path) -> None:
        (response,) = drive(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "search", "arguments": ["query"]},
                }
            ],
            index,
        )
        assert response["error"]["code"] == -32602

    def test_nothing_but_responses_reaches_stdout(self, index: Path) -> None:
        # A stray line corrupts the stream and the client sees a parse error it
        # cannot attribute.
        stdin = io.StringIO(
            "\n".join(
                json.dumps(m)
                for m in [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    call("search", {"query": "東京"}, identifier=2),
                    call("nope", {}, identifier=3),
                ]
            )
            + "\n"
        )
        stdout = io.StringIO()
        serve(TsumugiConfig(index_path=index), stdin=stdin, stdout=stdout)

        for line in stdout.getvalue().splitlines():
            message = json.loads(line)
            assert message["jsonrpc"] == "2.0"
            assert "result" in message or "error" in message
