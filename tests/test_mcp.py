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
from tsumugi.interfaces.mcp.server import SEARCH_HITS_CONTRACT, TOOLS, McpServer, serve


def drive(
    messages: list[dict[str, Any]], index: Path, config: TsumugiConfig | None = None
) -> list[dict[str, Any]]:
    """Run one session over a list of requests and return the responses."""
    stdin = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    stdout = io.StringIO()
    serve(config or TsumugiConfig(index_path=index), stdin=stdin, stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def text_of(response: dict[str, Any]) -> str:
    """The tool result's text -- the JSON string a caller would hold."""
    return str(response["result"]["content"][0]["text"])


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
        # `resultType` and `_meta` ride on every result in 2026-07-28; the
        # answer to a ping is still nothing.
        assert responses[1]["result"]["resultType"] == "complete"


class TestTheToolsAreReadOnly:
    """The constraint that makes this safe inside somebody else's agent loop."""

    def test_exactly_five_tools_are_offered(self, index: Path) -> None:
        (response,) = drive([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}], index)
        assert {tool["name"] for tool in response["result"]["tools"]} == {
            "search",
            "context",
            "render",
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
    def test_it_returns_anchored_spans_under_a_named_shape(self, index: Path) -> None:
        (response,) = drive([call("search", {"query": "東京"}, identifier=1)], index)
        payload = body(response)
        assert payload["contract"] == SEARCH_HITS_CONTRACT
        assert payload["index"] == "default"
        hit = payload["hits"][0]
        assert hit["anchor"]["source_path"] == "notes/mountain.md"
        assert hit["anchor"]["end"] > hit["anchor"]["start"]

    def test_a_hit_anchor_has_exactly_the_keys_a_package_anchor_has(self, index: Path) -> None:
        """The promise the -draft shape makes: a consumer's anchor code works on both.

        Compared against a real package's item anchor from `to_json()`, not a
        list written here, so the two cannot drift apart unnoticed.
        """
        search_response, context_response = drive(
            [
                call("search", {"query": "テント"}, identifier=1),
                call("context", {"query": "テント", "budget": "characters:2000"}, identifier=2),
            ],
            index,
        )
        hit_anchor = body(search_response)["hits"][0]["anchor"]
        item_anchor = body(context_response)["items"][0]["anchor"]
        assert sorted(hit_anchor) == sorted(item_anchor), "the same seven keys"
        assert all(hit_anchor[key] is not None for key in hit_anchor)

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
        # Genuinely distinct: near-identical documents are marked
        # redundant_candidate instead, which is not what this test is about.
        notes = [
            "テントの設営は風上から。ペグは45度に打ち込み、張り綱を先に固定する。",
            "テントの前室には炊事道具をまとめる。結露を避けるため換気口は常に開ける。",
            "テントのポールは継ぎ目を確認してから伸ばす。砂が入ると曲がりやすい。",
            "テントの底面には薄い敷物を追加した。冷えと摩耗の両方に効いている。",
            "テントの色は視認性より落ち着きを優先した。写真映りは二の次でよい。",
            "テントの収納は畳まず押し込む方式に変えた。生地の折り目が減った。",
        ]
        for n, note in enumerate(notes):
            (corpus / "notes" / f"gear-{n}.md").write_text(
                f"# 記録 {n}\n\n{note}\n", encoding="utf-8"
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


class TestOneReportShape:
    """The MCP `verify` tool emits the report's own serialisation.

    It used to build its own dict, and it had already drifted: no
    `package_id`, and no `unverifiable_because` -- so an agent was told a
    claim was unverifiable and not why, which is exactly the distinction
    ADR-0009 exists to preserve. Three hand-written copies of one shape is
    three chances to disagree about whether an answer was checked.
    """

    def test_it_carries_the_reason_a_claim_could_not_be_checked(self) -> None:
        from tsumugi.domain.claim import Claim, VerificationReport

        report = VerificationReport.of(
            [Claim(text="a claim", unverifiable_because="no restorer for scope-1")]
        )
        document = report.to_dict()
        assert document["claims"][0]["unverifiable_because"] == "no restorer for scope-1"

    def test_a_citation_states_whether_it_resolved(self) -> None:
        # Derivable from `locations`, and stated anyway: a consumer deciding
        # whether to trust a sentence should not infer it from a list length.
        from tsumugi.domain.claim import Citation, Claim, VerificationReport

        report = VerificationReport.of([Claim(text="c", citations=(Citation("nope"),))])
        assert report.to_dict()["claims"][0]["citations"][0]["resolved"] is False

    def test_the_mcp_handler_does_not_rebuild_the_shape(self) -> None:
        import inspect

        from tsumugi.interfaces.mcp import server

        source = inspect.getsource(server.McpServer._verify)
        assert "report.to_dict()" in source
        assert '"support": claim.support.value' not in source


class TestBothProtocolEras:
    """MCP 2026-07-28 retired the handshake. Most clients have not.

    The specification expects an implementation to detect the counterpart's
    era and fall back. For a server that means answering a stateless client
    that opens with `tools/call`, and an older one that opens with
    `initialize` -- and telling each the version it asked for, because
    reporting a revision a client does not implement is worse than reporting
    none.
    """

    def test_a_stateless_client_needs_no_handshake(self, index: Path) -> None:
        # No initialize, no notifications/initialized. Straight to work.
        (response,) = drive(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "search",
                        "arguments": {"query": "テント"},
                        "_meta": {
                            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                            "io.modelcontextprotocol/clientCapabilities": {},
                        },
                    },
                }
            ],
            index,
        )
        assert "error" not in response
        assert response["result"]["resultType"] == "complete"

    def test_an_older_client_still_gets_its_handshake(self, index: Path) -> None:
        (response,) = drive(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                }
            ],
            index,
        )
        # The version it asked for. Answering 2026-07-28 would be telling a
        # client to speak a protocol it does not have.
        assert response["result"]["protocolVersion"] == "2025-06-18"

    def test_discover_answers_the_same_facts_as_initialize(self, index: Path) -> None:
        first, second = drive(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "server/discover", "params": {}},
            ],
            index,
        )
        assert first["result"]["serverInfo"] == second["result"]["serverInfo"]
        assert first["result"]["instructions"] == second["result"]["instructions"]

    def test_a_result_names_the_server_without_a_session(self, index: Path) -> None:
        # There is no handshake left to carry it, and a stateless client should
        # not have to remember what it was talking to.
        (response,) = drive([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], index)
        info = response["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]
        assert info["name"] == "tsumugi"

    def test_a_list_says_how_long_it_keeps(self, index: Path) -> None:
        # Four tools compiled into the module; they cannot change while the
        # process runs, so a client re-listing on every turn is wasting a
        # round trip.
        (response,) = drive([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}], index)
        assert response["result"]["ttlMs"] > 0
        assert response["result"]["cacheScope"] == "server"

    def test_the_version_it_reports_is_the_current_revision(self) -> None:
        from tsumugi.interfaces.mcp.server import PROTOCOL_VERSION

        assert PROTOCOL_VERSION == "2026-07-28"


