"""A lead is offered, is never evidence, and stops when there is evidence.

`application/leads.py` exists because "no confirmed evidence" is true and
useless, and because the passage a reader wanted was already named in the
package's omissions with nothing to fetch it.

The tests that matter here are the ones about what a lead is *not*. Three
separate mistakes would each turn this feature into the thing ADR-0022
refused:

1. offering leads beside real items, so an unsupported passage joins an
   evidence list;
2. offering every near miss, which the measurement says is a coin flip;
3. offering a near-duplicate or a budget casualty as though it were a near
   miss, when the reader either has it already or can raise the budget.

**Every one of those has a positive control**, and that is not decoration.
The first version of this file asserted `leads_from(...) == []` against the
empty `store` fixture, so every lead was dropped because its document could
not be fetched -- and the file passed unchanged with both central guards
deleted from the module. A test that cannot say *why* it saw nothing has not
checked the rule it names.
"""

from __future__ import annotations

import pytest

from tests.helpers import build_document
from tsumugi.application.leads import DEFAULT_LIMIT, Lead, leads_from
from tsumugi.domain.anchor import Anchor
from tsumugi.domain.budget import Budget
from tsumugi.domain.document import Document
from tsumugi.domain.hashing import ContentHash
from tsumugi.domain.omission import Omission, OmissionRule
from tsumugi.domain.package import BudgetReport, ContextPackage, PackageProvenance
from tsumugi.domain.selection import ContextItem, SelectionTrace
from tsumugi.domain.span import Span
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

#: Long enough that every synthetic span slices real text out of it.
STORED_TEXT = "a passage that was found and never confirmed, sitting in a document"


class TestALeadIsNotEvidence:
    def test_a_package_with_items_is_offered_no_leads(self, store: SqliteDocumentStore) -> None:
        """The exact shape ADR-0022 refused, and the reason for the default.

        Beside a real item, an unconfirmed passage is a mark on an evidence
        list, and the argument against that is measured rather than aesthetic.

        The omission here is offerable and its document is in the store, so it
        *would* come back if the guard were gone. The second assertion is what
        makes the first one mean something.
        """
        _stored(store, "near.md")
        package = _package_of((_item(),), (_omission(OmissionRule.BELOW_THRESHOLD, "near.md"),))

        assert leads_from(package, store) == []
        assert leads_from(package, store, only_when_empty=False), (
            "the omission must be offerable, or the assertion above proves nothing"
        )

    def test_a_caller_can_ask_anyway_and_has_to_say_so(self, store: SqliteDocumentStore) -> None:
        _stored(store, "near.md")
        package = _package_of((_item(),), (_omission(OmissionRule.BELOW_THRESHOLD, "near.md"),))
        asked = leads_from(package, store, only_when_empty=False)
        assert [lead.source_path for lead in asked] == ["near.md"]
        assert all(isinstance(lead, Lead) for lead in asked)

    def test_a_lead_carries_no_text_hash_to_verify_against(self) -> None:
        """It cannot be verified, and it does not pretend it can.

        A `Lead` with a `text_hash` would be a `ContextItem` under another
        name, and something downstream would eventually treat it as one.
        """
        assert not hasattr(Lead, "text_hash")
        assert "text_hash" not in Lead.__annotations__


