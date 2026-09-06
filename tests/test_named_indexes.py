"""Several indexes in one process, addressed by name and never by path.

`sora` keeps two corpora -- `personal` and `news` -- and wants one MCP process.
The configuration gains `indexes`, a name-to-path table the *operator* writes;
a tool call says `index: "news"` and may not say where any file lives.

The tests that matter are the two refusals: an unknown name raises and lists
the known ones (falling back to the default index would answer a question
about the news out of somebody's notes and call it success), and a malformed
environment entry raises rather than being skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tsumugi.config import TsumugiConfig
from tsumugi.errors import ConfigurationError


class TestResolutionByName:
    def test_no_name_is_the_default_index(self, tmp_path: Path) -> None:
        config = TsumugiConfig(index_path=tmp_path / "default.db")
        assert config.resolved_index_path() == tmp_path / "default.db"
        assert config.resolved_index_path(None) == tmp_path / "default.db"

    def test_a_known_name_resolves_to_its_path(self, tmp_path: Path) -> None:
        config = TsumugiConfig.from_mapping(
            {"indexes": {"personal": tmp_path / "p.db", "news": tmp_path / "n.db"}}
        )
        assert config.resolved_index_path("news") == tmp_path / "n.db"
        assert config.resolved_index_path("personal") == tmp_path / "p.db"

    def test_an_unknown_name_is_refused_and_the_known_ones_are_listed(self, tmp_path: Path) -> None:
        """Never the default. The wrong corpus answering is not a fallback."""
        config = TsumugiConfig.from_mapping({"indexes": {"news": tmp_path / "n.db"}})
        with pytest.raises(ConfigurationError) as raised:
            config.resolved_index_path("persnal")
        assert "persnal" in str(raised.value)
        assert "news" in str(raised.value), "the message does not say what is available"

    def test_an_unknown_name_with_nothing_configured_says_so(self) -> None:
        with pytest.raises(ConfigurationError) as raised:
            TsumugiConfig().resolved_index_path("news")
        assert "none" in str(raised.value)
        assert "TSUMUGI_INDEXES" in str(raised.value), "it does not say how to configure one"

    def test_the_config_stays_hashable(self, tmp_path: Path) -> None:
        """Pairs rather than a dict, for exactly this."""
        config = TsumugiConfig.from_mapping({"indexes": {"news": tmp_path / "n.db"}})
        assert isinstance(hash(config), int)


class TestTheEnvironment:
    def test_entries_are_name_equals_path_separated_by_the_path_separator(
        self, tmp_path: Path
    ) -> None:
        value = os.pathsep.join([f"personal={tmp_path / 'p.db'}", f"news={tmp_path / 'n.db'}"])
        config = TsumugiConfig.from_env({"TSUMUGI_INDEXES": value})
        assert config.resolved_index_path("personal") == tmp_path / "p.db"
        assert config.resolved_index_path("news") == tmp_path / "n.db"

    @pytest.mark.parametrize("entry", ["news", "=path", "news=", "news:path"])
    def test_a_malformed_entry_is_refused_not_skipped(self, entry: str) -> None:
        """Skipping it would make `index: "news"` fail later with a less
        useful message, on a machine where the operator believes it is set."""
        with pytest.raises(ConfigurationError) as raised:
            TsumugiConfig.from_env({"TSUMUGI_INDEXES": entry})
        assert "name=path" in str(raised.value)

    def test_a_tilde_is_expanded(self) -> None:
        config = TsumugiConfig.from_env({"TSUMUGI_INDEXES": "news=~/news.db"})
        assert "~" not in str(config.resolved_index_path("news"))

    def test_an_unset_variable_leaves_no_named_indexes(self) -> None:
        assert TsumugiConfig.from_env({}).indexes == ()

    def test_named_indexes_survive_a_merge(self, tmp_path: Path) -> None:
        base = TsumugiConfig()
        named = TsumugiConfig.from_mapping({"indexes": {"news": tmp_path / "n.db"}})
        assert base.merged_with(named).resolved_index_path("news") == tmp_path / "n.db"