class TestRender:
    """The fifth tool: the exact prompt, touching nothing."""

    def test_it_renders_the_package_context_returned(self, index: Path) -> None:
        (context_response,) = drive(
            [call("context", {"query": "テント", "budget": "characters:2000"}, identifier=1)],
            index,
        )
        package_text = text_of(context_response)
        (rendered,) = drive([call("render", {"package": package_text}, identifier=2)], index)
        payload = body(rendered)
        assert payload["package_id"] == json.loads(package_text)["package_id"]
        assert payload["contract"].startswith("tsumugi.context-package/")
        assert "# TASK" in payload["rendered"]
        assert "テント" in payload["rendered"]

    def test_it_needs_no_index(self, tmp_path: Path, index: Path) -> None:
        """A package from anywhere renders here; `render` never opens a store."""
        (context_response,) = drive(
            [call("context", {"query": "テント", "budget": "characters:2000"}, identifier=1)],
            index,
        )
        (rendered,) = drive(
            [call("render", {"package": text_of(context_response)}, identifier=2)],
            tmp_path / "does-not-exist.db",
        )
        assert "# TASK" in body(rendered)["rendered"]

    def test_a_malformed_package_is_a_tool_error_naming_its_kind(self, index: Path) -> None:
        (response,) = drive([call("render", {"package": "{not json"}, identifier=1)], index)
        assert response["result"]["isError"] is True
        kind = text_of(response).split(":")[0]
        assert kind.endswith("Error"), kind


