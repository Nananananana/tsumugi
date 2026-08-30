"""Document text must not escape through a log, a repr or a traceback.

tsumugi holds a person's notes. The places text leaks are the boring ones: an
exception message that interpolated the value it was complaining about, a
``repr`` that dumped a whole document into a debugger, a log line written
during ingest.

This file greps for a distinctive string after doing the things that could
leak it. It is the same idea as `mamori`'s ``test_security_leakage.py``, and it
is here for the same reason: the promise is worth nothing unless something
checks it.
"""

from __future__ import annotations

import io
import logging
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

from tsumugi.application.ingest import ingest_paths
from tsumugi.domain.anchor import Anchor, resolve
from tsumugi.domain.hashing import ContentHash
from tsumugi.domain.span import Span
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

from .helpers import build_document

#: Distinctive enough that finding it anywhere is unambiguous.
SECRET = "ZZQX-diary-entry-about-the-argument-7731"


class TestReprAndErrors:
    def test_a_document_repr_does_not_print_the_whole_corpus(self) -> None:
        # Not a privacy guarantee so much as a debugger one: a repr that dumps
        # a megabyte of notes into a traceback is how text ends up in a log.
        document = build_document("diary.md", f"# entry\n\n{SECRET}\n" * 200)
        assert len(repr(document)) < 20_000

    def test_a_failed_resolution_names_offsets_not_text(self) -> None:
        document = build_document("diary.md", f"# entry\n\n{SECRET}\n")
        anchor = Anchor(
            document.document_id,
            Span(0, 99_999),
            ContentHash.of("x"),
            document.version,
        )
        result = resolve(anchor, document)
        assert SECRET not in result.detail

    def test_a_mismatched_document_error_names_ids_not_text(self) -> None:
        anchor = Anchor.into(build_document("a.md", SECRET), Span(0, 4))
        with pytest.raises(ValueError) as raised:
            resolve(anchor, build_document("b.md", SECRET))
        assert SECRET not in str(raised.value)

    def test_a_document_whose_hash_lies_reports_hashes_not_content(self) -> None:
        from tsumugi.domain.document import Document

        lying = Document(
            document_id="doc_x",
            version=ContentHash.of("elsewhere"),
            source_path="diary.md",
            media_type="text/plain",
            content=SECRET,
        )
        with pytest.raises(ValueError) as raised:
            lying.verify()
        assert SECRET not in str(raised.value)


class TestIngestion:
    def test_ingesting_writes_nothing_to_a_log(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            (corpus / "diary.md").write_text(f"# entry\n\n{SECRET}\n", encoding="utf-8")
            ingest_paths(
                [corpus / "diary.md"],
                root=corpus,
                store=store,
                index=index,
                parser_for=parser_for,
            )
        finally:
            root.removeHandler(handler)

        assert SECRET not in stream.getvalue()

    def test_an_unreadable_file_reports_the_error_without_the_bytes(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "binary.txt").write_bytes(b"\x00\x01" + SECRET.encode())

        report = ingest_paths(
            [corpus / "binary.txt"],
            root=corpus,
            store=store,
            index=index,
            parser_for=parser_for,
        )
        assert report.failed
        assert all(SECRET not in reason for _, reason in report.failed)

    def test_a_traceback_from_a_broken_parser_carries_no_document_text(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "bad.json").write_text(f'{{"note": "{SECRET}", oh no', encoding="utf-8")

        report = ingest_paths(
            [corpus / "bad.json"],
            root=corpus,
            store=store,
            index=index,
            parser_for=parser_for,
        )
        # A JSON error message quotes the offending region by design, so the
        # test asserts what is actually true: the report names the file and the
        # position, and the parser's message is not allowed to grow into the
        # document. Anything more would be a false promise.
        assert report.failed
        for path, reason in report.failed:
            assert path == "bad.json"
            assert len(reason) < 200


class TestTracebacks:
    def test_an_exception_raised_deep_in_a_slice_does_not_print_the_text(self) -> None:
        document = build_document("diary.md", SECRET)
        try:
            Span(0, 99_999).slice(document.content)
        except ValueError:
            printed = traceback.format_exc()
        assert SECRET not in printed


class TestItImportsWithoutItsOptionalSiblings:
    """`pip install tsumugi` and import it. No mamori, no kiseki, no error.

    The static rules next door prove *who imports whom, and where*, which is a
    different thing. `iriguchi` drew the line: a property that reduces to the
    import graph is worth proving statically and needs no environment; this one
    does not reduce to that, because the guard sits where the static rules
    already allow a sibling to be named -- inside the adapter.

    So this proves it directly, by making the sibling absent. It costs eight
    lines because the absence is faked in-process rather than by installing
    anything.

    CI covers this today as a side effect: the test job installs `[dev]` and
    nothing else, so mamori is missing there and a hoisted import fails
    collection. That is real coverage and it is nobody's stated intent -- a
    later commit installing the sibling in that job, to run the integration
    tests in CI, would remove it silently. This test says the intent out loud and fails
    on a developer's machine, where the sibling *is* installed.
    """

    SIBLINGS = ("mamori", "kiseki")

    def _without_siblings(self) -> str:
        """A subprocess that cannot import any sibling, importing the world."""
        program = (
            "import sys\n"
            "class Blocker:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            f"        if name.split('.')[0] in {self.SIBLINGS!r}:\n"
            "            raise ModuleNotFoundError(name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, Blocker())\n"
            # Everything a user touches: the package, the CLI, the MCP server,
            # the use case that takes a redactor, and the adapter itself.
            "import tsumugi\n"
            "import tsumugi.interfaces.cli.main\n"
            "import tsumugi.interfaces.mcp.server\n"
            "import tsumugi.application.ask\n"
            "import tsumugi.infrastructure.adapters.mamori\n"
            "import tsumugi.infrastructure.adapters.kiseki\n"
            "print('imported')\n"
        )
        finished = subprocess.run(  # noqa: S603
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=Path(__file__).resolve().parent.parent,
        )
        assert finished.returncode == 0, finished.stderr
        return finished.stdout

    def test_the_blocker_actually_blocks(self) -> None:
        # First, because a check for absence that quietly stopped working
        # would report success forever. The last one of these was written with
        # `find_module`, removed in 3.12, and passed while doing nothing.
        program = (
            "import sys\n"
            "class Blocker:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'mamori':\n"
            "            raise ModuleNotFoundError(name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, Blocker())\n"
            "import mamori\n"
        )
        finished = subprocess.run(  # noqa: S603
            [sys.executable, "-c", program], capture_output=True, text=True
        )
        assert finished.returncode != 0
        assert "ModuleNotFoundError" in finished.stderr

    def test_everything_a_user_touches_imports(self) -> None:
        assert "imported" in self._without_siblings()
