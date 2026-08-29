"""The return path: what was sent, and afterwards, what was used.

Two rules are the decision rather than implementation detail, and most of this
file is about them: the ledger holds **no text**, and it is **derived data**
that never feeds back into a build (ADR-0011).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tsumugi.domain.anchor import Anchor
from tsumugi.domain.budget import Budget
from tsumugi.domain.claim import VerificationReport, verify_claims
from tsumugi.domain.omission import Omission, OmissionRule
from tsumugi.domain.package import BudgetReport, ContextPackage, PackageProvenance
from tsumugi.domain.selection import ContextItem, SelectionTrace
from tsumugi.domain.span import Span
from tsumugi.infrastructure.storage.database import connect
from tsumugi.infrastructure.storage.ledger import SqliteLedger
from tsumugi.ports.ledger import LedgerStore

from .helpers import build_document

SECRET_QUERY = "ZZQX-what-did-the-doctor-say-7731"
SECRET_TEXT = "YYPL-the-private-sentence-in-my-notes-4402"
DOCUMENT = build_document(
    "notes/private.md",
    f"# note\n\n{SECRET_TEXT} and more besides. " + "padding for spans. " * 20,
)


def a_package(query: str = SECRET_QUERY, *, items: int = 2) -> ContextPackage:
    selected = tuple(
        ContextItem(
            item_id=f"itm_{n:03d}",
            text=DOCUMENT.content[n * 10 : n * 10 + 8],
            anchor=Anchor.into(DOCUMENT, Span(n * 10, n * 10 + 8)),
            source_path=DOCUMENT.source_path,
            selection=SelectionTrace(rank=n + 1, score=0.9 - n * 0.1),
            cost=8,
        )
        for n in range(items)
    )
    return ContextPackage(
        query=query,
        items=selected,
        omissions=(
            Omission(OmissionRule.BUDGET_EXHAUSTED, "no room", "doc_x", Span(0, 5), cost=99),
            Omission(OmissionRule.BELOW_THRESHOLD, "scored 0.01", "doc_y", Span(0, 5)),
        ),
        budget=BudgetReport(Budget.characters(1000), 8 * items, "characters@1"),
        provenance=PackageProvenance(tsumugi_version="0.1.0.dev0"),
        created_at="2026-08-30T10:00:00+00:00",
    )


def ledger_for(tmp_path: Path) -> tuple[SqliteLedger, sqlite3.Connection]:
    connection = connect(tmp_path / "ledger.db")
    return SqliteLedger(connection), connection


class TestOpening:
    def test_it_satisfies_the_port(self, tmp_path: Path) -> None:
        ledger, _ = ledger_for(tmp_path)
        assert isinstance(ledger, LedgerStore)

    def test_building_a_package_records_it(self, tmp_path: Path) -> None:
        ledger, _ = ledger_for(tmp_path)
        entry = ledger.open(a_package())

        assert entry.items == 2
        assert entry.omissions == 2
        assert not entry.closed
        assert len(ledger.entries()) == 1

    def test_opening_the_same_package_twice_records_it_once(self, tmp_path: Path) -> None:
        # A package id is a hash of its inputs, so building the same package
        # twice is the same event. Counting it twice would overstate what was
        # sent.
        ledger, _ = ledger_for(tmp_path)
        ledger.open(a_package())
        ledger.open(a_package())
        assert len(ledger.entries()) == 1

    def test_different_questions_are_different_entries(self, tmp_path: Path) -> None:
        ledger, _ = ledger_for(tmp_path)
        ledger.open(a_package("one question"))
        ledger.open(a_package("another question"))
        assert len(ledger.entries()) == 2


class TestClosing:
    def test_verifying_records_which_items_were_cited(self, tmp_path: Path) -> None:
        ledger, _ = ledger_for(tmp_path)
        package = ledger_package = a_package()
        ledger.open(package)

        report = verify_claims(
            [("a claim", [ledger_package.items[0].text])],
            package.items,
            package_id=str(package.package_id),
        )
        assert ledger.close(report) is True

        entry = ledger.entries()[0]
        assert entry.closed
        assert entry.cited_items == 1
        assert entry.unused_items == 1

    def test_closing_a_package_that_was_never_opened_is_not_an_error(self, tmp_path: Path) -> None:
        # Verifying against a package built elsewhere is legitimate.
        ledger, _ = ledger_for(tmp_path)
        package = a_package()
        report = verify_claims([("x", [])], package.items, package_id=str(package.package_id))
        assert ledger.close(report) is False

    def test_a_report_with_no_package_id_closes_nothing(self, tmp_path: Path) -> None:
        ledger, _ = ledger_for(tmp_path)
        ledger.open(a_package())
        assert ledger.close(VerificationReport.of([])) is False

    def test_an_unused_item_is_visible(self, tmp_path: Path) -> None:
        # The number the project exists to reduce.
        ledger, _ = ledger_for(tmp_path)
        package = a_package(items=4)
        ledger.open(package)
        report = verify_claims(
            [("x", [package.items[0].text])], package.items, package_id=str(package.package_id)
        )
        ledger.close(report)
        assert ledger.entries()[0].unused_items == 3


class TestUsage:
    def test_an_unverified_ledger_declines_to_say_what_was_used(self, tmp_path: Path) -> None:
        # Reporting 100% unused for a ledger nobody closed would be a lie about
        # the tool rather than about the corpus.
        ledger, _ = ledger_for(tmp_path)
        ledger.open(a_package())
        usage = ledger.usage()

        assert usage.packages == 1
        assert usage.closed == 0
        assert usage.uncited_share is None

    def test_it_reports_the_uncited_share_once_something_is_verified(self, tmp_path: Path) -> None:
        ledger, _ = ledger_for(tmp_path)
        package = a_package(items=4)
        ledger.open(package)
        report = verify_claims(
            [("x", [package.items[0].text])], package.items, package_id=str(package.package_id)
        )
        ledger.close(report)

        usage = ledger.usage()
        assert usage.items_sent == 4
        assert usage.items_cited == 1
        assert usage.uncited_share == 0.75

    def test_open_packages_do_not_count_towards_the_share(self, tmp_path: Path) -> None:
        # An open package has not been checked; counting its items as uncited
        # would blame the corpus for the caller not having verified.
        ledger, _ = ledger_for(tmp_path)
        closed = a_package("closed one", items=2)
        ledger.open(closed)
        ledger.open(a_package("never verified", items=8))
        ledger.close(
            verify_claims(
                [("x", [closed.items[0].text])],
                closed.items,
                package_id=str(closed.package_id),
            )
        )
        assert ledger.usage().items_sent == 2

    def test_budget_pressure_is_counted_separately(self, tmp_path: Path) -> None:
        ledger, _ = ledger_for(tmp_path)
        ledger.open(a_package())
        usage = ledger.usage()
        assert usage.omissions == 2
        assert usage.budget_exhausted == 1

    def test_since_filters(self, tmp_path: Path) -> None:
        ledger, _ = ledger_for(tmp_path)
        ledger.open(a_package("old"), at="2026-01-01T00:00:00+00:00")
        ledger.open(a_package("new"), at="2026-08-30T00:00:00+00:00")
        assert len(ledger.entries(since="2026-06-01T00:00:00+00:00")) == 1


class TestItHoldsNoText:
    """The rule that lets the ledger default to on."""

    def test_the_query_is_hashed_not_stored(self, tmp_path: Path) -> None:
        ledger, _ = ledger_for(tmp_path)
        ledger.open(a_package())

        entry = ledger.entries()[0]
        assert SECRET_QUERY not in entry.query_hash
        assert entry.query_hash.startswith("sha256:")

    def test_the_same_question_hashes_the_same_way(self, tmp_path: Path) -> None:
        # Enough to group repeats, which is all the ledger needs.
        ledger, _ = ledger_for(tmp_path)
        first = ledger.open(a_package("同じ問い", items=1))
        second = ledger.open(a_package("同じ問い", items=2))
        assert first.query_hash == second.query_hash

    def test_no_query_or_document_text_reaches_the_database_file(self, tmp_path: Path) -> None:
        # The whole claim, checked the only way worth checking it: grep the
        # file. A list of what someone asked is revealing even with no
        # documents in it, so neither may be there.
        path = tmp_path / "ledger.db"
        connection = connect(path)
        ledger = SqliteLedger(connection)
        package = a_package()
        ledger.open(package)
        ledger.close(
            verify_claims(
                [("x", [package.items[0].text])],
                package.items,
                package_id=str(package.package_id),
            )
        )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
        connection.close()

        raw = path.read_bytes()
        assert SECRET_QUERY.encode("utf-8") not in raw
        assert SECRET_TEXT.encode("utf-8") not in raw

    def test_the_tables_have_no_text_columns_beyond_identifiers(self, tmp_path: Path) -> None:
        # A schema check, so that adding a `query TEXT` column later is a test
        # failure rather than a quiet change of what the ledger is.
        _, connection = ledger_for(tmp_path)
        allowed = {
            # created_at and verified_at are timestamps; query_hash is a hash.
            # None of them is text from the corpus or from the question.
            "ledger": {"package_id", "created_at", "verified_at", "query_hash", "unit"},
            "ledger_items": {"package_id", "item_id", "document_id"},
            "ledger_omissions": {"package_id", "document_id", "rule"},
        }
        for table, permitted in allowed.items():
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info('{table}')")
                if row[2].upper() == "TEXT"
            }
            assert columns <= permitted, f"{table} gained a text column: {columns - permitted}"


class TestItIsDerived:
    def test_forgetting_leaves_nothing(self, tmp_path: Path) -> None:
        ledger, _ = ledger_for(tmp_path)
        ledger.open(a_package())
        assert ledger.forget() == 1
        assert ledger.entries() == []
        assert ledger.usage().packages == 0

    def test_forgetting_costs_history_and_nothing_else(self, tmp_path: Path) -> None:
        # The store and the index are untouched: the ledger is a record of
        # questions, not of the corpus.
        connection = connect(tmp_path / "ledger.db")
        ledger = SqliteLedger(connection)
        ledger.open(a_package())
        before = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        ledger.forget()
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == before
