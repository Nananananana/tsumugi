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
from pathlib import Path

from ..domain.document import Document
from ..domain.hashing import ContentHash

__all__ = ["FilesystemFreshness", "NeverStale"]


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
    """Satisfies :class:`~tsumugi.ports.freshness.FreshnessCheck`."""

    name = "freshness/filesystem@1"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._answers: dict[tuple[str, str], bool] = {}

    def is_current(self, document: Document) -> bool:
        key = (document.source_path, str(document.version))
        cached = self._answers.get(key)
        if cached is not None:
            return cached

        answer = self._look(document)
        self._answers[key] = answer
        return answer

    def _look(self, document: Document) -> bool:
        path = self._root / document.source_path
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
