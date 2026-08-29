"""Pure values and the decisions made over them.

This package imports nothing outside the Python standard library, and nothing
from any other layer. Everything that decides what a package contains, what it
admits to leaving out, and whether a citation resolves lives here.

See ``docs/adr/0001-the-domain-depends-on-nothing.md`` for why, and
``tests/test_architecture.py`` for the assertion that it is still true.
"""

from __future__ import annotations
