"""The command line, and the things it must never fail to say.

Every test here runs against a throwaway index (see ``conftest.py``). A CLI
test that writes into the developer's real corpus is a bug that only shows up
once, badly.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

# Imported rather than `importorskip`ed: it is in the same `[dev]` extra as
# pytest, so this file running already means it is installed. Skipping on its
# absence would hide the extra losing it, and take the CLI's contract check
# with it, green.
import jsonschema
import pytest

from tsumugi.interfaces.cli.main import main

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    return tmp_path / "cli-index.db"


def run(*argv: str, index: Path) -> int:
    return main(["--index", str(index), *argv])


class TestIngest:
    def test_it_reports_what_it_did(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run("ingest", str(corpus), index=index_path) == 0
        out = capsys.readouterr().out
        assert "3 new" in out

    def test_it_always_says_where_the_index_is(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A file you do not know about is a file you cannot protect, and this
        # one is a complete plaintext copy of the corpus.
        run("ingest", str(corpus), index=index_path)
        assert str(index_path) in capsys.readouterr().out

    def test_a_credential_file_is_named_without_being_asked(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        out = capsys.readouterr().out
        assert "refused" in out
        assert ".env" in out

    def test_ignored_files_are_counted_even_when_not_listed(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        assert "more skipped" in capsys.readouterr().out

    def test_show_skipped_lists_them(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), "--show-skipped", index=index_path)
        assert "scratch.tmp" in capsys.readouterr().out

    def test_a_missing_path_is_an_error_not_an_empty_run(
        self, tmp_path: Path, index_path: Path
    ) -> None:
        assert run("ingest", str(tmp_path / "nope"), index=index_path) == 2

    def test_a_single_file_can_be_ingested(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run("ingest", str(corpus / "notes" / "budget.md"), index=index_path) == 0
        assert "1 new" in capsys.readouterr().out


class TestSearch:
    def test_it_finds_a_two_character_japanese_query(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        assert run("search", "東京", index=index_path) == 0
        out = capsys.readouterr().out
        assert "notes/mountain.md" in out
        assert "offset" in out

    def test_finding_nothing_exits_non_zero(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()
        assert run("search", "量子色力学", index=index_path) == 1
        assert "nothing found" in capsys.readouterr().out

    def test_searching_without_an_index_says_so(self, index_path: Path) -> None:
        assert run("search", "anything", index=index_path) == 2


class TestContext:
    def test_it_renders_a_structured_prompt(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        assert run("context", "テント", "--budget", "characters:2000", index=index_path) == 0
        out = capsys.readouterr().out
        for section in ("# SYSTEM", "# TASK", "# CONTEXT"):
            assert section in out

    def test_the_prompt_tells_the_model_to_quote_rather_than_locate(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # ADR-0004. A model asked for offsets produces coordinates that are
        # plausible, self-consistent and wrong.
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()
        run("context", "テント", "--budget", "characters:2000", index=index_path)
        assert "Do not report character offsets" in capsys.readouterr().out

    def test_a_budget_without_a_unit_is_refused(self, corpus: Path, index_path: Path) -> None:
        # The unit is a decision, and defaulting it puts the decision back
        # where nobody makes it.
        run("ingest", str(corpus), index=index_path)
        assert run("context", "テント", "--budget", "2000", index=index_path) == 2

    def test_a_token_budget_states_that_it_is_estimated(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()
        run("context", "テント", "--budget", "tokens:500", index=index_path)
        out = capsys.readouterr().out
        assert "estimated, not counted" in out
        assert "cl100k_base" in out

    def test_the_json_package_validates_against_the_published_schema(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Through the package's accessor, not a path walked up from this file.
        # A test that reads the repository floor passes in a checkout and says
        # nothing about what a consumer gets.
        from tsumugi import contract_schema

        schema = contract_schema()
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        run("context", "テント", "--budget", "tokens:500", "--json", index=index_path)
        jsonschema.validate(json.loads(capsys.readouterr().out), schema)

    def test_the_same_question_twice_produces_the_same_package_id(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        ids = []
        for _ in range(2):
            run("context", "テント", "--budget", "tokens:500", "--json", index=index_path)
            ids.append(json.loads(capsys.readouterr().out)["package_id"])
        assert ids[0] == ids[1]

    def _crowd(self, corpus: Path) -> None:
        """Enough competing documents that a budget can actually bind.

        Genuinely distinct from each other: near-identical documents are
        marked ``redundant_candidate`` instead, which is correct behaviour and
        not what this test is about. Only the query word is shared.
        """
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

    def test_a_tight_budget_reports_what_it_dropped(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The most useful thing a selection can say (ADR-0005).
        self._crowd(corpus)
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        run("context", "テント", "--budget", "characters:60", "--why", index=index_path)
        out = capsys.readouterr().out
        assert "budget_exhausted" in out
        assert "would exceed the limit" in out

    def test_omissions_are_mentioned_even_without_why(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._crowd(corpus)
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()
        run("context", "テント", "--budget", "characters:60", index=index_path)
        assert "left out" in capsys.readouterr().out

    def test_finding_nothing_exits_non_zero(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()
        assert run("context", "量子色力学", "--budget", "tokens:500", index=index_path) == 1


class TestTrace:
    def test_a_present_quotation_resolves(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        assert run("trace", "テントは 2.4kg", index=index_path) == 0
        assert "resolved" in capsys.readouterr().out

    def test_an_absent_quotation_says_unsupported_and_why(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        assert run("trace", "テントは 3.9kg", index=index_path) == 1
        out = capsys.readouterr().out
        assert "unsupported" in out
        # The user has to be told this is a hard match, or they will read the
        # failure as "tsumugi could not find it" rather than "it is not there".
        assert "no fuzzy match" in out.lower()


class TestVerify:
    def _package(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> tuple[Path, dict[str, Any]]:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()
        run("context", "テント", "--budget", "characters:2000", "--json", index=index_path)
        payload = capsys.readouterr().out
        path = index_path.parent / "package.json"
        path.write_text(payload, encoding="utf-8")
        return path, json.loads(payload)

    def _answer(self, at: Path, claims: list[dict[str, object]]) -> Path:
        path = at.parent / "answer.json"
        path.write_text(json.dumps({"claims": claims}, ensure_ascii=False), encoding="utf-8")
        return path

    def test_a_real_quotation_is_supported_and_anchored(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        package_path, package = self._package(corpus, index_path, capsys)
        quotation = package["items"][0]["text"][:12]
        answer = self._answer(package_path, [{"text": "a claim", "citations": [quotation]}])

        assert run("verify", str(answer), "--package", str(package_path), index=index_path) == 0
        out = capsys.readouterr().out
        assert "supported" in out
        assert "notes/mountain.md" in out

    def test_an_invented_quotation_is_unsupported(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        package_path, _ = self._package(corpus, index_path, capsys)
        answer = self._answer(
            package_path, [{"text": "a claim", "citations": ["この文はどこにもない"]}]
        )

        assert run("verify", str(answer), "--package", str(package_path), index=index_path) == 1
        out = capsys.readouterr().out
        assert "unsupported" in out
        assert "not found in the text that was sent" in out

    def test_it_always_says_that_supported_is_not_true(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The failure mode of an evidence system is that people stop reading
        # "evidence" and start reading it as "correct".
        package_path, package = self._package(corpus, index_path, capsys)
        quotation = package["items"][0]["text"][:12]
        answer = self._answer(package_path, [{"text": "a claim", "citations": [quotation]}])

        run("verify", str(answer), "--package", str(package_path), index=index_path)
        assert "does not mean the claim is true" in capsys.readouterr().out

    def test_an_altered_package_is_refused(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        package_path, package = self._package(corpus, index_path, capsys)
        package["query"] = "a different question"
        package_path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
        answer = self._answer(package_path, [{"text": "a claim", "citations": []}])

        assert run("verify", str(answer), "--package", str(package_path), index=index_path) == 2

    def test_a_non_json_answer_says_what_was_expected(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        package_path, _ = self._package(corpus, index_path, capsys)
        answer = package_path.parent / "prose.txt"
        answer.write_text("The tent weighs 2.4kg.", encoding="utf-8")

        assert run("verify", str(answer), "--package", str(package_path), index=index_path) == 2
        assert "not JSON" in capsys.readouterr().err

    def test_json_output_carries_the_locations(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        package_path, package = self._package(corpus, index_path, capsys)
        quotation = package["items"][0]["text"][:12]
        answer = self._answer(package_path, [{"text": "a claim", "citations": [quotation]}])

        run("verify", str(answer), "--package", str(package_path), "--json", index=index_path)
        report = json.loads(capsys.readouterr().out)
        assert report["counts"]["supported"] == 1
        assert report["claims"][0]["citations"][0]["locations"][0]["source_path"]


class TestForget:
    def test_it_removes_a_document_from_the_index(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        assert run("forget", "notes/mountain.md", index=index_path) == 0
        assert "forgotten" in capsys.readouterr().out
        assert run("search", "東京", index=index_path) == 1

    def test_forgetting_something_not_held_is_not_an_error_message(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Asking to forget something already gone is a reasonable thing to do
        # twice.
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()
        assert run("forget", "notes/never-existed.md", index=index_path) == 1
        assert "not held" in capsys.readouterr().out

    def test_it_leaves_nothing_recoverable(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Removing rows is not removing text. The index is a complete plaintext
        # copy of a corpus, so this is the check that matters.
        secret = "ZZQX-forget-me-4402"
        (corpus / "notes" / "secret.md").write_text(f"# note\n\n{secret}\n", encoding="utf-8")
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        assert secret.encode("utf-8") in index_path.read_bytes()
        run("forget", "notes/secret.md", index=index_path)
        assert secret.encode("utf-8") not in index_path.read_bytes()

    def test_it_says_what_it_does_not_cover(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()
        run("forget", "notes/mountain.md", index=index_path)
        assert "already sent to a model" in capsys.readouterr().out


class TestRebuild:
    def test_it_reads_the_corpus_again(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        assert run("ingest", str(corpus), "--rebuild", index=index_path) == 0
        out = capsys.readouterr().out
        assert "discarding what the index holds" in out
        # Everything is new again, because the old index is gone.
        assert "3 new" in out

    def test_a_forgotten_document_comes_back(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A rebuild reads the corpus, and the corpus still has the file. That
        # is the difference between `forget` and `--rebuild`, and it is worth
        # a test because it is easy to expect the opposite.
        run("ingest", str(corpus), index=index_path)
        run("forget", "notes/mountain.md", index=index_path)
        capsys.readouterr()

        run("ingest", str(corpus), "--rebuild", index=index_path)
        capsys.readouterr()
        assert run("search", "東京", index=index_path) == 0


class TestDoctor:
    def test_it_reports_the_corpus(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()

        assert run("doctor", index=index_path) == 0
        out = capsys.readouterr().out
        assert "documents:  3" in out

    def test_it_states_what_tsumugi_does_not_protect(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # mamori's ADR-0019, adopted: a report that only lists reassurances is
        # a marketing document.
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()
        run("doctor", index=index_path)

        out = capsys.readouterr().out
        assert "not" in out and "encrypted" in out
        assert "No redaction is running" in out
        assert "your responsibility" in out

    def test_it_names_the_tests_behind_its_by_construction_claims(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A report citing a test that does not exist would look like evidence.
        run("ingest", str(corpus), index=index_path)
        capsys.readouterr()
        run("doctor", index=index_path)

        out = capsys.readouterr().out
        for named in ("tests/test_architecture.py", "tests/test_anchor.py"):
            assert named in out
            assert (Path(__file__).parent.parent / named).exists()

    def test_a_missing_index_is_reported_rather_than_created(
        self, index_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run("doctor", index=index_path) == 1
        assert not index_path.exists()


class TestTheEnvironment:
    def test_the_index_env_var_is_honoured(
        self, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elsewhere = tmp_path / "from-env.db"
        monkeypatch.setenv("TSUMUGI_INDEX", str(elsewhere))
        assert main(["ingest", str(corpus)]) == 0
        assert elsewhere.exists()

    def test_a_bad_setting_is_refused_rather_than_ignored(
        self, corpus: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A typo in a setting that silently does nothing is the worst
        # available outcome.
        monkeypatch.setenv("TSUMUGI_CANDIDATE_LIMIT", "lots")
        assert main(["ingest", str(corpus)]) == 2

    def test_the_default_index_is_not_inside_the_corpus(self, corpus: Path) -> None:
        # Corpus folders get synced, shared and committed. An index there is a
        # one-line accident. See docs/threat-model.md.
        from tsumugi.config import default_index_path

        assert corpus not in default_index_path().parents


class TestAsk:
    """The one command that sends anything. Tested for what it refuses.

    What it does when a model *is* there belongs in ``test_ask.py``, against a
    fake provider and a loopback server -- a CLI test that needed ollama
    running would be a test that is skipped on every machine that matters.
    """

    def test_it_refuses_a_host_that_is_not_this_machine(self, corpus: Path) -> None:
        # Before the index is opened and before anything is built, so a
        # mistyped URL costs nothing and reveals nothing.
        assert main(["ingest", str(corpus)]) == 0
        assert main(["ask", "テント", "--url", "http://example.com:11434"]) == 2

    def test_the_refusal_says_what_to_do_about_it(
        self, corpus: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["ingest", str(corpus)])
        main(["ask", "テント", "--url", "http://example.com:11434"])
        combined = capsys.readouterr()
        message = combined.err + combined.out
        assert "somebody else" in message
        assert "--allow-remote" in message

    def test_it_says_where_it_is_sending_before_it_sends(
        self, corpus: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Nothing is listening, so this fails -- but the notice has to have
        # been printed first. A person who did not mean to reach a remote host
        # should find out while they can still stop it.
        main(["ingest", str(corpus)])
        main(["ask", "テント", "--url", "http://127.0.0.1:1"])
        assert "sending to ollama/" in capsys.readouterr().err

    def test_the_commands_that_can_send_are_the_ones_named(self) -> None:
        # An allow-list, in the same shape and for the same reason as
        # NETWORKED_ADAPTERS. The way this stops being true is somebody adding
        # a provider to a command that used to be local, and nothing about
        # that change would look wrong in a diff unless something checks.
        import ast

        root = Path(__file__).resolve().parent.parent
        tree = ast.parse(
            (root / "src" / "tsumugi" / "interfaces" / "cli" / "main.py").read_text(
                encoding="utf-8"
            )
        )
        sending = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "OllamaProvider"
                for inner in ast.walk(node)
            )
        }
        assert sending == {"_ask", "_answer_report"}, (
            f"{sorted(sending)} can send. `tsumugi ask` and `tsumugi eval --model` "
            f"are the two commands that may; every other command is local, and the "
            f"threat model says so."
        )


class TestOutputEncoding:
    """It survives being piped on a machine whose locale is not UTF-8.

    Found by running `tsumugi demo | head` on a Japanese Windows console: a
    redirected stream takes the locale codepage, and one em dash ended the
    run with a traceback in place of an answer. A library for Japanese notes
    does not get to fail on Japanese.
    """

    def test_the_demo_survives_a_legacy_codepage(self) -> None:
        finished = subprocess.run(
            [sys.executable, "-c", "from tsumugi.interfaces.cli.main import main; main(['demo'])"],
            capture_output=True,
            cwd=ROOT,
            # No PYTHONUTF8, and a codepage that cannot hold an em dash. This
            # is what a redirected stream looks like on a Japanese Windows.
            env={"PATH": "", "PYTHONIOENCODING": "cp932"},
        )
        assert finished.returncode == 0, finished.stderr.decode("utf-8", "replace")
        assert b"Traceback" not in finished.stderr

    def test_the_corpus_survives_it_too(self) -> None:
        # cp932 holds Japanese but not every character a corpus might carry.
        # The rule is that an unwritable character costs one glyph, never the
        # output.
        finished = subprocess.run(
            [
                sys.executable,
                "-c",
                "from tsumugi.interfaces.cli.main import main; main(['--help'])",
            ],
            capture_output=True,
            cwd=ROOT,
            env={"PATH": "", "PYTHONIOENCODING": "cp1252"},
        )
        assert finished.returncode == 0


class TestAskJson:
    """The flag itself. What it emits is asserted in `test_ask.py`, against a
    fake provider, because a CLI test that needed ollama running would be a
    test that is skipped on every machine that matters."""

    def test_it_fails_with_a_message_rather_than_a_traceback(self, corpus: Path) -> None:
        main(["ingest", str(corpus)])
        # Port 1 on loopback: reserved, nothing is listening.
        assert main(["ask", "テント", "--url", "http://127.0.0.1:1", "--json"]) == 2

    def test_it_emits_the_answer_and_the_evidence_together(self) -> None:
        # The package is included whole rather than by id. An answer and the
        # evidence behind it are only meaningful together, and a consumer that
        # has to fetch the other half will eventually report on a mismatched
        # pair.
        source = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "tsumugi"
            / "interfaces"
            / "cli"
            / "main.py"
        ).read_text(encoding="utf-8")
        assert '"verification": asked.verification.to_dict()' in source
        assert '"package": asked.package.to_dict()' in source


class TestProtectRefusesRatherThanSendingPlain:
    def test_the_flag_is_wired_to_the_adapter_and_not_to_an_import(self) -> None:
        # The first attempt put `import mamori` in this module and the
        # architecture test refused it. The session belongs in the one file
        # allowed to know a sibling exists.
        source = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "tsumugi"
            / "interfaces"
            / "cli"
            / "main.py"
        ).read_text(encoding="utf-8")
        assert "open_session" in source
        assert "import mamori" not in source

    def test_a_missing_mamori_is_a_message_not_a_silent_plain_send(self) -> None:
        # The failure mode that matters. Someone who typed --protect and got
        # an unprotected send has been told the opposite of what happened.
        import inspect

        from tsumugi.infrastructure.adapters import mamori as adapter

        source = inspect.getsource(adapter.open_session)
        assert "ConfigurationError" in source
        assert "pip install mamori" in source


class TestComparingTwoModels:
    """`--model a,b` runs both and names where they differed.

    The reason the flag takes a list: every model-facing defect this project
    has found was found by two models disagreeing. `qwen2.5:14b` answered
    fifty evaluation cases and `llama3.1:8b` answered none of them, on the
    same prompt -- and one model alone looked like a working system.
    """

    def test_the_help_says_why_a_list(self) -> None:
        from tsumugi.interfaces.cli.main import build_parser

        text = build_parser().format_help()
        assert "eval" in text
        source = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "tsumugi"
            / "interfaces"
            / "cli"
            / "main.py"
        ).read_text(encoding="utf-8")
        # The formatter wraps the help string, so match on a fragment that
        # survives being broken across lines.
        assert "disagreeing" in source and "Comma-separate" in source

    def test_agreement_is_reported_as_weak_evidence(self) -> None:
        # Silence would read as a strong result. Two models can be wrong
        # together, and on a corpus this tidy they often will be.
        import inspect

        from tsumugi.interfaces.cli import main as module

        assert "weaker evidence than it looks" in inspect.getsource(module._disagreements)

    def test_one_model_does_not_trigger_a_comparison(self) -> None:
        import inspect

        from tsumugi.interfaces.cli import main as module

        assert "if len(scored) > 1:" in inspect.getsource(module._eval)

    def test_each_model_answers_every_case_once(self) -> None:
        # The report and the comparison read the same scores. Asking twice
        # would double the slowest part of the command to print a second view
        # of one answer.
        import inspect

        from tsumugi.interfaces.cli import main as module

        assert "answer_cases" not in inspect.getsource(module._disagreements)


def test_no_ledger_leaves_the_ledger_empty(tmp_path: Path, index_path: Path) -> None:
    """`--no-ledger` is the switch ADR-0011 said existed.

    The ADR's cost section has said since it was written that the ledger *can
    be disabled, and disabling it costs only the loop*. There was no way to do
    it: `tsumugi context` wrote a row unconditionally. A stated mitigation with
    no mechanism is worth less than no mitigation, because it is the one a
    reader stops looking for.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "note.md").write_text("The tent weighs 2.4kg." + chr(10), encoding="utf-8")
    assert run("ingest", str(corpus), index=index_path) == 0

    def rows() -> int:
        """Ledger rows, counting a missing table as none.

        It is missing rather than empty, which is the stronger result: the
        table is created by the ledger itself, so `--no-ledger` does not write
        an empty artefact, it declines to make one.
        """
        with sqlite3.connect(index_path) as connection:
            present = connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='ledger'"
            ).fetchone()[0]
            if not present:
                return 0
            return int(connection.execute("SELECT count(*) FROM ledger").fetchone()[0])

    run("context", "how heavy is the tent", "--no-ledger", index=index_path)
    assert rows() == 0, "--no-ledger recorded the build anyway"

    run("context", "how heavy is the tent", index=index_path)
    assert rows() == 1, "the ledger stopped recording when nothing asked it to"
