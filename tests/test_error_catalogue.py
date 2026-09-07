"""Every failure that can be printed is in the catalogue, and carries no values.

`sora` folds repeated failures into one line of an incident file and can
promise that file holds nothing sensitive **only** because it keeps the kind
and throws the sentence away — stderr may quote whatever it was handed. So the
catalogue it reads must contain no values either: no paths, no examples, no
message templates with holes a reader would fill from a log.

Two guarantees, and neither is a promise in prose:

1. **Exhaustive.** A test walks the package for exception classes and fails if
   one is missing here, so adding an error without cataloguing it is a failure
   rather than a silence.
2. **Valueless.** Every string is searched for the shapes a value takes.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
import re
import sqlite3
from pathlib import Path

import pytest

import tsumugi
from tsumugi.errors import (
    CATALOGUE,
    CONTRACT,
    OPEN_NAMESPACES,
    catalogue,
)
from tsumugi.interfaces.cli.main import main

#: The outcomes `sora` maps onto. A closed set: a fifth word would be one this
#: catalogue invented and no consumer knows.
OUTCOMES = frozenset({"refused", "unavailable", "failed", "timed_out"})

#: Kinds that are not ours and can still be reported by name, so they are
#: catalogued even though no class of ours defines them.
FOREIGN = frozenset({"ValueError", "DatabaseError"})

NL = chr(10)

#: Ours, and never reported as a kind. Named with the reason, so that leaving
#: one out of the catalogue is a decision rather than an oversight.
NOT_REPORTED_AS_A_KIND = {
    "TsumugiError": "the base class; nothing raises it directly",
    "RpcError": "a JSON-RPC error code on the wire, not a kind on stderr",
}


def _every_exception_class() -> set[str]:
    """Every exception class **defined in this package**, whatever it subclasses.

    Not just `TsumugiError` subclasses: `UnsupportedContractError` is a
    `ValueError`, is raised, and is reported by name through an MCP tool
    result. A walk that looked only for our base class would have missed it,
    and did.
    """
    found: set[str] = set()
    for module in pkgutil.walk_packages(tsumugi.__path__, f"{tsumugi.__name__}."):
        imported = importlib.import_module(module.name)
        for _name, obj in inspect.getmembers(imported, inspect.isclass):
            if issubclass(obj, BaseException) and obj.__module__.startswith("tsumugi."):
                found.add(obj.__name__)
    return found


class TestItIsExhaustive:
    def test_every_error_class_in_the_package_is_catalogued(self) -> None:
        """The guarantee, enforced by walking rather than by remembering.

        A new `TsumugiError` subclass prints its own name on stderr the first
        time it is raised. Without this, the catalogue would quietly describe a
        version that no longer exists.
        """
        catalogued = {kind.kind for kind in CATALOGUE}
        missing = _every_exception_class() - catalogued - set(NOT_REPORTED_AS_A_KIND)
        assert not missing, f"raised but not catalogued: {sorted(missing)}"

    def test_the_walk_finds_the_classes_it_should(self) -> None:
        """The positive control. A walk that found nothing would make the
        assertion above pass over an empty set."""
        found = _every_exception_class()
        assert {
            "ConfigurationError",
            "StorageError",
            "ProviderError",
            "UnsupportedContractError",
        } <= found, found

    def test_nothing_is_catalogued_that_cannot_be_raised(self) -> None:
        """The other direction: a kind left behind after its class went."""
        catalogued = {kind.kind for kind in CATALOGUE}
        unexplained = catalogued - _every_exception_class() - FOREIGN
        assert not unexplained, f"catalogued but nothing raises it: {sorted(unexplained)}"

    def test_every_uncatalogued_class_says_why(self) -> None:
        """The exemption list is an argument, not a hole.

        Each name in it is a class this package defines and deliberately does
        not report as a kind, with the reason beside it.
        """
        for name, reason in NOT_REPORTED_AS_A_KIND.items():
            assert name in _every_exception_class(), f"{name} no longer exists"
            assert reason.strip()
            assert name not in {kind.kind for kind in CATALOGUE}

    def test_the_foreign_kinds_really_are_reachable(self) -> None:
        """`ValueError` and `DatabaseError` are catalogued without being ours.

        Both are printed by name: the CLI's handler catches `sqlite3.DatabaseError`
        and reports that word, and `UnsupportedContractError` is a `ValueError`
        subclass whose siblings surface unqualified.
        """
        assert issubclass(sqlite3.DatabaseError, Exception)
        assert "DatabaseError" in {kind.kind for kind in CATALOGUE}
        assert "ValueError" in {kind.kind for kind in CATALOGUE}


class TestItCarriesNoValues:
    #: What a value looks like when it leaks into prose.
    FORBIDDEN = (
        (re.compile(r"[A-Za-z]:[\\/]"), "a Windows path"),
        (re.compile(r"(?<![a-z])/(home|Users|tmp|var|etc)/"), "a POSIX path"),
        (re.compile(r"\{\w*\}"), "a message template"),
        (re.compile(r"https?://"), "a URL"),
        (re.compile(r"\.db\b"), "a database filename"),
    )

    @pytest.mark.parametrize("kind", CATALOGUE, ids=lambda k: k.kind)
    def test_no_detail_contains_anything_a_reader_could_fill_in(self, kind: object) -> None:
        for text in (kind.detail, kind.detail_ja):  # type: ignore[attr-defined]
            for pattern, what in self.FORBIDDEN:
                assert not pattern.search(text), f"{what} in: {text}"

    def test_the_patterns_would_catch_a_leak(self) -> None:
        """The positive control. Patterns that match nothing would make every
        row above pass, which is how a check comes to check nothing."""
        leaks = [
            "no index at C:/Users/ada/personal.db",
            "no index at /home/ada/personal.db",
            "the index at {path} is missing",
            "see https://example.com/help",
        ]
        for leak in leaks:
            assert any(pattern.search(leak) for pattern, _ in self.FORBIDDEN), leak


class TestTheShapeIsWhatWasAskedFor:
    def test_the_contract_is_named_and_draft(self) -> None:
        assert CONTRACT == "tsumugi.errors/1-draft"
        assert catalogue("0.0.0-test")["contract"] == CONTRACT

    def test_every_outcome_is_one_of_the_four(self) -> None:
        assert {kind.outcome for kind in CATALOGUE} <= OUTCOMES

    def test_every_kind_is_a_bare_identifier(self) -> None:
        """It is matched against the word before the colon on stderr."""
        for kind in CATALOGUE:
            assert kind.kind.isidentifier(), kind.kind
            assert ":" not in kind.kind

    def test_both_sentences_are_present_and_one_line(self) -> None:
        """Written by the same hand, so they cannot disagree."""
        for kind in CATALOGUE:
            assert kind.detail.strip()
            assert kind.detail_ja.strip()
            assert "\n" not in kind.detail and "\n" not in kind.detail_ja

    def test_open_namespaces_is_empty_and_says_so(self) -> None:
        assert OPEN_NAMESPACES == ()
        assert catalogue("0.0.0-test")["open_namespaces"] == []

    def test_no_kind_claims_an_exit_code_that_is_an_outcome(self) -> None:
        """0 and 1 are outcomes rather than failures.

        `context` exits 1 for "nothing was confirmed" and `verify` for "not
        every claim is supported". Neither is in this catalogue, and a caller
        cross-checking its own mapping needs that to stay true.
        """
        assert {kind.exit_code for kind in CATALOGUE} <= {2, None}
        assert 2 in {kind.exit_code for kind in CATALOGUE}

    def test_a_null_exit_code_says_where_the_kind_does_arrive(self) -> None:
        """`None` is a claim about the surface, so it has to be explained."""
        for kind in CATALOGUE:
            if kind.exit_code is None:
                assert "MCP" in kind.detail, kind.kind


class TestTheCommandLine:
    def test_it_emits_the_catalogue_as_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["errors", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["contract"] == CONTRACT
        assert payload["by"].startswith("tsumugi/")
        assert {row["kind"] for row in payload["errors"]} == {k.kind for k in CATALOGUE}

    def test_it_needs_no_index(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A caller asking what can go wrong is often asking *because* the
        index is what went wrong."""
        assert main(["--index", "/nonexistent/none.db", "errors", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["errors"]

    def test_the_human_form_names_every_kind(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["errors"]) == 0
        printed = capsys.readouterr().out
        for kind in CATALOGUE:
            assert kind.kind in printed


class TestStderrLeadsWithTheKind:
    def test_a_failure_prints_the_kind_before_the_colon(
        self, tmp_path: object, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """R-E2. `tsumugi:` was the same word for every failure there is, so a
        caller folding failures together learned nothing from it."""
        code = main(["--index", str(tmp_path) + "/absent.db", "search", "tent"])
        first = capsys.readouterr().err.splitlines()[0]
        assert code == 2
        assert first.split(":")[0] == "StorageError", first

    def test_the_word_it_prints_is_one_the_catalogue_contains(
        self, tmp_path: object, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The two halves have to agree, or the catalogue describes a
        vocabulary the binary does not speak."""
        main(["--index", str(tmp_path) + "/absent.db", "search", "tent"])
        first = capsys.readouterr().err.splitlines()[0]
        assert first.split(":")[0] in {kind.kind for kind in CATALOGUE}


class TestNothingOnStderrPretendsToBeAKind:
    """A consumer reads the first name-shaped line on stderr as the failure.

    `sora` takes the text before the first colon, keeps it if it is a bare
    identifier, and throws the sentence away — stderr may quote whatever it was
    handed, so the name is the only part it can promise to hold.

    That makes **every** `identifier:` at the start of a stderr line a claim
    about what went wrong, whether it meant to be or not. Two lines here were
    not: `tsumugi: no such path` and `index: <path>`, the second added by
    moving diagnostics to stderr for an unrelated and correct reason. An ingest
    that failed reported a failure kind called `index`.

    So the rule is general rather than three fixes: **a leading identifier on
    stderr must be a catalogued kind.**
    """

    KINDS = frozenset(kind.kind for kind in CATALOGUE)

    def _leading_name(self, line: str) -> str | None:
        """What a consumer would take from this line, or ``None``."""
        head, separator, _rest = line.partition(":")
        if not separator:
            return None
        return head if head.isidentifier() else None

    def test_the_reader_agrees_with_soras_examples(self) -> None:
        """The parser itself, against the cases sora wrote down."""
        assert self._leading_name("StorageError: no index at /home/a/p.db") == "StorageError"
        assert self._leading_name("error: unexpected argument '--state-dir' found") == "error"
        assert self._leading_name("/home/someone/notes.db: not found") is None
        windows = "  index   C" + chr(58) + chr(92) + "Users" + chr(92) + "a"
        assert self._leading_name(windows) is None
        assert self._leading_name("3 new, 0 revised") is None

    def test_a_failing_ingest_names_a_kind_or_names_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exit 1 from ingest: a document failed, the run did not."""
        corpus = tmp_path / "notes"
        corpus.mkdir()
        (corpus / "ok.md").write_text("# G\n\ntext\n", encoding="utf-8", newline="")
        (corpus / "bad.json").write_text("not json at all", encoding="utf-8")

        code = main(["--index", str(tmp_path / "i.db"), "ingest", str(corpus)])
        assert code == 1
        for line in capsys.readouterr().err.splitlines():
            name = self._leading_name(line)
            assert name is None or name in self.KINDS, line

    def test_a_missing_path_names_a_real_kind(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exit 2, so there *is* a kind, and it is a catalogued one.

        It printed `tsumugi: no such path` and would have been recorded as a
        failure kind called `tsumugi` — the program's own name, attached to
        every failure it has.
        """
        code = main(["--index", str(tmp_path / "i.db"), "ingest", str(tmp_path / "absent")])
        assert code == 2
        first = capsys.readouterr().err.splitlines()[0]
        assert self._leading_name(first) == "ConfigurationError"

    def test_the_quiet_exits_stay_quiet(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`context` and `verify` exit 1 for outcomes rather than failures, so
        there must be nothing on stderr for a consumer to read as a kind."""
        corpus = tmp_path / "notes"
        corpus.mkdir()
        (corpus / "g.md").write_text(
            "# G" + NL * 2 + "The tent weighs 2.4kg." + NL,
            encoding="utf-8",
            newline="",
        )
        index = tmp_path / "i.db"
        main(["--index", str(index), "ingest", str(corpus)])
        capsys.readouterr()

        code = main(
            [
                "--index",
                str(index),
                "context",
                "unrelated refund policy",
                "--budget",
                "characters:300",
            ]
        )
        assert code == 1
        assert capsys.readouterr().err.strip() == ""
