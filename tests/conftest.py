"""Shared fixtures, and the isolation every CLI test needs.

A CLI test that does not strip ``TSUMUGI_*`` writes into the developer's real
index. `kiseki` shipped that bug once; this file is the reason it cannot happen
here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tsumugi.infrastructure.index.fts import FtsIndex
from tsumugi.infrastructure.storage.database import connect
from tsumugi.infrastructure.storage.sqlite import SqliteDocumentStore


@pytest.fixture(autouse=True)
def _no_real_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point every test at a throwaway index, whatever the environment says.

    Autouse and unconditional. A test that wants the real default asks for it
    explicitly; nothing gets it by forgetting.
    """
    for name in ("TSUMUGI_INDEX", "TSUMUGI_IGNORE", "TSUMUGI_CANDIDATE_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TSUMUGI_INDEX", str(tmp_path / "test-index.db"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    handle = connect(tmp_path / "index.db")
    yield handle
    handle.close()


@pytest.fixture
def store(connection: sqlite3.Connection) -> SqliteDocumentStore:
    return SqliteDocumentStore(connection)


@pytest.fixture
def index(connection: sqlite3.Connection) -> FtsIndex:
    return FtsIndex(connection)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A small mixed-script corpus, with the awkward cases in it."""
    root = tmp_path / "corpus"
    (root / "notes").mkdir(parents=True)
    (root / "secrets").mkdir()

    (root / "notes" / "mountain.md").write_text(
        "---\ntitle: 装備メモ\n---\n\n# 装備\n\n"
        "テントは 2.4kg、二人用。\n\n## 燃料\n\nガスは 250g。東京の会議は明日です。\n",
        encoding="utf-8",
    )
    (root / "notes" / "budget.md").write_text(
        "# Budget\n\nThe unit is explicit at the call site.\n", encoding="utf-8"
    )
    (root / "notes" / "config.json").write_text(
        '{"title": "settings", "budget": "tokens"}\n', encoding="utf-8"
    )
    (root / "secrets" / ".env").write_text("API_KEY=hunter2\n", encoding="utf-8")
    (root / "notes" / "scratch.tmp").write_text("ignore me\n", encoding="utf-8")
    (root / ".tsumugiignore").write_text("*.tmp\n", encoding="utf-8")
    return root


class NetworkAttemptedError(RuntimeError):
    """A test tried to open a connection. Distinct so it cannot be mistaken.

    `ConnectionRefusedError` is what a closed port raises, and a test that
    expects a refusal would pass on this fence for the wrong reason -- the
    fence would be reporting its own success as the test's.
    """


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """`docs/architecture.md` says every test runs with no network. This checks it.

    That sentence was held by a static check -- `test_architecture.py` forbids
    the core from importing `socket`, `urllib` and the rest -- which proves
    what the source says and not what a run does. A transitive import, a
    fixture, or a test helper could still reach out, and nothing would notice.

    **Patched on the method, not on the class.** `ssl.SSLSocket` inherits from
    `socket.socket`, so replacing the class breaks anything that subclasses it
    and the failure looks nothing like the thing being prevented. `iriguchi`
    found that; `mamori` had already avoided it and said why in a docstring,
    which is the only reason it is avoided here too.

    A test that means to attempt a connection asks for it with
    `@pytest.mark.allows_network`, so the exception is a decision rather than a
    surprise.
    """
    if request.node.get_closest_marker("allows_network"):
        return

    import socket

    def refuse(self: socket.socket, address: object) -> None:
        raise NetworkAttemptedError(
            f"a test tried to connect to {address!r}. Nothing here needs the network; "
            "mark it `@pytest.mark.allows_network` if it genuinely does"
        )

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
