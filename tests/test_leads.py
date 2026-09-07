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

from dataclasses import FrozenInstanceError, replace

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


class TestTheEdgesMutationTestingFound:
    """Survivors from `python tools/mutate.py`, and what each one asked.

    Every one of these was a change to shipped code that this file did not
    object to, and two of them survived by **coincidence in the fixtures**:
    `-score` ordering and `document_id` ordering happened to agree, and an
    omission's path happened to equal its document's path. A fixture where two
    things agree cannot tell you which one is being used.

    One survivor is left deliberately: `if limit <= 0` mutated to `limit < 0`
    is an **equivalent program**. At `limit == 0` the loop's own `len(found) >=
    limit` breaks before the first append, so both spellings return `[]`. It is
    recorded here rather than chased with a test that could not fail.
    """

    def test_a_lead_carries_the_score_it_was_ranked_by(self, store: SqliteDocumentStore) -> None:
        """Nothing asserted the score, so zeroing it changed nothing."""
        _stored(store, "a.md")
        package = _package_of((), (_omission(OmissionRule.BELOW_THRESHOLD, "a.md", score=7.5),))
        (lead,) = leads_from(package, store)
        assert lead.score == 7.5

    def test_the_order_is_by_score_and_not_by_the_tiebreak(
        self, store: SqliteDocumentStore
    ) -> None:
        """Names chosen so that score order and `document_id` order disagree.

        `_ranking` is `(-score, document_id, span.start)`, so a mutant that
        zeroes the score falls through to `document_id` -- a hash of the path.
        Two arbitrary names have an even chance of hashing in the order the
        test wants, and the first version of this used a pair that did:
        `zzz.md` hashes below `aaa.md`, so ranking by nothing at all passed it.

        `red.md` hashes to `doc_fdb3...` and `two.md` to `doc_09f4...`, so
        giving the higher score to the higher hash makes the two orderings
        provably opposite. Recomputed from `Document.identity_for` in the
        assertion below, so a change to how ids are made fails here loudly
        rather than turning this back into a coin flip.
        """
        assert _id("red.md") > _id("two.md"), "the ids no longer disagree with the scores"

        _stored(store, "red.md", "two.md")
        package = _package_of(
            (),
            (
                _omission(OmissionRule.BELOW_THRESHOLD, "two.md", score=1.0),
                _omission(OmissionRule.BELOW_THRESHOLD, "red.md", score=9.0),
            ),
        )
        assert [lead.source_path for lead in leads_from(package, store, limit=2)] == [
            "red.md",
            "two.md",
        ]

    def test_the_limit_is_a_ceiling_not_an_off_by_one(self, store: SqliteDocumentStore) -> None:
        """More are offerable than are asked for, so `>=` and `>` differ."""
        _stored(store, "a.md", "b.md", "c.md")
        package = _package_of(
            (),
            tuple(
                _omission(OmissionRule.BELOW_THRESHOLD, name, score=score)
                for name, score in (("a.md", 3.0), ("b.md", 2.0), ("c.md", 1.0))
            ),
        )
        assert len(leads_from(package, store, limit=2)) == 2
        assert len(leads_from(package, store, limit=1)) == 1
        assert len(leads_from(package, store, limit=99)) == 3

    def test_the_omissions_own_path_wins_over_the_documents(
        self, store: SqliteDocumentStore
    ) -> None:
        """The fallback is a fallback, checked where the two differ.

        Every other fixture stores the document at the same path the omission
        names, so `omission.source_path or document.source_path` and its
        opposite return the same string.
        """
        _stored(store, "on-disk.md")
        renamed = Omission(
            rule=OmissionRule.BELOW_THRESHOLD,
            reason="unconfirmed",
            document_id=_id("on-disk.md"),
            span=Span(0, len(STORED_TEXT)),
            source_path="named-by-the-omission.md",
            score=9.0,
        )
        (lead,) = leads_from(_package_of((), (renamed,)), store)
        assert lead.source_path == "named-by-the-omission.md"

    def test_a_lead_with_no_path_of_its_own_falls_back_to_the_document(
        self, store: SqliteDocumentStore
    ) -> None:
        _stored(store, "on-disk.md")
        pathless = Omission(
            rule=OmissionRule.BELOW_THRESHOLD,
            reason="unconfirmed",
            document_id=_id("on-disk.md"),
            span=Span(0, len(STORED_TEXT)),
            source_path="",
            score=9.0,
        )
        (lead,) = leads_from(_package_of((), (pathless,)), store)
        assert lead.source_path == "on-disk.md"

    def test_describe_names_the_path_and_falls_back_to_the_id(self) -> None:
        """`describe()` is what a person reads; nothing called it."""
        named = Lead(
            text="a passage",
            source_path="notes/a.md",
            document_id="doc_x",
            span=Span(0, 9),
            score=1.0,
            unconfirmed_because="unconfirmed",
        )
        assert "notes/a.md" in named.describe()
        assert "unconfirmed" in named.describe()
        assert "doc_x" in replace(named, source_path="").describe()

    def test_a_lead_cannot_be_edited_after_it_is_handed_over(self) -> None:
        """Frozen, and it has to stay frozen.

        A caller that could rewrite a lead's text or span could hand on a
        passage that no longer matches where it says it came from -- which is
        the whole failure this library is built to prevent, arriving through a
        mutable dataclass.
        """
        lead = Lead(
            text="a passage",
            source_path="notes/a.md",
            document_id="doc_x",
            span=Span(0, 9),
            score=1.0,
            unconfirmed_because="unconfirmed",
        )
        with pytest.raises(FrozenInstanceError):
            lead.text = "something else"  # type: ignore[misc]

        # `slots=True`: there is nowhere to put an attribute that is not a
        # field, and **that** is the property rather than any particular
        # exception. The first version of this asserted `TypeError`, which is
        # what a frozen slotted dataclass raises on 3.12 -- its `__setattr__`
        # reaches a `super()` whose cell points at the pre-slots class. 3.13
        # fixed that and raises `FrozenInstanceError`, so the test pinned a
        # version's accident and went red on half the CI matrix. `__dict__` is
        # the fact underneath and does not move.
        assert not hasattr(lead, "__dict__"), "a slotted instance grew a dict to stash things in"
        assert Lead.__slots__ == (
            "text",
            "source_path",
            "document_id",
            "span",
            "score",
            "unconfirmed_because",
        )
