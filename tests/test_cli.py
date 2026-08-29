"""The command line, and the things it must never fail to say.

Every test here runs against a throwaway index (see ``conftest.py``). A CLI
test that writes into the developer's real corpus is a bug that only shows up
once, badly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tsumugi.interfaces.cli.main import main


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
        jsonschema = pytest.importorskip("jsonschema")
        schema = json.loads(
            (Path(__file__).parent.parent / "schemas" / "context-package-1.json").read_text(
                encoding="utf-8"
            )
        )
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
        """Enough competing documents that a budget can actually bind."""
        for n in range(6):
            (corpus / "notes" / f"gear-{n}.md").write_text(
                f"# 装備 {n}\n\nテントの候補 {n} について。重量と設営のしやすさを比較する。\n",
                encoding="utf-8",
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
    ) -> tuple[Path, dict[str, object]]:
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
