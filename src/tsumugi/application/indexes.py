"""What indexes this process can reach, and how much is in each.

`sora` is building profiles: one machine, several people, and switching swaps
the corpus. Its screen wants to say *"this person's record: personal (1,234
documents)"*, and until now the only way to find out whether a name worked was
to use it and read the error.

**Names and counts, never paths.** The same rule the tool boundary already
follows: the person who starts the process decides which corpora it may see,
and nothing downstream gets to learn where they are on disk. A count and a
date say enough to show that a switch took effect.

An index that cannot be opened is a **row**, not an exception. A profile whose
file has been deleted should appear as unavailable next to the ones that work;
failing the whole listing would take the working ones down with it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..errors import TsumugiError

__all__ = ["CONTRACT", "IndexSummary", "summarise_indexes"]

#: Named so a consumer can write it down, and `-draft` because it is not
#: frozen. `sora` holds contract names in a list of what it will read.
CONTRACT = "tsumugi.indexes/1-draft"


@dataclass(frozen=True, slots=True)
class IndexSummary:
    """One named index: what it is called, and what is in it."""

    name: str
    #: ``None`` when the index could not be opened.
    documents: int | None
    #: When anything was last ingested, ISO 8601. ``None`` for an empty or
    #: unopenable index.
    ingested_at: str | None
    #: The *kind* of failure that stopped it opening, or ``""`` when it opened.
    #:
    #: **The kind and nothing else, because the message carries the path.**
    #: `StorageError: no index at /home/ada/.tsumugi/personal.db; run ...` names
    #: a file, and the whole point of addressing indexes by name is that a
    #: caller never learns where they are. The first version of this passed the
    #: exception's message straight through and put the path in the listing.
    #:
    #: A caller can act on the kind, which is all it needs: `StorageError`
    #: means this profile has no index yet. The person who configured the paths
    #: can see them in their own configuration, and `tsumugi doctor` prints
    #: them for the one index it was pointed at.
    unavailable: str = ""

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "documents": self.documents,
            "ingested_at": self.ingested_at,
        }
        if self.unavailable:
            payload["unavailable"] = self.unavailable
        return payload


def summarise_indexes(
    names: Sequence[str],
    open_index: Callable[[str], sqlite3.Connection],
) -> list[IndexSummary]:
    """One row per name, in the order given, whether or not each one opens.

    ``open_index`` is passed in rather than a path, so this never learns where
    an index lives -- the composition root resolves a name and hands back an
    open connection.
    """
    summaries: list[IndexSummary] = []
    for name in names:
        try:
            connection = open_index(name)
            row = connection.execute(
                "SELECT COUNT(*) AS n, MAX(ingested_at) AS latest FROM documents "
                "WHERE is_current = 1"
            ).fetchone()
        except (TsumugiError, sqlite3.DatabaseError, OSError) as error:
            summaries.append(
                IndexSummary(
                    name=name,
                    documents=None,
                    ingested_at=None,
                    unavailable=type(error).__name__,
                )
            )
            continue
        summaries.append(
            IndexSummary(name=name, documents=int(row["n"]), ingested_at=row["latest"])
        )
    return summaries
