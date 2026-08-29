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
from pathlib import Path
from typing import Any, Final

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
