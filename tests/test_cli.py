"""The command line, and the things it must never fail to say.

Every test here runs against a throwaway index (see ``conftest.py``). A CLI
test that writes into the developer's real corpus is a bug that only shows up
once, badly.
"""

from __future__ import annotations

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
