"""Ranking model — wraps the full ranking pipeline for EVEE evaluation.

Registered as ``@model("cpl-ranking")`` for EVEE evaluation.

Calls ``recon_raw_signals()`` to get the candidate pool, then runs
gate → ranker → cutoff to produce a ranked DefFact list.
"""

from __future__ import annotations

import httpx
from evee import model


@model("cpl-ranking")
class RankingModel:
    """Wraps the ranking pipeline for EVEE benchmarking.

    Config args:
        daemon_port: CodePlane daemon port (default 7777)
        timeout: MCP call timeout in seconds (default 300)
    """

    def __init__(self, daemon_port: int = 7777, timeout: int = 300, **kwargs: object) -> None:
        self.mcp_url = f"http://127.0.0.1:{daemon_port}/mcp"
        self.timeout = timeout

    def _init_session(self) -> str:
        """Create a fresh MCP session."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        payload = {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "cpl-ranking-bench", "version": "1.0"},
            },
        }
        r = httpx.post(self.mcp_url, json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        session_id = r.headers.get("mcp-session-id", "")

        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        httpx.post(
            self.mcp_url,
            json=notif,
            headers={**headers, "Mcp-Session-Id": session_id},
            timeout=self.timeout,
        )
        return session_id

    def infer(self, input: dict) -> dict:  # noqa: A002
        """Run raw signals + ranking pipeline for a single query.

        Expects ``input["query_text"]``.
        Returns ranked DefFact list, gate prediction, and predicted N.
        """
        raise NotImplementedError
