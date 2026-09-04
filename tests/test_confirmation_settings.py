"""The three numbers confirmation turns on, and proof each one is connected.

`tools/measure_sensitivity.py` moved every constant in the retrieval path and
re-scored the labelled corpus. Three of them are **cliffs** — the sweep's word
for a value that swings the numbers when nudged, which is the signature of a
number fitted to one corpus:

    INFLECTION_TAIL       16.7 recall points between 1 and 2
    RELATIVE_MATCH_FLOOR  16.7 trap points between 0.5 and 0.8
    COVERAGE_THRESHOLD    13.3 trap points between 0.6 and 1.0

They are settings now. The defaults do not move, and every number in
`docs/measurements.md` was taken on them.

**Each test here changes one number and observes retrieval change.** That is
the whole point: this repository's recurring defect is a setting that is
honoured, documented, typed, and does nothing, and the sweep itself found two
constants it was not actually moving. A test that only checks the value round
-trips through a dataclass would pass on a `Confirmation` nothing reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tsumugi.application.ingest import ingest_paths
from tsumugi.application.search import (
    DEFAULT_CONFIRMATION,
    Confirmation,
    _counted,
    search,
)
from tsumugi.config import TsumugiConfig
from tsumugi.errors import ConfigurationError
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore


@pytest.fixture
def corpus_index(
    corpus: Path, store: SqliteDocumentStore, index: FtsIndex
) -> tuple[SqliteDocumentStore, FtsIndex]:
    ingest_paths(walk(corpus).files, root=corpus, store=store, index=index, parser_for=parser_for)
    return store, index


#: Two documents, one confirming more of the question than the other. The
#: arrangement the relative floor exists for, and the only one where it acts.
NL = chr(10)

TWO_STRENGTHS_QUERY = "the warranty period and the refund window"


@pytest.fixture
def two_strengths(
    tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
) -> tuple[SqliteDocumentStore, FtsIndex]:
    root = tmp_path / "strengths"
    root.mkdir()
    (root / "both.md").write_text(
        "# A" + NL * 2 + "We document the warranty period and the refund window here." + NL,
        encoding="utf-8",
    )
    (root / "one.md").write_text(
        "# B" + NL * 2 + "Only the warranty period appears in this note." + NL,
        encoding="utf-8",
    )
    ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)
    return store, index


#: No two words of this stand together in the document below, so the phrase
#: rule finds nothing and coverage -- where the tail acts -- decides.
STEM_QUERY = "warranty refunds periodic"


@pytest.fixture
def stem_only(
    tmp_path: Path, store: SqliteDocumentStore, index: FtsIndex
) -> tuple[SqliteDocumentStore, FtsIndex]:
    root = tmp_path / "stems"
    root.mkdir()
    (root / "policy.md").write_text(
        "# Policy"
        + NL * 2
        + "Warranty is explained below. Refund is handled separately. "
        + "Periodic reviews happen yearly."
        + NL,
        encoding="utf-8",
    )
    ingest_paths(walk(root).files, root=root, store=store, index=index, parser_for=parser_for)
    return store, index


def _confirmed(
    question: str,
    pair: tuple[SqliteDocumentStore, FtsIndex],
    confirmation: Confirmation | None = None,
) -> int:
    """How many candidates survived confirmation."""
    store, index = pair
    results, _ = search(question, store=store, index=index, confirmation=confirmation)
    return sum(1 for result in results if not result.unconfirmed)


class TestEachSettingReachesTheCode:
    """One number moved, and retrieval observed to change.

    The Japanese question is deliberate: `inflection_tail` exists because
    Japanese glues grammar to its nouns, so it is the language where the
    setting can be seen doing its job.
    """

    QUESTION = "テントの重量は"

    def test_the_inflection_tail_changes_what_is_confirmed(
        self, stem_only: tuple[SqliteDocumentStore, FtsIndex]
    ) -> None:
        """End to end, because the unit test below could not see the wiring.

        `refunds` in the question, `refund` in the document: one character.
        The tail decides whether that stem counts as the whole term, and the
        whole term is what carries coverage to 1.0.

        **Its own corpus, and every clause of it is load-bearing.** No two
        words of the question stand together in the document, so the phrase
        rule finds nothing and coverage -- the only rule the tail acts in --
        is what decides. `warranty` retrieves the document, so the index is
        not what is being tested. Three earlier attempts failed for reasons
        worth knowing: `_content_terms` already strips Japanese particles, and
        an English plural like `tents` is never retrieved at all because the
        index holds `tent`.

        Written after deleting the setting from the call and watching the
        suite stay green -- the `_counted` test below passes on a `_counted`
        nothing passes a tail to.
        """
        assert _confirmed(STEM_QUERY, stem_only, Confirmation(inflection_tail=0)) == 0
        assert _confirmed(STEM_QUERY, stem_only, Confirmation(inflection_tail=1)) == 1

    def test_the_counting_rule_itself(self) -> None:
        """The stem rule, at the function the setting feeds.

        Supporting detail for the test above, not a substitute for it: this
        passes whether or not anything passes a tail to `_counted`.
        """
        # `weighs` in the question, `weigh` in the document: one character.
        assert _counted("weighs", "weigh", 0) == 5, "no tail: only the stem counts"
        assert _counted("weighs", "weigh", 1) == 6, "one character of tail: the whole term counts"
        assert _counted("weighs", "weigh", 2) == 6

        # Two characters, which is what ships.
        assert _counted("running", "runn", 2) == 4, "three short is more than a tail"
        assert _counted("runs", "run", 2) == 4

        # A stem has to be a real prefix and at least two characters, whatever
        # the tail says -- otherwise a generous tail would make `x` match
        # anything beginning with it.
        assert _counted("warranty", "w", 8) == 1
        assert _counted("warranty", "arrant", 8) == 6, "not a prefix"

    def test_the_coverage_threshold_changes_what_is_confirmed(
        self, corpus_index: tuple[SqliteDocumentStore, FtsIndex]
    ) -> None:
        loose = _confirmed(self.QUESTION, corpus_index, Confirmation(coverage_threshold=0.2))
        tight = _confirmed(self.QUESTION, corpus_index, Confirmation(coverage_threshold=1.0))
        assert loose >= tight
        assert loose != tight, "coverage_threshold is not reaching the coverage rule"

    def test_the_relative_floor_demotes_the_weaker_match(
        self, two_strengths: tuple[SqliteDocumentStore, FtsIndex]
    ) -> None:
        """A document matching less of the question than the best one.

        `both.md` confirms 41 characters of the question and `one.md` confirms
        19. The floor is a share of the best, so at 0.8 the weaker one is
        demoted to unconfirmed and at 0.0 it survives -- which is the whole
        behaviour ADR-0019 describes, and 16.7 trap points on the real corpus.

        **Its own corpus, because the shared fixture cannot show this.** The
        floor engages only where the *phrase* rule confirmed something, and a
        coverage-confirmed result carries `matched=0`, so every question the
        fixture can answer leaves the floor switched off.
        """
        assert (
            _confirmed(TWO_STRENGTHS_QUERY, two_strengths, Confirmation(relative_match_floor=0.0))
            == 2
        )
        assert (
            _confirmed(TWO_STRENGTHS_QUERY, two_strengths, Confirmation(relative_match_floor=0.8))
            == 1
        )

    def test_the_default_is_what_the_measurements_were_taken_on(
        self, corpus_index: tuple[SqliteDocumentStore, FtsIndex]
    ) -> None:
        """Passing nothing and passing the default must agree.

        If they did not, every published number would describe a configuration
        nobody gets by default.
        """
        assert _confirmed(self.QUESTION, corpus_index) == _confirmed(
            self.QUESTION, corpus_index, DEFAULT_CONFIRMATION
        )
        assert Confirmation() == DEFAULT_CONFIRMATION


class TestTheDefaultsDidNotMove:
    def test_they_are_the_values_every_published_number_used(self) -> None:
        assert DEFAULT_CONFIRMATION.coverage_threshold == 1.0
        assert DEFAULT_CONFIRMATION.relative_match_floor == 0.8
        assert DEFAULT_CONFIRMATION.inflection_tail == 2

    def test_the_config_defaults_agree_with_the_search_defaults(self) -> None:
        """Two places holding the same number is two places to change it."""
        assert TsumugiConfig().confirmation() == DEFAULT_CONFIRMATION


class TestAValueOutsideItsRangeIsRefused:
    """Raised, never clamped.

    A `coverage_threshold` of 2.0 quietly clamped to 1.0 is a setting that
    looks applied and is not -- and the run would report numbers for a
    configuration nobody chose.
    """

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"coverage_threshold": 0.0},
            {"coverage_threshold": 1.5},
            {"coverage_threshold": -1.0},
            {"relative_match_floor": 1.5},
            {"relative_match_floor": -0.1},
            {"inflection_tail": -1},
        ],
    )
    def test_it_refuses(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError) as raised:
            Confirmation(**kwargs)  # type: ignore[arg-type]
        assert next(iter(kwargs)) in str(raised.value), "the message does not name the field"

    def test_the_edges_that_are_allowed_really_are(self) -> None:
        """The positive control. Without it every row above could pass on a
        constructor that refused everything."""
        assert Confirmation(coverage_threshold=1.0).coverage_threshold == 1.0
        assert Confirmation(relative_match_floor=0.0).relative_match_floor == 0.0
        assert Confirmation(inflection_tail=0).inflection_tail == 0


class TestTheEnvironmentReachesThem:
    def test_each_variable_is_read(self) -> None:
        config = TsumugiConfig.from_env(
            {
                "TSUMUGI_COVERAGE_THRESHOLD": "0.8",
                "TSUMUGI_RELATIVE_MATCH_FLOOR": "0.6",
                "TSUMUGI_INFLECTION_TAIL": "3",
            }
        )
        assert config.confirmation() == Confirmation(
            coverage_threshold=0.8, relative_match_floor=0.6, inflection_tail=3
        )

    @pytest.mark.parametrize(
        "variable",
        [
            "TSUMUGI_COVERAGE_THRESHOLD",
            "TSUMUGI_RELATIVE_MATCH_FLOOR",
            "TSUMUGI_INFLECTION_TAIL",
        ],
    )
    def test_an_unreadable_value_names_the_variable(self, variable: str) -> None:
        with pytest.raises(ConfigurationError) as raised:
            TsumugiConfig.from_env({variable: "quite a lot"})
        assert variable in str(raised.value)

    def test_an_unset_environment_leaves_the_defaults_alone(self) -> None:
        assert TsumugiConfig.from_env({}).confirmation() == DEFAULT_CONFIRMATION
