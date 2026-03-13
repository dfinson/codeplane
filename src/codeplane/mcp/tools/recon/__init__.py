"""Recon MCP tools — task-aware context retrieval.

Three tools:
- ``recon``            — ranked semantic spans for a task query
- ``recon_map``        — repository structure map
- ``recon_raw_signals`` — raw retrieval signals (dev-mode only)

    from codeplane.mcp.tools.recon import register_tools

In-process APIs (no MCP server required):

    from codeplane.mcp.tools.recon import recon_pipeline, raw_signals_pipeline
"""

from __future__ import annotations

from codeplane.mcp.tools.recon.pipeline import recon_pipeline, register_tools
from codeplane.mcp.tools.recon.raw_signals import raw_signals_pipeline

__all__ = ["raw_signals_pipeline", "recon_pipeline", "register_tools"]
