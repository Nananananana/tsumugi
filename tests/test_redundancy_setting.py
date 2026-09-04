"""The redundancy threshold is reachable, and selecting a value changes output.

`fit_to_budget` has taken a `redundancy_threshold` since ADR-0008 and **nothing
could reach it**: `build_context` never passed one, and no configuration field
existed. A parameter with no caller is not a setting, it is a number with a
longer name, and 0.75 was chosen against one corpus like every other number
here.

The order of these tests is the order the questions matter in:

1. does it reach the build at all;
2. does the configuration carry it, through defaults, file and `TSUMUGI_*`;
3. **does choosing a different value change the package** -- because on the
   evaluation corpus it does not. `tools/measure_sensitivity.py` sweeps it from
   0.5 to 0.9 and moves no measured number, since near-duplicates are marked
   rather than vetoed (ADR-0008) and the budget binds in few cases. A setting
   that is honoured, typed, documented and inert is the failure this repository
   keeps finding, so the case where it bites is constructed here on purpose.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tsumugi.application.build_context import build_context
from tsumugi.application.ingest import ingest_paths
from tsumugi.config import TsumugiConfig
from tsumugi.domain.budget import Budget, Unit
from tsumugi.domain.package import ContextPackage
from tsumugi.domain.redundancy import DEFAULT_THRESHOLD
from tsumugi.errors import ConfigurationError
from tsumugi.evaluation.runner import cost_model_for
from tsumugi.infrastructure.filesystem import walk
from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.parsers import parser_for
from tsumugi.infrastructure.storage.database import connect
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore

Build = Callable[..., ContextPackage]

#: Two passages that differ by one character, and a third that does not. The
#: smallest corpus where a redundancy threshold can be observed at all.
DOCUMENTS = {
    "a.md": "The warranty coverage period is 24 months from the date of purchase.\n",
    "b.md": "The warranty coverage period is 24 months from the date of purchase!\n",
    "c.md": "Returns are accepted within 30 days of delivery to the customer.\n",
}
QUERY = "warranty coverage period"


@pytest.fixture
def build(tmp_path: Path) -> Callable[..., ContextPackage]:
    def _build(*, threshold: float, budget: str) -> ContextPackage:
        root = tmp_path / "corpus"
        root.mkdir(exist_ok=True)
        for name, text in DOCUMENTS.items():
            (root / name).write_text(text, encoding="utf-8")
        connection = connect(tmp_path / "index.db")
        try:
            store, index = SqliteDocumentStore(connection), FtsIndex(connection)
            ingest_paths(
                walk(root).files, root=root, store=store, index=index, parser_for=parser_for
            )
            return build_context(
                QUERY,
                store=store,
                index=index,
                cost_model=cost_model_for(Unit.CHARACTERS),
                budget=Budget.parse(budget),
                redundancy_threshold=threshold,
            )
        finally:
            connection.close()

    return _build


def signals_of(package: ContextPackage) -> list[str]:
    return [str(s) for item in package.items for s in getattr(item.selection, "signals", ())]


def rules_of(package: ContextPackage) -> list[str]:
    return [omission.rule.value for omission in package.omissions]


class TestChoosingAValueChangesThePackage:
    """The guard against a setting that is wired everywhere and does nothing."""

    def test_a_low_threshold_marks_the_near_duplicate(self, build: Build) -> None:
        package = build(threshold=0.5, budget="characters:2000")
        assert any("redundant_with:" in s for s in signals_of(package)), (
            "at 0.5 the two passages differ by one character and should be marked"
        )

    def test_a_high_threshold_does_not(self, build: Build) -> None:
        package = build(threshold=0.99, budget="characters:2000")
        assert not any("redundant_with:" in s for s in signals_of(package))

    def test_it_changes_why_something_was_left_out(self, build: Build) -> None:
        """The reason, not just the contents -- which is the point of ADR-0005.

        With a budget that admits one of the two, the same item is dropped
        either way. What differs is what the package *says* about it, and a
        package that explains a near-duplicate as "there was no room" is
        answering the wrong question.
        """
        tight = "characters:90"
        assert "redundant_candidate" in rules_of(build(threshold=0.5, budget=tight))
        assert "budget_exhausted" in rules_of(build(threshold=0.99, budget=tight))


class TestTheConfigurationReachesIt:
    """Defaults, then the file, then `TSUMUGI_*`."""

    def test_the_default_is_what_the_numbers_were_measured_on(self) -> None:
        assert TsumugiConfig().redundancy_threshold == DEFAULT_THRESHOLD

    def test_the_environment_carries_it(self) -> None:
        config = TsumugiConfig.from_env({"TSUMUGI_REDUNDANCY_THRESHOLD": "0.5"})
        assert config.redundancy_threshold == pytest.approx(0.5)

    def test_a_mapping_carries_it(self) -> None:
        assert TsumugiConfig.from_mapping(
            {"redundancy_threshold": 0.9}
        ).redundancy_threshold == pytest.approx(0.9)

    def test_an_unreadable_value_names_the_variable(self) -> None:
        """Refused rather than ignored, like every other setting here."""
        with pytest.raises(ConfigurationError) as raised:
            TsumugiConfig.from_env({"TSUMUGI_REDUNDANCY_THRESHOLD": "quite similar"})
        assert "TSUMUGI_REDUNDANCY_THRESHOLD" in str(raised.value)
