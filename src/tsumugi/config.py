"""Every switch, on one frozen object, and where the index lives.

No opinion about file formats: ``from_mapping`` takes an already-parsed
mapping, so a caller picks JSON, TOML or a dict literal and keeps their parser
to themselves. Layers, later winning:

    built-in defaults  ->  config file  ->  TSUMUGI_* env  ->  command-line flags

Unknown keys are refused rather than ignored. A typo in an ignore rule or a
budget that silently does nothing is the worst available outcome.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Final

from .application.search import DEFAULT_CONFIRMATION, Confirmation
from .domain.ordering import (
    DEFAULT_DIVERSITY,
    ORDERINGS,
    Ordering,
    maximal_marginal_relevance,
)
from .domain.redundancy import DEFAULT_THRESHOLD
from .errors import ConfigurationError

__all__ = ["DEFAULT_INDEX_DIRECTORY", "TsumugiConfig", "default_index_path"]

#: One rule, one place, printed on every ingest.
#:
#: Not beside the corpus, and not under a platform data directory. Corpus
#: folders get synced, shared and committed, and an index is a complete
#: plaintext copy of everything in one (docs/threat-model.md) -- putting it
#: there invites a one-line accident. A platform directory would be more
#: conventional and less predictable, and for a file this sensitive the owner
#: knowing where it is outranks convention.
DEFAULT_INDEX_DIRECTORY: Final = ".tsumugi"


def default_index_path() -> Path:
    """``~/.tsumugi/index.db``, or whatever ``TSUMUGI_INDEX`` says."""
    override = os.environ.get("TSUMUGI_INDEX")
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_INDEX_DIRECTORY / "index.db"


@dataclass(frozen=True, slots=True)
class TsumugiConfig:
    """The whole configuration."""

    #: Where the index file lives. Printed by every command that touches it.
    index_path: Path | None = None
    #: Extra ignore patterns, on top of the corpus's own ignore files.
    ignore: tuple[str, ...] = ()
    #: Follow symlinks while walking a corpus. Off, because a corpus folder
    #: with a link to ``/`` is not a hypothetical.
    follow_symlinks: bool = False
    #: Cap on candidates the index returns per query. A cap that truncates has
    #: to reach ``omissions[]`` under ``truncated_by_cap`` (ADR-0005).
    candidate_limit: int = 50
    #: Which candidate the budget is offered first (`domain/ordering.py`).
    #: ``score`` is descending relevance and is what every number in
    #: `docs/measurements.md` was measured on; ``mmr`` trades relevance against
    #: novelty (Carbonell & Goldstein, 1998) so a budget is spent on distinct
    #: evidence rather than the same sentence twice.
    #:
    #: Named rather than defaulted to the newer one. On this corpus MMR changes
    #: the contents of 5 packages in 240 and moves no measured number, because
    #: the budget binds in only 32 cases -- so there is nothing here to justify
    #: changing what everybody already gets.
    ordering: str = "score"
    #: The share of the MMR trade given to relevance: 1.0 is pure relevance and
    #: is exactly ``score``, 0.0 is pure novelty and ignores the question.
    #: Ignored unless ``ordering`` is ``mmr``.
    diversity: float = DEFAULT_DIVERSITY
    #: How alike two passages must be before one is marked a near-duplicate of
    #: the other. Measured on this corpus, moving it between 0.5 and 0.9 changes
    #: no reported number -- which is the reason it is a setting rather than a
    #: constant. A value that nothing here can move is a value fitted to
    #: documents nobody else has, and somebody else's corpus will disagree.
    redundancy_threshold: float = DEFAULT_THRESHOLD
    #: The three numbers that decide whether a candidate is confirmed. Settings
    #: rather than constants because `tools/measure_sensitivity.py` moved each
    #: one and re-scored the corpus, and **all three are on cliffs** -- between
    #: 13 and 17 points of recall or trap ride on each, and every one was
    #: chosen against a corpus this project wrote.
    #:
    #: `inflection_tail` is the one most likely to need changing: it exists
    #: because Japanese glues grammar to its nouns, and 2 is the number that
    #: suits Japanese. The defaults are unchanged and are what every number in
    #: `docs/measurements.md` was measured on.
    coverage_threshold: float = DEFAULT_CONFIRMATION.coverage_threshold
    relative_match_floor: float = DEFAULT_CONFIRMATION.relative_match_floor
    inflection_tail: int = DEFAULT_CONFIRMATION.inflection_tail

    def confirmation(self) -> Confirmation:
        """The three as one value, validated.

        Raises on a value outside its range rather than clamping. A
        `coverage_threshold` of 2.0 silently clamped to 1.0 is a setting that
        looks applied and is not, which is the failure this repository keeps
        finding in its own claims.
        """
        return Confirmation(
            coverage_threshold=self.coverage_threshold,
            relative_match_floor=self.relative_match_floor,
            inflection_tail=self.inflection_tail,
        )

    def selected_ordering(self) -> Ordering:
        """The ordering this configuration names, ready to pass to a build.

        Raises rather than falling back. A misspelt ``ordering`` that quietly
        became ``score`` would be a setting that looks applied and is not --
        which is the failure this repository has spent a week removing from its
        own claims.
        """
        if self.ordering == "rerank":
            # Resolved here rather than in `domain.ORDERINGS`, because the
            # domain may not know that `infrastructure` exists (ADR-0001) and a
            # cross-encoder is as far from stdlib-only as this project goes.
            from .infrastructure.reranking import rerank

            return rerank
        try:
            chosen = ORDERINGS[self.ordering]
        except KeyError:
            raise ConfigurationError(
                f"unknown ordering {self.ordering!r}. "
                f"Known: {', '.join(sorted([*ORDERINGS, 'rerank']))}"
            ) from None
        if chosen is not maximal_marginal_relevance:
            return chosen
        return partial(maximal_marginal_relevance, diversity=self.diversity)

    def resolved_index_path(self) -> Path:
        return self.index_path if self.index_path is not None else default_index_path()

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> TsumugiConfig:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - known)
        if unknown:
            raise ConfigurationError(
                f"unknown settings: {', '.join(unknown)}. Known: {', '.join(sorted(known))}"
            )
        settings: dict[str, Any] = dict(values)
        if "index_path" in settings and settings["index_path"] is not None:
            settings["index_path"] = Path(str(settings["index_path"])).expanduser()
        if "ignore" in settings:
            settings["ignore"] = tuple(settings["ignore"])
        return cls(**settings)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> TsumugiConfig:
        source = environ if environ is not None else os.environ
        values: dict[str, Any] = {}
        if index := source.get("TSUMUGI_INDEX"):
            values["index_path"] = index
        if ignore := source.get("TSUMUGI_IGNORE"):
            values["ignore"] = tuple(p for p in ignore.split(os.pathsep) if p)
        if limit := source.get("TSUMUGI_CANDIDATE_LIMIT"):
            try:
                values["candidate_limit"] = int(limit)
            except ValueError as error:
                raise ConfigurationError(
                    f"TSUMUGI_CANDIDATE_LIMIT must be a number, not {limit!r}"
                ) from error
        if ordering := source.get("TSUMUGI_ORDERING"):
            values["ordering"] = ordering
        if diversity := source.get("TSUMUGI_DIVERSITY"):
            try:
                values["diversity"] = float(diversity)
            except ValueError as error:
                raise ConfigurationError(
                    f"TSUMUGI_DIVERSITY must be a number between 0 and 1, not {diversity!r}"
                ) from error
        if threshold := source.get("TSUMUGI_REDUNDANCY_THRESHOLD"):
            try:
                values["redundancy_threshold"] = float(threshold)
            except ValueError as error:
                raise ConfigurationError(
                    "TSUMUGI_REDUNDANCY_THRESHOLD must be a number between 0 and 1, "
                    f"not {threshold!r}"
                ) from error
        for variable, field, cast in (
            ("TSUMUGI_COVERAGE_THRESHOLD", "coverage_threshold", float),
            ("TSUMUGI_RELATIVE_MATCH_FLOOR", "relative_match_floor", float),
            ("TSUMUGI_INFLECTION_TAIL", "inflection_tail", int),
        ):
            if raw := source.get(variable):
                try:
                    values[field] = cast(raw)
                except ValueError as error:
                    raise ConfigurationError(f"{variable} must be a number, not {raw!r}") from error
        return cls.from_mapping(values)

    def merged_with(
        self, other: TsumugiConfig, defaults: TsumugiConfig | None = None
    ) -> TsumugiConfig:
        """``other`` wins, field by field, wherever it differs from the defaults."""
        baseline = defaults if defaults is not None else TsumugiConfig()
        changes = {
            name: getattr(other, name)
            for name in self.__dataclass_fields__
            if getattr(other, name) != getattr(baseline, name)
        }
        return replace(self, **changes)
