"""Checking the disk, cheaply.

Hashing every candidate document on every query would be the obvious
implementation and a slow one. Size is a free pre-check that settles most
edits: a file whose byte count changed is certainly different, and one whose
byte count matches is *probably* the same but has to be hashed to be sure.

Results are cached per instance, keyed by path and by the stored version, so a
package that draws several passages from one document reads it once.
"""

from __future__ import annotations

import codecs
from collections.abc import Mapping
from pathlib import Path

from ..domain.document import Document
from ..domain.hashing import ContentHash
from ..ports.store import DocumentStore as RemembersRoots

__all__ = ["FilesystemFreshness", "NeverStale", "remembered_roots"]


class NeverStale:
    """Says everything is current. For a caller with no corpus to check.

    Named for what it does rather than for what it is, so that a package built
    with it says so in its providers and nobody reads "no stale anchors" as
    "nothing was stale".
    """

    name = "freshness/unchecked"

    def is_current(self, document: Document) -> bool:
        return True


class FilesystemFreshness:
    """Satisfies :class:`~tsumugi.ports.freshness.FreshnessCheck`.

    With no ``root``, each document is looked for under the corpus it was
    ingested from, which the index records. That is what makes staleness
    checking the default rather than a flag the caller has to remember -- and
    remembering it is exactly what nobody does.

    A ``root`` overrides that, for a corpus that has been moved.
    """

    name = "freshness/filesystem@1"

    def __init__(self, root: Path | None = None, *, roots: Mapping[str, str] | None = None) -> None:
        self._root = Path(root) if root is not None else None
        self._roots = dict(roots or {})
        self._answers: dict[tuple[str, str], bool] = {}

    def is_current(self, document: Document) -> bool:
        key = (document.source_path, str(document.version))
        cached = self._answers.get(key)
        if cached is not None:
            return cached

        answer = self._look(document)
        self._answers[key] = answer
        return answer

    def _root_for(self, document: Document) -> Path | None:
        if self._root is not None:
            return self._root
        remembered = self._roots.get(document.document_id)
        return Path(remembered) if remembered else None

    def _look(self, document: Document) -> bool:
        root = self._root_for(document)
        if root is None:
            # The index does not record where this came from -- ingested under
            # an older schema. "Cannot check" is answered as current, because
            # reporting stale would be a claim nothing supports.
            return True
        path = root / document.source_path
        try:
            raw = path.read_bytes()
        except OSError:
            # A missing or unreadable file is a different problem from a
            # changed one, and `tsumugi doctor` is where it belongs. Reporting
            # "stale" here would mark every document on an unmounted drive as
            # historical.
            return True

        stored = document.content.encode("utf-8")
        # Free, and settles most edits. The byte order mark is allowed for:
        # ingest strips it, so a BOM'd file is three bytes longer than the text
        # it produced -- and comparing sizes naively would mark every such file
        # permanently stale.
        if len(raw) not in {len(stored), len(stored) + len(codecs.BOM_UTF8)}:
            return False

        for encoding in ("utf-8-sig", "utf-8"):
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            return ContentHash.of(text, document.version.algorithm) == document.version
        return True


def remembered_roots(store: RemembersRoots) -> FilesystemFreshness:
    """A check that looks for each document under the corpus it came from.

    The index records the root at ingest, so staleness checking needs no flag.
    That matters more than it sounds: a check the caller has to remember to
    turn on is a check that is off, and ADR-0010's whole point is that evidence
    from an edited file must not be offered as current.
    """
    roots = {
        document.document_id: root
        for document in store.all_current()
        if (root := store.corpus_root_of(document.document_id)) is not None
    }
    return FilesystemFreshness(roots=roots)
