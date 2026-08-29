"""Content hashes that say which algorithm made them.

A bare hex string is a hash whose algorithm is an assumption. Writing the
algorithm into the value means an index built under one can be recognised, and
migrated, rather than silently compared against another.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

__all__ = ["DEFAULT_ALGORITHM", "ContentHash"]

#: Changing this changes every id in a new index, which is why it is one name
#: in one place rather than a literal at each call site.
DEFAULT_ALGORITHM: Final = "sha256"

_SUPPORTED: Final = frozenset({"sha256", "sha512", "blake2b"})


@dataclass(frozen=True, slots=True, order=True)
class ContentHash:
    """A digest, and the algorithm that produced it.

    Renders and parses as ``sha256:9f2c...``, so it survives a round trip
    through JSON without a schema having to remember what it was.
    """

    algorithm: str
    hexdigest: str

    def __post_init__(self) -> None:
        if self.algorithm not in _SUPPORTED:
            raise ValueError(f"unsupported hash algorithm {self.algorithm!r}")
        if not self.hexdigest:
            raise ValueError("hexdigest is empty")
        if not all(c in "0123456789abcdef" for c in self.hexdigest):
            raise ValueError("hexdigest is not lowercase hexadecimal")

    @classmethod
    def of(cls, text: str, algorithm: str = DEFAULT_ALGORITHM) -> ContentHash:
        """Hash ``text`` as UTF-8.

        The encoding is fixed rather than configurable. A hash whose encoding
        is a setting compares equal to nothing on another machine.
        """
        if algorithm not in _SUPPORTED:
            raise ValueError(f"unsupported hash algorithm {algorithm!r}")
        digest = hashlib.new(algorithm, text.encode("utf-8")).hexdigest()
        return cls(algorithm, digest)

    @classmethod
    def parse(cls, value: str) -> ContentHash:
        algorithm, separator, hexdigest = value.partition(":")
        if not separator:
            raise ValueError(f"not a qualified hash: {value!r}; expected 'algorithm:hex'")
        return cls(algorithm, hexdigest)

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.hexdigest}"

    def short(self, length: int = 12) -> str:
        """The leading hex, for human-facing output. Never for comparison."""
        return self.hexdigest[:length]
