"""Speaking the shapes other retrieval libraries already speak.

Most people meeting this library already have a pipeline. It is built on
LangChain or LlamaIndex, it passes `Document` or `TextNode` objects around, and
the cost of trying something new is rewriting the plumbing at both ends. This
module removes that cost in both directions:

    documents  ->  tsumugi          `texts_from(...)`  reads their shape
    tsumugi    ->  documents        `as_documents(...)` writes it

**Without importing either of them.** LangChain's `Document` is
``page_content`` and ``metadata``; LlamaIndex's `TextNode` is ``text`` and
``metadata``; both accept plain mappings and both hand you objects with those
attributes. So this reads attributes when they are there and keys when they are
not, and emits dictionaries that either constructor accepts:

    from langchain_core.documents import Document
    Document(**page) for page in tsumugi.as_documents(package)

A dependency would buy nothing here and would cost the thing this library is
for: `pip install tsumugi` still installs nothing, and a version bump in
somebody else's package cannot break a consumer of this one.

**What survives the trip is the point.** A retriever hands a pipeline text and
a source. This hands it the anchor -- document id, offsets, both hashes -- so a
consumer can call `verify` on an answer their own chain produced, from a
`Document` their own chain carried. Losing that would make this a slower way to
get the same strings.

And `omissions` travels too, as documents with ``kind: "omission"`` and no
text, because a pipeline that silently drops what was left out has thrown away
the half of the package that makes it a package (ADR-0005). A consumer that
does not want them filters on ``kind``; a consumer that does not know they
exist gets told.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

__all__ = ["ANCHOR_FIELDS", "as_documents", "texts_from"]

#: The anchor keys copied into a document's metadata. Flat, because both
#: libraries' metadata is a flat mapping in practice and a nested anchor is the
#: first thing a serialiser somewhere will drop.
ANCHOR_FIELDS: Final = (
    "document_id",
    "source_path",
    "section",
    "start",
    "end",
    "text_hash",
    "document_hash",
)


def _field(item: Any, *names: str) -> Any:
    """The first of ``names`` present, whether it is an attribute or a key.

    A `Document` is an object with `.page_content`; a dict from
    `.model_dump()`, a `TextNode`, and a hand-written mapping are all
    subscriptable instead. Duck-typing both is what lets this file import
    nothing.
    """
    for name in names:
        if isinstance(item, Mapping):
            if name in item:
                return item[name]
        elif hasattr(item, name):
            return getattr(item, name)
    return None


def texts_from(documents: Iterable[Any]) -> list[tuple[str, str]]:
    """``(source_path, text)`` for each document, whatever library made it.

    Accepts LangChain `Document`, LlamaIndex `TextNode`, either one's
    dictionary form, or a plain mapping. The path is taken from the metadata
    keys those ecosystems already use -- ``source`` is LangChain's convention
    from every loader it ships -- and falls back to an index, because a
    document with no source still has to be ingestible and a made-up name is
    better than a crash halfway through a corpus.

    Raises on a document with no text at all rather than skipping it: a loader
    that silently drops empty pages is how a corpus quietly gets smaller than
    the folder it came from.
    """
    found: list[tuple[str, str]] = []
    for position, document in enumerate(documents):
        text = _field(document, "page_content", "text", "content")
        if text is None:
            raise ValueError(
                f"document {position} has no page_content, text or content; "
                "it cannot be ingested and skipping it would shrink the corpus silently"
            )
        metadata = _field(document, "metadata", "extra_info") or {}
        source = (
            _field(metadata, "source", "source_path", "file_path", "path")
            or _field(document, "id_", "id")
            or f"document-{position:04d}"
        )
        found.append((str(source), str(text)))
    return found


def as_documents(package: Mapping[str, Any], *, omissions: bool = True) -> list[dict[str, Any]]:
    """A ContextPackage as documents a LangChain or LlamaIndex pipeline accepts.

    Takes the package's dictionary form -- ``package.to_dict()`` or the JSON
    the CLI emits -- rather than the object, so a consumer who received one over
    the wire can convert it without importing this library's domain.

    Every document carries ``page_content`` **and** ``text``: the two
    ecosystems disagree about the name and a consumer should not have to know
    which one this came from.
    """
    items = package.get("items") or []
    query = package.get("query", "")
    contract = package.get("contract", "")

    documents: list[dict[str, Any]] = []
    for item in items:
        anchor = item.get("anchor") or {}
        selection = item.get("selection") or {}
        metadata: dict[str, Any] = {
            "kind": item.get("kind", "passage"),
            "item_id": item.get("item_id"),
            "score": selection.get("score"),
            "rank": selection.get("rank"),
            # The signals are why this passage is here. A pipeline that shows a
            # user retrieved context can show the reason with it.
            "signals": list(selection.get("signals") or ()),
            "contract": contract,
            "query": query,
            # LangChain's loaders all write `source`, so a chain that already
            # renders citations will render these without being changed.
            "source": anchor.get("source_path") or anchor.get("document_id"),
        }
        metadata.update({name: anchor.get(name) for name in ANCHOR_FIELDS})
        text = item.get("text", "")
        documents.append({"page_content": text, "text": text, "metadata": metadata})

    if not omissions:
        return documents

    for left_out in package.get("omissions") or []:
        anchor = left_out.get("anchor") or {}
        metadata = {
            "kind": "omission",
            "rule": left_out.get("rule"),
            "reason": left_out.get("reason"),
            "contract": contract,
            "query": query,
            "source": anchor.get("source_path") or anchor.get("document_id"),
        }
        metadata.update({name: anchor.get(name) for name in ANCHOR_FIELDS})
        # Empty text on purpose: an omission is not context, and a pipeline
        # that concatenates `page_content` must not accidentally send the
        # reason to a model as though it were evidence.
        documents.append({"page_content": "", "text": "", "metadata": metadata})
    return documents
