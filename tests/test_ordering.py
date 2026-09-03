"""Two orderings, selectable, and neither of them inert.

`fit_to_budget` fills a budget best-first and *best* used to mean one thing.
It is a parameter now, with the default unchanged, because the alternative is
a real trade and not an improvement — and because a choice nobody can express
is not a choice.

The tests here answer three questions in order, and the third is the one this
repository keeps having to ask:

1. does each ordering do what its name says;
2. does the configuration reach it, through file, environment and flag;
3. **does selecting the other one change anything at all** — because a setting
   that is honoured, documented, typed, and produces identical output is the
   shape of four defects found this week.
"""

from __future__ import annotations

import pytest

from tsumugi.config import TsumugiConfig
from tsumugi.domain.anchor import Anchor
from tsumugi.domain.assembly import Candidate
from tsumugi.domain.hashing import ContentHash
from tsumugi.domain.ordering import (
    ORDERINGS,
    by_score,
    maximal_marginal_relevance,
)
from tsumugi.domain.span import Span
from tsumugi.errors import ConfigurationError


def candidate(text: str, score: float, name: str) -> Candidate:
    digest = ContentHash.of(text)
    return Candidate(
        text=text,
        score=score,
        source_path=name,
        anchor=Anchor(
            document_id="doc_x", span=Span(0, len(text)), text_hash=digest, version=digest
        ),
    )


#: A near-duplicate ranked second, and a distinct passage ranked third. The
#: only arrangement where the two orderings can disagree.
TRIPLE = [
    candidate("The warranty coverage period is 24 months from purchase.", 5.0, "a.md"),
    candidate("The warranty coverage period is 24 months from purchase!", 4.9, "b.md"),
    candidate("Returns are accepted within 30 days of delivery.", 4.5, "c.md"),
]


class TestTheOrderingsDiffer:
    """The guard against shipping a setting that changes nothing."""

    def test_score_keeps_the_near_duplicate_second(self) -> None:
        assert [c.source_path for c in by_score(TRIPLE)] == ["a.md", "b.md", "c.md"]

    def test_mmr_demotes_the_near_duplicate_below_the_distinct_passage(self) -> None:
        """The whole point of the algorithm, on the smallest case that shows it.

        Without this the option could be honoured everywhere and do nothing,
        and every test above would still pass.
        """
        assert [c.source_path for c in maximal_marginal_relevance(TRIPLE)] == [
            "a.md",
            "c.md",
            "b.md",
        ]

    def test_pure_relevance_is_exactly_the_score_ordering(self) -> None:
        """`diversity=1.0` reduces to `by_score`, which is what the maths says.

        A check on the implementation rather than on the idea: if the two
        disagreed at the extreme, the trade in between would be measuring
        something other than the trade.
        """
        assert maximal_marginal_relevance(TRIPLE, diversity=1.0) == by_score(TRIPLE)

    def test_both_are_stable_under_repetition(self) -> None:
        """ADR-0003: two runs of the same query produce the same package."""
        for ordering in ORDERINGS.values():
            assert [c.source_path for c in ordering(TRIPLE)] == [
                c.source_path for c in ordering(TRIPLE)
            ]

    def test_an_empty_list_orders_to_an_empty_list(self) -> None:
        for ordering in ORDERINGS.values():
            assert ordering([]) == []


class TestTheConfigurationReachesIt:
    """Defaults, then the file, then `TSUMUGI_*`, then the flag."""

    def test_the_default_is_the_one_the_numbers_were_measured_on(self) -> None:
        assert TsumugiConfig().ordering == "score"
        assert TsumugiConfig().selected_ordering() is by_score

    def test_the_environment_selects_and_carries_its_parameter(self) -> None:
        config = TsumugiConfig.from_env({"TSUMUGI_ORDERING": "mmr", "TSUMUGI_DIVERSITY": "0.4"})
        assert config.ordering == "mmr"
        assert config.diversity == pytest.approx(0.4)
        chosen = config.selected_ordering()
        assert [c.source_path for c in chosen(TRIPLE)] == ["a.md", "c.md", "b.md"]

    def test_a_misspelt_ordering_is_refused_rather_than_ignored(self) -> None:
        """The failure this project keeps finding: a setting that looks applied.

        Falling back to the default would make `TSUMUGI_ORDERING=mrr` a silent
        no-op, and the run would report numbers for an ordering nobody chose.
        """
        with pytest.raises(ConfigurationError) as raised:
            TsumugiConfig(ordering="mrr").selected_ordering()
        assert "mrr" in str(raised.value)
        assert "mmr" in str(raised.value), "the message does not say what is available"

    def test_a_diversity_outside_the_range_is_refused(self) -> None:
        with pytest.raises(ValueError):
            maximal_marginal_relevance(TRIPLE, diversity=1.5)

    def test_an_unreadable_diversity_names_the_variable(self) -> None:
        with pytest.raises(ConfigurationError) as raised:
            TsumugiConfig.from_env({"TSUMUGI_DIVERSITY": "quite a lot"})
        assert "TSUMUGI_DIVERSITY" in str(raised.value)
