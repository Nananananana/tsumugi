"""Reading and writing the shapes other retrieval libraries already use.

The barrier to trying this library is not the API, it is the plumbing: a
pipeline built on LangChain or LlamaIndex passes `Document` and `TextNode`
objects around, and something that speaks neither has to be wired in at both
ends before it can be evaluated at all.

These tests hold both directions against the shapes as those libraries actually
define them — objects with `.page_content` / `.text`, their dictionary forms,
and plain mappings — **without importing either**, because a dependency here
would cost the thing that makes this library easy to adopt in the first place.

The shapes are asserted from their public definitions:

    LangChain   Document(page_content=str, metadata=dict, id=str | None)
    LlamaIndex  TextNode(text=str, metadata=dict, id_=str)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tsumugi import as_documents, texts_from

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FakeLangChainDocument:
    """`page_content` and `metadata`, which is all LangChain's Document is."""

    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeLlamaIndexNode:
    """`text` and `metadata`, plus the `id_` LlamaIndex names with a trailing underscore."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id_: str = "node-1"


@pytest.fixture
def package() -> dict[str, Any]:
    """A real package, not a hand-written one.

    The fixture in `fixtures/seam/` is what the CLI actually emits and what
    `akashi` and `seam` actually read, so a change to the package shape breaks
    this file rather than being absorbed by a mock that was written to agree.
    """
    loaded: dict[str, Any] = json.loads(
        (ROOT / "fixtures" / "seam" / "context-package.json").read_text(encoding="utf-8")
    )
    return loaded


class TestReadingTheirDocuments:
    def test_a_langchain_document_object(self) -> None:
        pages = [FakeLangChainDocument("the tent weighs 2.4kg", {"source": "gear.md"})]
        assert texts_from(pages) == [("gear.md", "the tent weighs 2.4kg")]

    def test_a_llamaindex_node_object(self) -> None:
        nodes = [FakeLlamaIndexNode("the tent weighs 2.4kg", {"file_path": "gear.md"})]
        assert texts_from(nodes) == [("gear.md", "the tent weighs 2.4kg")]

    def test_either_ones_dictionary_form(self) -> None:
        """`Document.model_dump()` and a node's `.dict()` are both mappings.

        Anything that crossed a queue or a cache arrives as a dict, and a
        consumer should not have to reconstruct objects to hand them over.
        """
        assert texts_from([{"page_content": "a", "metadata": {"source": "x.md"}}]) == [
            ("x.md", "a")
        ]
        assert texts_from([{"text": "b", "metadata": {"source": "y.md"}}]) == [("y.md", "b")]

    def test_a_document_with_no_source_still_gets_a_name(self) -> None:
        """A made-up name beats a crash halfway through a corpus."""
        ((path, text),) = texts_from([FakeLangChainDocument("orphan")])
        assert text == "orphan"
        assert path, "a document with no source got an empty path"

    def test_a_document_with_no_text_raises_rather_than_being_skipped(self) -> None:
        """Skipping is how a corpus quietly gets smaller than its folder.

        The alternative -- dropping it -- would make an ingest report a count
        nobody can reconcile with the number of files, which is this
        repository's own named failure class.
        """
        with pytest.raises(ValueError) as raised:
            texts_from([{"metadata": {"source": "empty.md"}}])
        assert "page_content" in str(raised.value)


class TestWritingDocumentsTheyAccept:
    def test_every_item_becomes_a_document_with_both_names(self, package: dict[str, Any]) -> None:
        """`page_content` for LangChain and `text` for LlamaIndex, on each one.

        A consumer should not have to know which library this package was
        converted for.
        """
        documents = as_documents(package)
        assert documents, "the fixture package has no items to convert"
        for document in documents:
            assert document["page_content"] == document["text"]
            assert isinstance(document["metadata"], dict)

    def test_the_anchor_survives_into_the_metadata(self, package: dict[str, Any]) -> None:
        """The reason to use this rather than a retriever.

        A passage without its anchor is a string. With it, a consumer can call
        `verify` on an answer their own chain produced -- which is the whole
        proposition, and it has to survive the conversion or the conversion is
        a slower way to get the same strings.
        """
        passage = next(d for d in as_documents(package) if d["page_content"])
        metadata = passage["metadata"]
        for name in ("document_id", "start", "end", "text_hash", "document_hash"):
            assert metadata.get(name) is not None, f"the anchor lost {name}"
        assert metadata["source"], "no source for a chain to render as a citation"

    def test_the_omissions_travel_too(self, package: dict[str, Any]) -> None:
        """ADR-0005: a package is what was sent *and* what was not.

        A conversion that dropped them would hand a pipeline the half of the
        package that looks like every other retriever's output.
        """
        omissions = [d for d in as_documents(package) if d["metadata"]["kind"] == "omission"]
        assert omissions, "the fixture has omissions and none survived"
        assert all(d["page_content"] == "" for d in omissions), (
            "an omission carried text, so concatenating page_content would send "
            "the reason to a model as evidence"
        )
        assert all(d["metadata"]["reason"] for d in omissions)

    def test_they_can_be_left_out_by_a_caller_that_knows_they_exist(
        self, package: dict[str, Any]
    ) -> None:
        assert not [
            d for d in as_documents(package, omissions=False) if d["metadata"]["kind"] == "omission"
        ]


class TestTheRoundTrip:
    def test_what_this_writes_is_what_it_reads(self, package: dict[str, Any]) -> None:
        """Out and back in, through the two functions a consumer would use.

        Not a symmetry for its own sake: a pipeline that retrieves with tsumugi,
        post-processes with LangChain and re-ingests the result is an ordinary
        arrangement, and it must not lose the source on the way round.
        """
        documents = as_documents(package, omissions=False)
        recovered = texts_from(documents)
        assert len(recovered) == len(documents)
        for (source, text), document in zip(recovered, documents, strict=True):
            assert text == document["page_content"]
            assert source == document["metadata"]["source"]


class TestAgainstTheRealLibraries:
    """The claim is about *their* shapes, so it is held against their classes.

    Everything above uses stand-ins, which test this repository's idea of a
    `Document`. That is the wrong thing to test on its own: `Document` and
    `TextNode` belong to other people, and other people change shapes. Both are
    in `[dev]` for this reason and nothing in `src/` imports either.
    """

    def test_langchain_accepts_a_converted_package_as_it_comes(
        self, package: dict[str, Any]
    ) -> None:
        """`Document(**page)` with no filtering, which is how anyone would write it."""
        from langchain_core.documents import Document

        pages = as_documents(package)
        documents = [Document(**page) for page in pages]
        assert len(documents) == len(pages)
        cited = next(d for d in documents if d.page_content)
        assert cited.metadata["document_id"] and cited.metadata["source"]

    def test_llamaindex_accepts_it_too(self, package: dict[str, Any]) -> None:
        from llama_index.core.schema import TextNode

        nodes = [TextNode(**page) for page in as_documents(package)]
        assert any(node.text for node in nodes)
        carried = next(node for node in nodes if node.text)
        assert carried.metadata["text_hash"], "the anchor did not survive into a TextNode"

    def test_their_objects_are_read_back(self) -> None:
        from langchain_core.documents import Document
        from llama_index.core.schema import TextNode

        assert texts_from([Document(page_content="a", metadata={"source": "x.md"})]) == [
            ("x.md", "a")
        ]
        assert texts_from([TextNode(text="b", metadata={"file_path": "y.md"})]) == [("y.md", "b")]