class TestWhatIsWorthOffering:
    def test_the_default_is_one(self) -> None:
        """Measured: the second lead adds 21.7 points of risk and no recall."""
        assert DEFAULT_LIMIT == 1

    @pytest.mark.parametrize(
        "rule", [OmissionRule.BUDGET_EXHAUSTED, OmissionRule.REDUNDANT_CANDIDATE]
    )
    def test_a_rule_that_is_not_a_near_miss_is_not_a_lead(
        self, store: SqliteDocumentStore, rule: OmissionRule
    ) -> None:
        """A budget casualty or a near-duplicate is a different conversation.

        The reader can raise the budget, or has the passage already. The
        document is in the store either way, so the rule is the only thing
        keeping these out -- which the second assertion demonstrates by
        putting an offerable rule on the same document.
        """
        _stored(store, "a.md")
        assert leads_from(_package_of((), (_omission(rule, "a.md"),)), store) == []
        assert leads_from(
            _package_of((), (_omission(OmissionRule.BELOW_THRESHOLD, "a.md"),)), store
        ), "an offerable rule on the same document must come back, or nothing is being tested"

    def test_zero_and_negative_limits_offer_nothing(self, store: SqliteDocumentStore) -> None:
        _stored(store, "a.md")
        package = _package_of((), (_omission(OmissionRule.BELOW_THRESHOLD, "a.md"),))
        assert leads_from(package, store), "the default must offer it, or the limits prove nothing"
        assert leads_from(package, store, limit=0) == []
        assert leads_from(package, store, limit=-1) == []

    def test_a_document_that_has_gone_is_skipped_rather_than_raised(
        self, store: SqliteDocumentStore
    ) -> None:
        """A hint that cannot be fetched must not cost the caller their answer."""
        package = _package_of((), (_omission(OmissionRule.BELOW_THRESHOLD, "never-stored.md"),))
        assert leads_from(package, store) == []

    def test_an_omission_with_no_score_never_ranks(self, store: SqliteDocumentStore) -> None:
        """`score=None` means it never reached ranking (a filtered file).

        Treating that as 0.0 would put an unjudged passage in a queue ordered
        by judgement.
        """
        _stored(store, "a.md")
        unscored = Omission(
            rule=OmissionRule.BELOW_THRESHOLD,
            reason="unconfirmed",
            document_id=_id("a.md"),
            span=Span(0, len(STORED_TEXT)),
            source_path="a.md",
            score=None,
        )
        assert leads_from(_package_of((), (unscored,)), store) == []


class TestItIsTheSameTwice:
    def test_the_best_scoring_omission_comes_first(self, store: SqliteDocumentStore) -> None:
        _stored(store, "doc_low.md", "doc_high.md")
        package = _package_of(
            (),
            (
                _omission(OmissionRule.BELOW_THRESHOLD, "doc_low.md", score=1.0),
                _omission(OmissionRule.BELOW_THRESHOLD, "doc_high.md", score=9.0),
            ),
        )
        ordered = leads_from(package, store, limit=2)
        assert [lead.source_path for lead in ordered] == ["doc_high.md", "doc_low.md"]

    def test_two_calls_give_the_same_leads_in_the_same_order(
        self, store: SqliteDocumentStore
    ) -> None:
        """ADR-0003 stops at the package boundary otherwise.

        Equal scores on purpose: the tie is where an unstable sort would show,
        and a package built twice from the same corpus is full of them.
        """
        _stored(store, "a.md", "b.md", "c.md")
        package = _package_of(
            (),
            tuple(
                _omission(OmissionRule.BELOW_THRESHOLD, name, score=4.0)
                for name in ("c.md", "a.md", "b.md")
            ),
        )
        first = [lead.source_path for lead in leads_from(package, store, limit=3)]
        assert first == [lead.source_path for lead in leads_from(package, store, limit=3)]
        assert len(first) == 3, "all three must be offered, or the ordering is untested"


def _stored(store: SqliteDocumentStore, *source_paths: str) -> SqliteDocumentStore:
    """Put real documents behind the synthetic omissions.

    Not a detail: with an empty store every lead is dropped because its
    document could not be fetched, so a test asserting `== []` passes without
    the rule it names ever being consulted.
    """
    for source_path in source_paths:
        store.put(build_document(source_path, STORED_TEXT), corpus_root="/tmp")
    return store


def _id(source_path: str) -> str:
    return Document.identity_for(source_path)


def _omission(rule: OmissionRule, source_path: str, score: float = 9.0) -> Omission:
    return Omission(
        rule=rule,
        reason="the index proposed this and confirmation could not support it",
        document_id=_id(source_path),
        span=Span(0, len(STORED_TEXT)),
        source_path=source_path,
        score=score,
    )


def _item() -> ContextItem:
    text = "the unit is explicit at the call site"
    return ContextItem(
        item_id="itm_001",
        text=text,
        anchor=Anchor(
            document_id=_id("kept.md"),
            span=Span(0, len(text)),
            text_hash=ContentHash.of(text),
            version=ContentHash.of(text),
        ),
        source_path="kept.md",
        cost=len(text),
        selection=SelectionTrace(rank=1, score=1.0, signals=("lexical",)),
    )


def _package_of(items: tuple[ContextItem, ...], omissions: tuple[Omission, ...]) -> ContextPackage:
    return ContextPackage(
        query="anything",
        items=items,
        omissions=omissions,
        budget=BudgetReport(
            budget=Budget.characters(1200),
            estimate=sum(item.cost for item in items),
            estimator="chars",
        ),
        provenance=PackageProvenance(tsumugi_version="test"),
    )
