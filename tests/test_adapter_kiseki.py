"""Reading kiseki's export, and the layering surviving the crossing.

The adapter imports nothing: the export is JSON with a documented shape, so
this needs neither `kiseki` installed nor a version of it agreed. Coupling to a
published contract rather than to a schema is the whole difference between an
adapter and a reach-in, and it is why this file has no skip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tsumugi.application.build_context import build_context
from tsumugi.application.ingest import ingest_paths
from tsumugi.domain.budget import Budget
from tsumugi.domain.selection import Layer
from tsumugi.errors import IngestionError
from tsumugi.infrastructure.adapters.kiseki import (
    EXPORT_SCHEMA,
    read_export,
    render_export,
)
from tsumugi.infrastructure.cost.heuristic import CharacterCost
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

#: The shape kiseki's `interest_export` produces. Invented values.
EXPORT: dict[str, Any] = {
    "schema": EXPORT_SCHEMA,
    "version": 1,
    "exported_on": "2026-08-30",
    "interests": [
        {
            "topic": "陶芸",
            "score": 0.82,
            "confidence": 0.71,
            "first_seen": "2025-04",
            "last_seen": "2026-07",
        },
        {
            "topic": "登山",
            "score": 0.64,
            "confidence": 0.55,
            "first_seen": "2024-09",
            "last_seen": "2026-06",
        },
    ],
    "stages": [{"topic": "陶芸", "stage": "deepening"}],
}


class TestReadingIt:
    def test_a_well_formed_export_reads(self) -> None:
        export = read_export(json.dumps(EXPORT, ensure_ascii=False))
        assert export.exported_on == "2026-08-30"
        assert len(export.interests) == 2
        assert export.stages["陶芸"] == "deepening"

    def test_an_unknown_schema_is_refused(self) -> None:
        # Fail closed: a consumer that cannot verify the shape refuses it
        # rather than guessing, the same rule the ContextPackage applies to
        # itself.
        payload = json.dumps({**EXPORT, "schema": "something-else"})
        with pytest.raises(IngestionError, match="unrecognised export schema"):
            read_export(payload)

    def test_an_unknown_version_is_refused(self) -> None:
        with pytest.raises(IngestionError, match="version"):
            read_export(json.dumps({**EXPORT, "version": 99}))

    def test_an_interest_with_no_confidence_is_refused(self) -> None:
        # An interpretation with no confidence is an opinion wearing a fact's
        # clothes, and the package would refuse to carry it anyway.
        broken = {**EXPORT, "interests": [{"topic": "陶芸", "score": 0.8}]}
        with pytest.raises(IngestionError, match="no confidence"):
            read_export(json.dumps(broken, ensure_ascii=False))

    def test_something_that_is_not_json_says_so(self) -> None:
        with pytest.raises(IngestionError, match="not a kiseki export"):
            read_export("interests: 陶芸")


class TestRenderingIt:
    def test_every_interest_becomes_a_line(self) -> None:
        # One line per claim, so an anchor into it covers exactly one.
        text, _ = render_export(read_export(json.dumps(EXPORT, ensure_ascii=False)))
        assert "陶芸" in text
        assert "登山" in text
        assert text.count("confidence") >= 2

    def test_the_confidence_travels_in_the_text(self) -> None:
        text, _ = render_export(read_export(json.dumps(EXPORT, ensure_ascii=False)))
        assert "0.71" in text

    def test_the_document_says_it_is_interpretation(self) -> None:
        _, metadata = render_export(read_export(json.dumps(EXPORT, ensure_ascii=False)))
        assert metadata["layer"] == "interpretation"
        assert metadata["producer"].startswith("kiseki-interest-export/")
        assert metadata["observed_at"] == "2026-08-30"

    def test_the_text_itself_warns_the_reader(self) -> None:
        # A model reading this should not have to consult the metadata to know
        # it is reading a reading.
        text, _ = render_export(read_export(json.dumps(EXPORT, ensure_ascii=False)))
        assert "interpretation" in text
        assert "not an observation" in text


class TestThroughAPackage:
    def _package(self, tmp_path: Path, connection: Any, query: str) -> Any:
        text, metadata = render_export(read_export(json.dumps(EXPORT, ensure_ascii=False)))
        root = tmp_path / "corpus"
        root.mkdir(exist_ok=True)
        (root / "kiseki-export.md").write_text(
            "---\n" + "\n".join(f"{k}: {v}" for k, v in metadata.items()) + "\n---\n\n" + text,
            encoding="utf-8",
            newline="",
        )
        (root / "notes.md").write_text(
            "# 記録\n\n陶芸教室の申し込みは今月末まで。会場は駅の north side。\n",
            encoding="utf-8",
            newline="",
        )
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        found = walk(root)
        ingest_paths(found.files, root=root, store=store, index=index, parser_for=parser_for)
        return build_context(
            query,
            store=store,
            index=index,
            cost_model=CharacterCost(),
            budget=Budget.characters(3000),
        )

    def test_an_interest_reaches_a_package_as_an_interpretation(
        self, tmp_path: Path, connection: Any
    ) -> None:
        # The whole point. A reading of somebody's photographs does not become
        # a fact by crossing a library boundary -- that would be laundering.
        package = self._package(tmp_path, connection, "陶芸")
        from_export = [i for i in package.items if "kiseki" in i.source_path]
        assert from_export
        assert all(i.provenance.layer is Layer.INTERPRETATION for i in from_export)
        assert all(i.provenance.confidence is not None for i in from_export)

    def test_and_a_plain_note_is_still_a_fact(self, tmp_path: Path, connection: Any) -> None:
        package = self._package(tmp_path, connection, "陶芸")
        notes = [i for i in package.items if "notes.md" in i.source_path]
        assert notes
        assert all(i.provenance.layer is Layer.FACT for i in notes)
        assert all(i.provenance.confidence is None for i in notes)

    def test_the_prompt_labels_the_interpretation(self, tmp_path: Path, connection: Any) -> None:
        package = self._package(tmp_path, connection, "陶芸")
        rendered = package.render()
        assert "interpretation" in rendered

    def test_it_names_the_export_it_read(self, tmp_path: Path, connection: Any) -> None:
        # "the kiseki export of 2026-08-30 said this" is true and checkable.
        # "your photographs say this" would be neither.
        package = self._package(tmp_path, connection, "陶芸")
        from_export = next(i for i in package.items if "kiseki" in i.source_path)
        assert from_export.provenance.producer.startswith("kiseki-interest-export/")
        assert from_export.provenance.observed_at == "2026-08-30"

    def test_an_interest_is_anchored_and_traceable(self, tmp_path: Path, connection: Any) -> None:
        # kiseki exports no evidence references by design, so an interest
        # cannot be anchored to a photograph. It is anchored to the export,
        # which is the claim that is actually true.
        from tsumugi.domain.anchor import ResolutionStatus, resolve

        package = self._package(tmp_path, connection, "陶芸")
        store = SqliteDocumentStore(connection)
        item = next(i for i in package.items if "kiseki" in i.source_path)
        document = store.get(item.anchor.document_id, item.anchor.version)

        assert document is not None
        assert resolve(item.anchor, document).status is ResolutionStatus.RESOLVED
        assert item.anchor.span.slice(document.content) == item.text


class TestAnUnknownLayerIsRefused:
    def test_a_document_declaring_nonsense_stops_the_build(
        self, tmp_path: Path, connection: Any
    ) -> None:
        # An unknown layer is not a reason to launder the passage into a fact.
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "odd.md").write_text(
            "---\nlayer: vibes\n---\n\n# 記録\n\n陶芸について。\n",
            encoding="utf-8",
            newline="",
        )
        store, index = SqliteDocumentStore(connection), FtsIndex(connection)
        found = walk(root)
        ingest_paths(found.files, root=root, store=store, index=index, parser_for=parser_for)

        with pytest.raises(ValueError, match="declares layer"):
            build_context(
                "陶芸",
                store=store,
                index=index,
                cost_model=CharacterCost(),
                budget=Budget.characters(2000),
            )


def test_the_adapter_needs_nothing_installed() -> None:
    """The export is a published contract, so reading it is reading JSON.

    Asserted rather than assumed: an import creeping in here would turn a file
    format into a dependency.
    """
    import ast

    source = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "tsumugi"
        / "infrastructure"
        / "adapters"
        / "kiseki.py"
    ).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            assert all(not a.name.startswith("kiseki") for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or not node.module.startswith("kiseki")
