"""Whether the file a document came from still says what it said.

The gap this closes was invisible from inside the store, which is why it
survived four versions: an anchor checked against the store always resolves,
because the store holds the text it anchored. Only the disk knows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tsumugi.application.build_context import build_context
from tsumugi.application.ingest import ingest_paths
from tsumugi.domain.budget import Budget
from tsumugi.domain.omission import OmissionRule
from tsumugi.infrastructure.cost.heuristic import CharacterCost
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.freshness import FilesystemFreshness, NeverStale
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore
from tsumugi.ports.freshness import FreshnessCheck

from .helpers import build_document

TEXT = "# 装備\n\nテントの重量は2.4kg、二人用。予備は持たない。\n"


class TestTheCheck:
    def test_both_implementations_satisfy_the_port(self, tmp_path: Path) -> None:
        assert isinstance(NeverStale(), FreshnessCheck)
        assert isinstance(FilesystemFreshness(tmp_path), FreshnessCheck)

    def test_an_unchanged_file_is_current(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text(TEXT, encoding="utf-8", newline="")
        assert FilesystemFreshness(tmp_path).is_current(build_document("a.md", TEXT))

    def test_an_edited_file_is_not(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text(TEXT + "追記。\n", encoding="utf-8", newline="")
        assert not FilesystemFreshness(tmp_path).is_current(build_document("a.md", TEXT))

    def test_an_edit_that_keeps_the_length_is_still_caught(self, tmp_path: Path) -> None:
        # Size is a free pre-check, not the check. An edit that swaps one
        # character for another of the same width would slip past a size
        # comparison, and it is exactly the edit that matters: a corrected
        # value.
        same_length = TEXT.replace("2.4kg", "3.1kg")
        assert len(same_length) == len(TEXT)
        (tmp_path / "a.md").write_text(same_length, encoding="utf-8", newline="")
        assert not FilesystemFreshness(tmp_path).is_current(build_document("a.md", TEXT))

    def test_a_missing_file_is_reported_as_current(self, tmp_path: Path) -> None:
        # A missing file is a different problem from a changed one, and
        # `doctor` is where it belongs. Answering "stale" here would mark every
        # document on an unmounted drive as historical.
        assert FilesystemFreshness(tmp_path).is_current(build_document("gone.md", TEXT))

    def test_a_byte_order_mark_is_not_a_change(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_bytes(TEXT.encode("utf-8-sig"))
        assert FilesystemFreshness(tmp_path).is_current(build_document("a.md", TEXT))

    def test_never_stale_says_so_in_its_name(self) -> None:
        # So that a package built without a corpus says it did not check,
        # rather than letting a reader take "no stale anchors" for "nothing
        # was stale".
        assert "unchecked" in NeverStale().name

    def test_the_answer_is_cached_per_document_version(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        path.write_text(TEXT, encoding="utf-8", newline="")
        check = FilesystemFreshness(tmp_path)
        document = build_document("a.md", TEXT)
        assert check.is_current(document)

        path.unlink()
        # Cached: a package drawing several passages from one document reads
        # it once.
        assert check.is_current(document)


class TestThroughAPackage:
    @pytest.fixture
    def corpus_and_index(self, tmp_path: Path, connection: object) -> tuple[Path, object, object]:
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "current.md").write_text(TEXT, encoding="utf-8", newline="")
        (root / "drifting.md").write_text(
            "# 装備\n\nテントの重量は3.1kg、二人用。改訂前。\n", encoding="utf-8", newline=""
        )
        store = SqliteDocumentStore(connection)  # type: ignore[arg-type]
        index = FtsIndex(connection)  # type: ignore[arg-type]
        found = walk(root)
        ingest_paths(found.files, root=root, store=store, index=index, parser_for=parser_for)
        return root, store, index

    def test_an_edited_file_becomes_a_stale_anchor_omission(
        self, corpus_and_index: tuple[Path, object, object]
    ) -> None:
        root, store, index = corpus_and_index
        (root / "drifting.md").write_text(
            "# 装備\n\nこの記録は全面的に書き直された。\n", encoding="utf-8", newline=""
        )

        package = build_context(
            "テントの重量は",
            store=store,  # type: ignore[arg-type]
            index=index,  # type: ignore[arg-type]
            cost_model=CharacterCost(),
            budget=Budget.characters(2000),
            freshness=FilesystemFreshness(root),
        )

        stale = [o for o in package.omissions if o.rule is OmissionRule.STALE_ANCHOR]
        assert stale
        assert "drifting.md" in stale[0].source_path
        assert "was true in the version that was read" in stale[0].reason

    def test_the_current_document_is_still_sent(
        self, corpus_and_index: tuple[Path, object, object]
    ) -> None:
        root, store, index = corpus_and_index
        (root / "drifting.md").write_text("# 装備\n\n書き直し。\n", encoding="utf-8", newline="")

        package = build_context(
            "テントの重量は",
            store=store,  # type: ignore[arg-type]
            index=index,  # type: ignore[arg-type]
            cost_model=CharacterCost(),
            budget=Budget.characters(2000),
            freshness=FilesystemFreshness(root),
        )
        assert any("current.md" in item.source_path for item in package.items)

    def test_without_a_freshness_check_nothing_is_reported_stale(
        self, corpus_and_index: tuple[Path, object, object]
    ) -> None:
        # The gap as it was: checking against the store can never find
        # staleness, because the store holds the text it anchored.
        root, store, index = corpus_and_index
        (root / "drifting.md").write_text("# 装備\n\n書き直し。\n", encoding="utf-8", newline="")

        package = build_context(
            "テントの重量は",
            store=store,  # type: ignore[arg-type]
            index=index,  # type: ignore[arg-type]
            cost_model=CharacterCost(),
            budget=Budget.characters(2000),
        )
        assert not [o for o in package.omissions if o.rule is OmissionRule.STALE_ANCHOR]

    def test_the_package_says_whether_it_checked(
        self, corpus_and_index: tuple[Path, object, object]
    ) -> None:
        root, store, index = corpus_and_index
        checked = build_context(
            "テントの重量は",
            store=store,  # type: ignore[arg-type]
            index=index,  # type: ignore[arg-type]
            cost_model=CharacterCost(),
            budget=Budget.characters(2000),
            freshness=FilesystemFreshness(root),
        )
        unchecked = build_context(
            "テントの重量は",
            store=store,  # type: ignore[arg-type]
            index=index,  # type: ignore[arg-type]
            cost_model=CharacterCost(),
            budget=Budget.characters(2000),
        )
        assert "filesystem@1" in " ".join(checked.provenance.providers)
        assert "unchecked" in " ".join(unchecked.provenance.providers)
