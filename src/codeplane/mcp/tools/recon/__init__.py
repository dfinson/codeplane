"""Recon MCP tools — task-aware context retrieval.

Three tools:
- ``recon``            — ranked semantic spans for a task query
- ``recon_map``        — repository structure map
- ``recon_raw_signals`` — raw retrieval signals (dev-mode only)

    from codeplane.mcp.tools.recon import register_tools
"""

from __future__ import annotations

from codeplane.mcp.tools.recon.pipeline import register_tools

__all__ = ["register_tools"]
