"""The seams.

Every port is a ``Protocol``, so an implementation is anything with the right
shape. Nothing outside has to import tsumugi to satisfy one, and nothing
inside has to know which implementation it got. This is `kiseki`'s ADR-0004,
and it is what makes the sibling adapters optional rather than special.

Each port ships with a conformance suite in ``tests/contracts.py``. A new
implementation subclasses the matching mixin and inherits the contract rather
than guessing at it -- including the test doubles, which are held to exactly
the same suite. A fake that is easier to satisfy than the real thing is a fake
that hides bugs.
"""

from __future__ import annotations

from .cost import CostModel
from .freshness import FreshnessCheck
from .index import Index, IndexHit
from .ledger import LedgerStore
from .parser import ParsedDocument, Parser
from .redactor import Redactor
from .store import DocumentStore
from .tokenizer import Tokenizer

__all__ = [
    "CostModel",
    "DocumentStore",
    "FreshnessCheck",
    "Index",
    "IndexHit",
    "LedgerStore",
    "ParsedDocument",
    "Parser",
    "Redactor",
    "Tokenizer",
]
