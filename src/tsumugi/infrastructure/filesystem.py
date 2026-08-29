"""Walking a corpus folder, and deciding what not to read.

Two kinds of skip, and they are reported differently because they mean
different things:

- **ignored** -- a pattern said not to. The owner asked for this.
- **refused** -- the file looks like a credential store. The owner did not ask,
  and would want to know.

Both are reported. Silently skipping and silently including are both wrong: a
corpus that quietly excluded half a folder looks exactly like a corpus that
found nothing there (ADR-0005).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = ["REFUSED_PATTERNS", "IgnoreRules", "Skipped", "Walk", "walk"]

#: Files whose whole purpose is to hold a secret. Skipped whatever the ignore
#: rules say, and reported when it happens -- see docs/threat-model.md.
REFUSED_PATTERNS: Final = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "*.ppk",
    "credentials",
    "credentials.*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "*.kdbx",
)

#: Directories never worth walking into. Not a security measure -- a time one.
_SKIP_DIRECTORIES: Final = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".tsumugi",
    }
)

_IGNORE_FILES: Final = (".tsumugiignore", ".gitignore")


@dataclass(frozen=True, slots=True)
class Skipped:
    """One file that was not read, and why."""

    path: Path
    reason: str
    #: The pattern that matched, when a pattern did.
    rule: str = ""


@dataclass(slots=True)
class Walk:
    """What a walk found."""

    files: list[Path] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)


class IgnoreRules:
    """A useful subset of gitignore syntax.

    Supported: glob patterns, ``#`` comments, blank lines, ``!`` negation,
    a trailing ``/`` for directories, and a leading ``/`` to anchor at the root.

    Not supported: ``**`` spanning path segments in the middle of a pattern,
    and per-directory rule stacking. Saying so is better than implying a
    compatibility that would silently include a file the owner meant to
    exclude -- which is why the unsupported half is written down rather than
    discovered.
    """

    def __init__(self, patterns: Sequence[str] = ()) -> None:
        self._rules: list[tuple[str, bool, bool]] = []
        for raw in patterns:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            directory_only = line.endswith("/")
            self._rules.append((line.strip("/"), negated, directory_only))

    @classmethod
    def read_from(cls, root: Path) -> IgnoreRules:
        """Load whichever ignore files sit at the root of the corpus."""
        patterns: list[str] = []
        for name in _IGNORE_FILES:
            candidate = root / name
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8", errors="replace")
                patterns.extend(text.splitlines())
        return cls(patterns)

    def matched_by(self, relative: Path, *, is_directory: bool) -> str | None:
        """The pattern that excludes this path, or ``None``.

        Later rules win, so a ``!`` line can rescue something an earlier line
        excluded -- which is the behaviour people expect from the files they
        already wrote.
        """
        text = relative.as_posix()
        decision: str | None = None
        for pattern, negated, directory_only in self._rules:
            if directory_only and not is_directory:
                continue
            hit = fnmatch.fnmatch(text, pattern) or any(
                fnmatch.fnmatch(part, pattern) for part in relative.parts
            )
            if hit:
                decision = None if negated else pattern
        return decision


def _is_refused(name: str) -> str | None:
    lowered = name.lower()
    for pattern in REFUSED_PATTERNS:
        if fnmatch.fnmatch(lowered, pattern):
            return pattern
    return None


def walk(root: Path, *, rules: IgnoreRules | None = None, follow_symlinks: bool = False) -> Walk:
    """Every candidate file under ``root``, and everything skipped.

    Symlinks are not followed by default. A corpus folder with a link to ``/``
    is not a hypothetical, and neither is one that links to itself.
    """
    root = root.resolve()
    rules = rules if rules is not None else IgnoreRules.read_from(root)
    found = Walk()

    for path in sorted(_descend(root, rules, follow_symlinks)):
        relative = path.relative_to(root)
        refused = _is_refused(path.name)
        if refused is not None:
            found.skipped.append(Skipped(relative, "looks like a credential store", refused))
            continue
        matched = rules.matched_by(relative, is_directory=False)
        if matched is not None:
            found.skipped.append(Skipped(relative, "excluded by an ignore rule", matched))
            continue
        found.files.append(path)

    return found


def _descend(root: Path, rules: IgnoreRules, follow_symlinks: bool) -> Iterator[Path]:
    stack = [root]
    seen: set[Path] = set()
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_symlink() and not follow_symlinks:
                continue
            if entry.is_dir():
                if entry.name in _SKIP_DIRECTORIES:
                    continue
                if rules.matched_by(entry.relative_to(root), is_directory=True) is not None:
                    continue
                resolved = entry.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                stack.append(entry)
            elif entry.is_file():
                yield entry