class TestContextArguments:
    """What sora asked for on `context`, and that each argument is connected."""

    def test_answering_instructions_carry_the_schema_and_change_the_id(self, index: Path) -> None:
        """Different prompts, different ids: an id that called them the same
        would be the thing that is wrong."""
        person, machine = drive(
            [
                call("context", {"query": "テント", "budget": "characters:2000"}, identifier=1),
                call(
                    "context",
                    {"query": "テント", "budget": "characters:2000", "instructions": "answering"},
                    identifier=2,
                ),
            ],
            index,
        )
        assert body(person)["output_schema"] is None
        assert body(machine)["output_schema"] is not None
        assert body(person)["package_id"] != body(machine)["package_id"]

        (rendered,) = drive([call("render", {"package": text_of(machine)}, identifier=3)], index)
        assert "OUTPUT_SCHEMA" in body(rendered)["rendered"]

    def test_an_unknown_instruction_set_is_refused_and_the_choices_named(self, index: Path) -> None:
        (response,) = drive(
            [call("context", {"query": "テント", "instructions": "answerng"}, identifier=1)],
            index,
        )
        assert response["result"]["isError"] is True
        assert text_of(response).startswith("ConfigurationError:")
        assert "answering" in text_of(response)

    def test_ledger_false_records_nothing_and_the_default_records_one(self, index: Path) -> None:
        """Both halves, or the test proves nothing about the flag."""
        from tsumugi.infrastructure.storage.database import connect
        from tsumugi.infrastructure.storage.ledger import SqliteLedger

        def entries() -> int:
            connection = connect(index)
            try:
                return len(SqliteLedger(connection).entries())
            finally:
                connection.close()

        drive([call("context", {"query": "テント", "ledger": False}, identifier=1)], index)
        assert entries() == 0, "ledger: false still wrote an entry"
        drive([call("context", {"query": "テント"}, identifier=2)], index)
        assert entries() == 1, "the default must record, or the assertion above is vacuous"

    def test_a_string_where_a_boolean_was_needed_is_refused(self, index: Path) -> None:
        """`ledger: "false"` coerced would be *true*: the exact thing the caller
        was trying to avoid. Refused as a malformed request."""
        (response,) = drive(
            [call("context", {"query": "テント", "ledger": "false"}, identifier=1)], index
        )
        assert "error" in response and response["error"]["code"] == -32602


class TestNamedIndexes:
    """Two corpora, one process, addressed by name and never by path."""

    @pytest.fixture
    def two(self, corpus: Path, tmp_path: Path) -> TsumugiConfig:
        personal = tmp_path / "personal.db"
        main(["--index", str(personal), "ingest", str(corpus)])
        news_root = tmp_path / "news"
        news_root.mkdir()
        (news_root / "today.md").write_text(
            "# Today\n\nThe harbour reopened after the storm.\n", encoding="utf-8"
        )
        news = tmp_path / "news.db"
        main(["--index", str(news), "ingest", str(news_root)])
        return TsumugiConfig.from_mapping(
            {"index_path": personal, "indexes": {"personal": personal, "news": news}}
        )

    def test_each_name_reaches_its_own_corpus(self, two: TsumugiConfig, tmp_path: Path) -> None:
        news, personal = drive(
            [
                call("search", {"query": "harbour", "index": "news"}, identifier=1),
                call("search", {"query": "harbour", "index": "personal"}, identifier=2),
            ],
            tmp_path,
            config=two,
        )
        assert body(news)["index"] == "news"
        assert body(news)["hits"], "the news corpus must answer, or nothing is tested"
        assert body(personal)["hits"] == [], "the notes must not know about the harbour"

    def test_omitting_the_name_is_the_default_index(
        self, two: TsumugiConfig, tmp_path: Path
    ) -> None:
        (response,) = drive(
            [call("search", {"query": "テント"}, identifier=1)], tmp_path, config=two
        )
        assert body(response)["index"] == "default"
        assert body(response)["hits"]

    def test_an_unknown_name_is_a_tool_error_listing_the_known_ones(
        self, two: TsumugiConfig, tmp_path: Path
    ) -> None:
        (response,) = drive(
            [call("search", {"query": "テント", "index": "nws"}, identifier=1)],
            tmp_path,
            config=two,
        )
        assert response["result"]["isError"] is True
        assert text_of(response).startswith("ConfigurationError:")
        assert "news" in text_of(response) and "personal" in text_of(response)

    def test_a_path_is_not_accepted_as_a_name(self, two: TsumugiConfig, tmp_path: Path) -> None:
        """The whole point of names. A path that exists must still be refused."""
        (response,) = drive(
            [
                call(
                    "search", {"query": "harbour", "index": str(tmp_path / "news.db")}, identifier=1
                )
            ],
            tmp_path,
            config=two,
        )
        assert response["result"]["isError"] is True


class TestErrorKinds:
    """One word a caller can match, at the front of every tool error."""

    def test_a_missing_index_is_a_storage_error(self, tmp_path: Path) -> None:
        (response,) = drive(
            [call("search", {"query": "テント"}, identifier=1)], tmp_path / "missing.db"
        )
        assert response["result"]["isError"] is True
        assert text_of(response).startswith("StorageError:"), text_of(response)

    def test_a_bad_budget_names_its_kind(self, index: Path) -> None:
        (response,) = drive(
            [call("context", {"query": "テント", "budget": "4000"}, identifier=1)], index
        )
        assert response["result"]["isError"] is True
        assert text_of(response).split(":", 1)[0] in {"ValueError", "ConfigurationError"}
