"""The ledger, on SQLite.

Same file as the index: one thing to back up, one thing to delete. It holds no
text at all -- identifiers, offsets, scores and counts (ADR-0011) -- so
deleting it costs history and nothing else.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from ...domain.claim import VerificationReport
from ...domain.hashing import ContentHash
from ...domain.package import ContextPackage
from ...domain.usage import LedgerEntry, Usage

__all__ = ["SqliteLedger"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    package_id   TEXT PRIMARY KEY,
    created_at   TEXT    NOT NULL,
    query_hash   TEXT    NOT NULL,
    unit         TEXT    NOT NULL,
    budget_limit INTEGER NOT NULL,
    estimate     INTEGER NOT NULL,
    items        INTEGER NOT NULL,
    omissions    INTEGER NOT NULL,
    verified_at  TEXT,
    cited_items  INTEGER
);
CREATE TABLE IF NOT EXISTS ledger_items (
    package_id  TEXT    NOT NULL,
    item_id     TEXT    NOT NULL,
    document_id TEXT    NOT NULL,
    start       INTEGER NOT NULL,
    end         INTEGER NOT NULL,
    score       REAL,
    cost        INTEGER NOT NULL,
    cited       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (package_id, item_id)
);
CREATE TABLE IF NOT EXISTS ledger_omissions (
    package_id  TEXT    NOT NULL,
    document_id TEXT    NOT NULL,
    start       INTEGER NOT NULL,
    end         INTEGER NOT NULL,
    rule        TEXT    NOT NULL,
    score       REAL,
    cost        INTEGER
);
CREATE INDEX IF NOT EXISTS ledger_by_time ON ledger (created_at);
"""


