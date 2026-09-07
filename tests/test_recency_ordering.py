"""Prefer newer passages, without a package changing on its own overnight.

`sora`'s front page wants *today's world*: the same topic should surface the
newer article. The obvious implementation ranks by age, which needs a **now** --
and then the same question over the same corpus produces a different package on
Wednesday than it did on Tuesday. That is forbidden by
[ADR-0003](../docs/adr/0003-a-package-is-reproducible.md), and worse than
forbidden: `package_id` would stop identifying a package.

So recency here is **relative**. Candidates are compared only against each
other, and the newest of them is the newest whenever you ask.

The two tests that matter are at the ends:

- with **no dates anywhere** it must be exactly `by_score`, because that is
  what every number in `docs/measurements.md` was measured on;
- with dates it must actually reorder, or it is a setting that does nothing --
  which this repository keeps finding in its own claims.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tsumugi.application.build_context import _dated, build_context
from tsumugi.application.ingest import ingest_paths
from tsumugi.config import TsumugiConfig
from tsumugi.domain.anchor import Anchor
from tsumugi.domain.assembly import Candidate
from tsumugi.domain.budget import Budget
from tsumugi.domain.hashing import ContentHash
from tsumugi.domain.ordering import (
    DEFAULT_FRESHNESS,
    ORDERINGS,
    by_score,
    prefer_recent,
)
from tsumugi.domain.selection import ItemProvenance
from tsumugi.domain.span import Span
from tsumugi.errors import ConfigurationError
from tsumugi.infrastructure.cost.heuristic import CharacterCost
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

NL = chr(10)


def candidate(name: str, score: float, dated: str | None = None) -> Candidate:
    text = f"a passage from {name}"
    digest = ContentHash.of(text)
    return Candidate(
        text=text,
        score=score,
        source_path=name,
        anchor=Anchor(
            document_id=f"doc_{name}", span=Span(0, len(text)), text_hash=digest, version=digest
        ),
        provenance=ItemProvenance(observed_at=dated),
    )


#: The best-scoring passage is the oldest. The only arrangement where a
#: recency ordering can be seen doing anything.
OLD_BEST = [
    candidate("old.md", 5.0, "2024-01-01"),
    candidate("middle.md", 4.6, "2025-06-01"),
    candidate("new.md", 4.2, "2026-09-07"),
]


class TestItReducesToScoreWhereItShould:
    def test_no_dates_anywhere_is_exactly_by_score(self) -> None:
        """The property every published number depends on.

        A corpus of undated notes must behave as it always has, or the measured
        recall and trap rate describe a configuration nobody is running.
        """
        undated = [candidate("a.md", 5.0), candidate("b.md", 4.0), candidate("c.md", 3.0)]
        assert prefer_recent(undated) == by_score(undated)

    def test_freshness_zero_is_exactly_by_score_even_with_dates(self) -> None:
        assert prefer_recent(OLD_BEST, freshness=0.0) == by_score(OLD_BEST)

    def test_an_empty_list_orders_to_an_empty_list(self) -> None:
        assert prefer_recent([]) == []

    def test_a_single_candidate_is_returned_unchanged(self) -> None:
        one = [candidate("only.md", 1.0, "2026-01-01")]
        assert prefer_recent(one) == one


class TestItActuallyReorders:
    def test_a_newer_passage_overtakes_a_slightly_better_older_one(self) -> None:
        """Without this the setting could be honoured everywhere and do nothing."""
        assert [c.source_path for c in by_score(OLD_BEST)] == [
            "old.md",
            "middle.md",
            "new.md",
        ]
        assert [c.source_path for c in prefer_recent(OLD_BEST, freshness=0.6)] == [
            "new.md",
            "middle.md",
            "old.md",
        ]

    def test_relevance_still_wins_at_the_default(self) -> None:
        """0.3 lets a newer passage overtake a *slightly* better one, not a
        much better one. A package is an answer, not a newspaper."""
        far_apart = [
            candidate("old-but-best.md", 9.0, "2020-01-01"),
            candidate("new-but-weak.md", 1.0, "2026-09-07"),
        ]
        assert prefer_recent(far_apart)[0].source_path == "old-but-best.md"

    def test_an_undated_candidate_is_neither_rewarded_nor_punished(self) -> None:
        """It takes its own score rank for the date term.

        Pushing undated passages to the bottom would make one dated document in
        a folder of notes rearrange the whole corpus; pushing them to the top
        would do the reverse. Saying nothing has to mean nothing.
        """
        mixed = [
            candidate("undated.md", 5.0),
            candidate("old.md", 4.9, "2020-01-01"),
            candidate("new.md", 4.8, "2026-09-07"),
        ]
        assert prefer_recent(mixed, freshness=0.5)[0].source_path == "undated.md"


class TestItIsTheSameTwice:
    def test_two_calls_give_the_same_order(self) -> None:
        assert [c.source_path for c in prefer_recent(OLD_BEST)] == [
            c.source_path for c in prefer_recent(OLD_BEST)
        ]

    def test_identical_dates_fall_back_to_the_score_order(self) -> None:
        """A tie in the date must not be broken by anything unstable."""
        same_day = [
            candidate("a.md", 5.0, "2026-09-07"),
            candidate("b.md", 4.0, "2026-09-07"),
            candidate("c.md", 3.0, "2026-09-07"),
        ]
        assert prefer_recent(same_day, freshness=0.9) == by_score(same_day)

    def test_a_date_it_cannot_parse_still_orders_against_its_own_kind(self) -> None:
        """Nothing is parsed, so nothing raises mid-query.

        ISO 8601 sorts lexicographically, which is why the contract asks for
        it. A date in another shape orders against others of that shape rather
        than taking the whole query down.
        """
        odd = [
            candidate("a.md", 5.0, "07/09/2024"),
            candidate("b.md", 4.0, "08/09/2024"),
        ]
        assert [c.source_path for c in prefer_recent(odd, freshness=1.0)] == ["b.md", "a.md"]


class TestTheSettingReachesIt:
    def test_it_is_offered_by_name(self) -> None:
        assert ORDERINGS["recent"] is prefer_recent

    def test_the_environment_selects_it_and_carries_its_parameter(self) -> None:
        config = TsumugiConfig.from_env({"TSUMUGI_ORDERING": "recent", "TSUMUGI_FRESHNESS": "0.6"})
        assert config.freshness == pytest.approx(0.6)
        chosen = config.selected_ordering()
        assert [c.source_path for c in chosen(OLD_BEST, "")] == [
            "new.md",
            "middle.md",
            "old.md",
        ]

    def test_the_default_freshness_is_what_the_docstring_says(self) -> None:
        assert TsumugiConfig().freshness == DEFAULT_FRESHNESS
        assert DEFAULT_FRESHNESS == 0.3

    def test_an_unreadable_freshness_names_the_variable(self) -> None:
        with pytest.raises(ConfigurationError) as raised:
            TsumugiConfig.from_env({"TSUMUGI_FRESHNESS": "quite fresh"})
        assert "TSUMUGI_FRESHNESS" in str(raised.value)

    @pytest.mark.parametrize("value", [-0.1, 1.5])
    def test_a_share_outside_its_range_is_refused(self, value: float) -> None:
        with pytest.raises(ValueError) as raised:
            prefer_recent(OLD_BEST, freshness=value)
        assert "freshness" in str(raised.value)


class TestTheDateSurvivesIngest:
    """A front-matter date has to reach a candidate, or none of the above runs.

    The tests before this build candidates by hand. This one starts from files
    on disk, so it covers the half that a unit test cannot: the parser reading
    front matter, `_dated` choosing a key, and the provenance carrying it into
    the ordering.
    """

    def _corpus(self, root: Path) -> None:
        root.mkdir(parents=True)
        (root / "old.md").write_text(
            "---"
            + NL
            + "published: 2020-03-01"
            + NL
            + "---"
            + NL * 2
            + "# Harbour"
            + NL * 2
            + "The harbour reopened after the storm."
            + NL,
            encoding="utf-8",
            newline="",
        )
        (root / "new.md").write_text(
            "---"
            + NL
            + "published: 2026-09-07"
            + NL
            + "---"
            + NL * 2
            + "# Harbour"
            + NL * 2
            + "The harbour reopened again after the storm."
            + NL,
            encoding="utf-8",
            newline="",
        )

    def test_a_published_date_reaches_the_package(
        self, tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
    ) -> None:
        root = tmp_path / "feed"
        self._corpus(root)
        ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)

        package = build_context(
            "harbour reopened storm",
            store=store,
            index=index,
            cost_model=CharacterCost(),
            budget=Budget.parse("characters:2000"),
        )
        dates = {item.source_path: item.provenance.observed_at for item in package.items}
        assert dates, "the corpus must answer, or this checks nothing"
        assert dates.get("new.md") == "2026-09-07"
        assert dates.get("old.md") == "2020-03-01"

    @pytest.mark.parametrize(
        ("key", "expected"),
        [("observed_at", "1"), ("published", "2"), ("date", "3"), ("fetched_at", "5")],
    )
    def test_each_date_key_is_read(self, key: str, expected: str) -> None:
        """`fetched_at` last on purpose: when a crawler took a copy is a worse
        answer than the document's own date, and a much better one than none."""
        assert _dated({key: expected}) == expected

    def test_the_document_s_own_date_beats_the_crawler_s(self) -> None:
        assert _dated({"fetched_at": "2026-09-07", "published": "2020-03-01"}) == "2020-03-01"

    def test_a_document_that_states_no_date_carries_none(self) -> None:
        assert _dated({"title": "no date here"}) is None
        assert _dated({"published": "   "}) is None


class TestTheInputOrderDoesNotMatter:
    """Whatever order candidates arrive in, the answer is the same.

    Score is the baseline the recency term is blended against, so it has to be
    established here rather than assumed from the caller. Every other fixture
    in this file happens to be in score order already, so replacing the
    baseline with the raw input left them all green.
    """

    def test_a_scrambled_input_gives_the_same_order(self) -> None:
        scrambled = [OLD_BEST[2], OLD_BEST[0], OLD_BEST[1]]
        assert [c.source_path for c in prefer_recent(scrambled, freshness=0.6)] == [
            c.source_path for c in prefer_recent(OLD_BEST, freshness=0.6)
        ]

    def test_a_scrambled_input_still_reduces_to_by_score_without_dates(self) -> None:
        undated = [candidate("a.md", 3.0), candidate("b.md", 5.0), candidate("c.md", 4.0)]
        assert prefer_recent(undated) == by_score(undated)
        assert [c.source_path for c in prefer_recent(undated)] == ["b.md", "c.md", "a.md"]
