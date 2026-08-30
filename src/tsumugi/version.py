"""The version, in a module that imports nothing.

``__init__.py`` re-exports it, and that is where a reader looks -- but the
public surface is the *interfaces* layer's to import, and the application layer
may not reach it (``tests/test_architecture.py``). So the string lives here,
classified like ``errors.py``: no imports, importable from anywhere.

It exists because ``build_context`` defaulted ``version`` to the empty string
and the published contract requires ``provenance.tsumugi_version`` to be
non-empty. The producer was emitting documents that failed its own frozen
schema whenever a caller did not pass a version, which nothing noticed until
the producer's real output was validated against the schema for the first time.
"""

from __future__ import annotations

from typing import Final

__all__ = ["__version__"]

__version__: Final = "0.1.0.dev0"
