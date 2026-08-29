"""The agent-facing surface. See ``docs/adr/0012-an-agent-facing-surface.md``."""

from __future__ import annotations

from .server import TOOLS, McpServer, serve

__all__ = ["TOOLS", "McpServer", "serve"]
