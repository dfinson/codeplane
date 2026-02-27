"""Recon model — calls CodePlane recon via MCP and returns file lists with tiers.

Registered as ``@model("cpl-recon")`` for EVEE evaluation.
"""

from __future__ import annotations

import json
import os
import re

import httpx
from evee import model


@model("cpl-recon")
class ReconModel:
    """Wraps the CodePlane recon MCP tool for EVEE benchmarking.

    Config args (cartesian product):
        daemon_port: CodePlane daemon port (default 7777)
        timeout: MCP call timeout in seconds (default 120)
    """

    def __init__(self, daemon_port: int = 7777, timeout: int = 300, **kwargs: object) -> None:
        self.mcp_url = f"http://127.0.0.1:{daemon_port}/mcp"
        self.timeout = timeout
        self._target_repo = os.environ.get(
            "CPL_BENCH_TARGET_REPO",
            os.path.expanduser("~/wsl-repos/evees/evee_cpl/evee"),
        )

    # ── MCP session management ───────────────────────────────────────

    def _init_session(self) -> str:
        """Create a fresh MCP session (one per query to avoid consecutive-recon guards)."""
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
                "clientInfo": {"name": "cpl-bench", "version": "1.0"},
            },
        }
        r = httpx.post(self.mcp_url, json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        session_id = r.headers.get("mcp-session-id", "")

        # Send initialized notification
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        httpx.post(
            self.mcp_url,
            json=notif,
            headers={**headers, "Mcp-Session-Id": session_id},
            timeout=self.timeout,
        )
        return session_id

    # ── EVEE infer() ─────────────────────────────────────────────────

    def infer(self, input: dict) -> dict:  # noqa: A002
        """Call recon for a single query and return structured results.

        Expects ``input["task"]`` (query text).
        Returns dict with ``returned_files``, ``returned_tiers``, ``returned_scores``.
        """
        task = input["task"]
        session_id = self._init_session()

        payload = {
            "jsonrpc": "2.0",
            "id": "recon-1",
            "method": "tools/call",
            "params": {"name": "recon", "arguments": {"task": task}},
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Session-Id": session_id,
        }

        r = httpx.post(self.mcp_url, json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        data = self._parse_response(r.json())

        returned_files: list[str] = []
        returned_tiers: dict[str, str] = {}
        returned_scores: dict[str, float] = {}

        for entry in data.get("files", []):
            path = entry.get("path", "")
            if not path:
                continue
            returned_files.append(path)
            returned_tiers[path] = entry.get("tier", "lite")
            returned_scores[path] = float(entry.get("combined_score", 0.0))

        return {
            "returned_files": returned_files,
            "returned_tiers": returned_tiers,
            "returned_scores": returned_scores,
            "file_count": len(returned_files),
        }

    # ── Response parsing ─────────────────────────────────────────────

    def _parse_response(self, raw: dict) -> dict:
        """Parse MCP JSON-RPC response, handling inline and resource delivery."""
        result = raw.get("result", {})
        content = result.get("content", [])

        for item in content:
            if item.get("type") != "text":
                continue
            try:
                data = json.loads(item["text"])
            except (json.JSONDecodeError, TypeError):
                continue

            # Resource delivery — read from disk cache
            if data.get("delivery") == "resource":
                cached = self._read_cache(data)
                if cached:
                    return cached

            # Inline delivery
            if "files" in data:
                return data

        return {"files": []}

    def _read_cache(self, data: dict) -> dict | None:
        """Read recon result from cache file for resource delivery."""
        hint = data.get("agentic_hint", "")
        match = re.search(r"\.codeplane/cache/recon_result/([a-f0-9]+)\.json", hint)
        if not match:
            return None

        cache_rel = f".codeplane/cache/recon_result/{match.group(1)}.json"
        cache_path = os.path.join(self._target_repo, cache_rel)
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                return json.load(f)
        return None
