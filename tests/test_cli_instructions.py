"""`tsumugi context --instructions answering`, from the command line.

The answering set was reachable only through `ask`, which brings its own model.
A caller running the model itself -- `sora` holds the device -- had no way to
get the one prompt shape `verify` can check, and every answer verified as
*uncited*. Both composition roots now offer the choice; this file covers the
CLI, `test_mcp.py` covers the server.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tsumugi.interfaces.cli.main import main


def run(*argv: str, index: Path) -> int:
    return main(["--index", str(index), *argv])


@pytest.fixture
def ingested(corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    index = tmp_path / "i.db"
    assert run("ingest", str(corpus), index=index) == 0
    capsys.readouterr()
    return index


class TestTheInstructionSetIsAChoice:
    def test_answering_renders_the_output_schema_and_default_does_not(
        self, ingested: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run("context", "テント", "--budget", "characters:2000", index=ingested)
        person = capsys.readouterr().out
        run(
            "context",
            "テント",
            "--budget",
            "characters:2000",
            "--instructions",
            "answering",
            index=ingested,
        )
        machine = capsys.readouterr().out

        assert "OUTPUT_SCHEMA" not in person
        assert "OUTPUT_SCHEMA" in machine
        assert "JSON only" in machine

    def test_the_two_are_different_packages(
        self, ingested: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Different prompts, so different ids. An id that called them the
        same would be the thing that is wrong."""
        run("context", "テント", "--budget", "characters:2000", "--json", index=ingested)
        person = json.loads(capsys.readouterr().out)
        run(
            "context",
            "テント",
            "--budget",
            "characters:2000",
            "--json",
            "--instructions",
            "answering",
            index=ingested,
        )
        machine = json.loads(capsys.readouterr().out)

        assert person["output_schema"] is None
        assert machine["output_schema"]["required"] == ["claims"]
        assert person["package_id"] != machine["package_id"]

    def test_a_misspelling_is_refused_by_the_parser(self, ingested: Path) -> None:
        """argparse's `choices` does this; the test pins that it stays a choice
        rather than a free string that would fall back to the default."""
        with pytest.raises(SystemExit) as raised:
            run("context", "テント", "--instructions", "answerng", index=ingested)
        assert raised.value.code == 2