class SqliteLedger:
    """Satisfies :class:`~tsumugi.ports.ledger.LedgerStore`."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        with connection:
            connection.executescript(_SCHEMA)

    def open(self, package: ContextPackage, *, at: str | None = None) -> LedgerEntry:
        """Record that a package was built. Idempotent on ``package_id``.

        Idempotent because a package_id is a hash of its inputs: building the
        same package twice is the same event, and counting it twice would
        overstate what was sent.
        """
        package_id = str(package.package_id)
        entry = LedgerEntry(
            package_id=package_id,
            created_at=at or package.created_at or datetime.now(UTC).isoformat(),
            # Enough to group repeats, and not the question itself. A list of
            # what someone asked is revealing even with no documents in it.
            query_hash=str(ContentHash.of(package.query)),
            unit=package.budget.budget.unit.value,
            limit=package.budget.budget.limit,
            estimate=package.budget.estimate,
            items=len(package.items),
            omissions=len(package.omissions),
        )

        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO ledger (package_id, created_at, query_hash, unit, "
                "budget_limit, estimate, items, omissions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.package_id,
                    entry.created_at,
                    entry.query_hash,
                    entry.unit,
                    entry.limit,
                    entry.estimate,
                    entry.items,
                    entry.omissions,
                ),
            )
            self._connection.execute("DELETE FROM ledger_items WHERE package_id = ?", (package_id,))
            self._connection.executemany(
                "INSERT INTO ledger_items (package_id, item_id, document_id, start, end, "
                "score, cost) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        package_id,
                        item.item_id,
                        item.anchor.document_id,
                        item.anchor.span.start,
                        item.anchor.span.end,
                        item.selection.score if item.selection else None,
                        item.cost,
                    )
                    for item in package.items
                ],
            )
            self._connection.execute(
                "DELETE FROM ledger_omissions WHERE package_id = ?", (package_id,)
            )
            self._connection.executemany(
                "INSERT INTO ledger_omissions (package_id, document_id, start, end, rule, "
                "score, cost) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        package_id,
                        omission.document_id,
                        omission.span.start,
                        omission.span.end,
                        omission.rule.value,
                        omission.score,
                        omission.cost,
                    )
                    for omission in package.omissions
                ],
            )
        return entry

    def close(self, report: VerificationReport, *, at: str | None = None) -> bool:
        """Record which items an answer actually cited.

        Returns ``False`` when the package was never opened -- verifying
        against a package built elsewhere is legitimate and is not an error.
        """
        if not report.package_id:
            return False
        held = self._connection.execute(
            "SELECT 1 FROM ledger WHERE package_id = ?", (report.package_id,)
        ).fetchone()
        if held is None:
            return False

        cited = {
            location.item_id
            for claim in report.claims
            for citation in claim.citations
            for location in citation.locations
        }
        with self._connection:
            self._connection.execute(
                "UPDATE ledger_items SET cited = 0 WHERE package_id = ?", (report.package_id,)
            )
            if cited:
                marks = ",".join("?" * len(cited))
                self._connection.execute(
                    f"UPDATE ledger_items SET cited = 1 WHERE package_id = ? "  # noqa: S608
                    f"AND item_id IN ({marks})",
                    (report.package_id, *sorted(cited)),
                )
            self._connection.execute(
                "UPDATE ledger SET verified_at = ?, cited_items = ? WHERE package_id = ?",
                (at or datetime.now(UTC).isoformat(), len(cited), report.package_id),
            )
        return True

    def entries(self, *, since: str | None = None, limit: int = 100) -> Sequence[LedgerEntry]:
        rows = self._connection.execute(
            "SELECT * FROM ledger WHERE (? IS NULL OR created_at >= ?) "
            "ORDER BY created_at DESC, package_id LIMIT ?",
            (since, since, limit),
        ).fetchall()
        return [
            LedgerEntry(
                package_id=row["package_id"],
                created_at=row["created_at"],
                query_hash=row["query_hash"],
                unit=row["unit"],
                limit=row["budget_limit"],
                estimate=row["estimate"],
                items=row["items"],
                omissions=row["omissions"],
                verified_at=row["verified_at"],
                cited_items=row["cited_items"],
            )
            for row in rows
        ]

    def usage(self, *, since: str | None = None) -> Usage:
        row = self._connection.execute(
            "SELECT COUNT(*) AS packages, "
            "       COUNT(verified_at) AS closed, "
            "       COALESCE(SUM(items), 0) AS items_sent, "
            "       COALESCE(SUM(omissions), 0) AS omissions "
            "FROM ledger WHERE (? IS NULL OR created_at >= ?)",
            (since, since),
        ).fetchone()

        # Only closed packages count towards citation: an open one has not been
        # checked, and counting its items as uncited would blame the corpus for
        # the caller not having verified.
        cited = self._connection.execute(
            "SELECT COALESCE(SUM(i.cited), 0) AS n, COALESCE(SUM(1), 0) AS sent "
            "FROM ledger_items i JOIN ledger l ON l.package_id = i.package_id "
            "WHERE l.verified_at IS NOT NULL AND (? IS NULL OR l.created_at >= ?)",
            (since, since),
        ).fetchone()

        exhausted = self._connection.execute(
            "SELECT COUNT(*) AS n FROM ledger_omissions o JOIN ledger l "
            "ON l.package_id = o.package_id "
            "WHERE o.rule = 'budget_exhausted' AND (? IS NULL OR l.created_at >= ?)",
            (since, since),
        ).fetchone()

        return Usage(
            packages=int(row["packages"]),
            closed=int(row["closed"]),
            items_sent=int(cited["sent"] or 0),
            items_cited=int(cited["n"] or 0),
            omissions=int(row["omissions"]),
            budget_exhausted=int(exhausted["n"]),
        )

    def forget(self) -> int:
        """Delete the whole ledger. Costs history and nothing else."""
        with self._connection:
            removed = self._connection.execute("DELETE FROM ledger").rowcount
            self._connection.execute("DELETE FROM ledger_items")
            self._connection.execute("DELETE FROM ledger_omissions")
        self._connection.execute("VACUUM")
        return removed
