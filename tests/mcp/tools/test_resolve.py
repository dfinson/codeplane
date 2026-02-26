"""Tests for recon_resolve tool (resolve.py).

Covers:
- ResolveTarget model validation
- _compute_file_sha256
- recon_resolve handler: full file, span, binary, not-found, flow gate
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from codeplane.mcp._compat import get_tools_sync
from codeplane.mcp.tools.resolve import (
    ResolveTarget,
    _compute_file_sha256,
    register_tools,
)

# =============================================================================
# ResolveTarget Model
# =============================================================================


class TestResolveTarget:
    """Tests for ResolveTarget Pydantic model."""

    def test_path_only(self) -> None:
        t = ResolveTarget(path="foo.py")
        assert t.path == "foo.py"
        assert t.start_line is None
        assert t.end_line is None

    def test_with_span(self) -> None:
        t = ResolveTarget(path="bar.py", start_line=10, end_line=20)
        assert t.start_line == 10
        assert t.end_line == 20


# =============================================================================
# _compute_file_sha256
# =============================================================================


class TestComputeFileSha256:
    """Tests for sha256 computation."""

    def test_known_hash(self) -> None:
        content = b"hello world\n"
        expected = hashlib.sha256(content).hexdigest()
        assert _compute_file_sha256(content) == expected

    def test_empty_content(self) -> None:
        assert _compute_file_sha256(b"") == hashlib.sha256(b"").hexdigest()


# =============================================================================
# recon_resolve handler
# =============================================================================


class TestReconResolve:
    """Integration tests for recon_resolve tool handler."""

    @pytest.fixture
    def mcp_app(self) -> FastMCP:
        return FastMCP("test")

    @pytest.fixture
    def app_ctx(self, tmp_path: Path) -> MagicMock:
        ctx = MagicMock()
        ctx.coordinator.repo_root = tmp_path
        session = MagicMock()
        session.counters = {"recon_called": 1}
        ctx.session_manager.get_or_create.return_value = session
        return ctx

    @pytest.fixture
    def fastmcp_ctx(self) -> MagicMock:
        ctx = MagicMock(spec=["session_id"])
        ctx.session_id = "test-session"
        return ctx

    def _write_file(self, repo_root: Path, rel_path: str, content: str) -> Path:
        fp = repo_root / rel_path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return fp

    @pytest.mark.asyncio
    async def test_resolve_full_file(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock, tmp_path: Path
    ) -> None:
        """Resolve full file returns content and sha256."""
        self._write_file(tmp_path, "hello.py", "print('hello')\n")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(path="hello.py")],
        )

        assert "resolved" in result
        assert len(result["resolved"]) == 1
        r = result["resolved"][0]
        assert r["path"] == "hello.py"
        assert r["content"] == "print('hello')\n"
        assert "file_sha256" in r
        assert r["line_count"] == 1

    @pytest.mark.asyncio
    async def test_resolve_span(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock, tmp_path: Path
    ) -> None:
        """Resolve span returns only requested lines."""
        content = "\n".join(f"line{i}" for i in range(1, 21)) + "\n"
        self._write_file(tmp_path, "big.py", content)
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(path="big.py", start_line=5, end_line=10)],
        )

        r = result["resolved"][0]
        assert "span" in r
        assert r["span"]["start_line"] == 5
        assert r["span"]["end_line"] == 10
        assert "line5" in r["content"]
        assert "line10" in r["content"]
        # Full file sha — not span sha
        assert r["line_count"] == 20

    @pytest.mark.asyncio
    async def test_resolve_file_not_found(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Non-existent file returns error entry."""
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(path="nonexistent.py")],
        )

        assert len(result["resolved"]) == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["path"] == "nonexistent.py"

    @pytest.mark.asyncio
    async def test_resolve_binary_file(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock, tmp_path: Path
    ) -> None:
        """Binary file returns error entry."""
        fp = tmp_path / "image.dat"
        fp.write_bytes(b"\x00\x01\x02\x03")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(path="image.dat")],
        )

        assert len(result["errors"]) == 1
        assert "Binary" in result["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_resolve_empty_targets_error(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Empty targets list raises MCPError."""
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        from codeplane.mcp.errors import MCPError

        with pytest.raises(MCPError, match="empty"):
            await resolve_fn(ctx=fastmcp_ctx, targets=[])

    @pytest.mark.asyncio
    async def test_resolve_flow_gate_no_recon(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock, tmp_path: Path
    ) -> None:
        """Without prior recon call, returns RECON_REQUIRED error."""
        self._write_file(tmp_path, "some.py", "content\n")
        # Override counters to simulate no recon called
        session = app_ctx.session_manager.get_or_create.return_value
        session.counters = {"recon_called": 0}

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(path="some.py")],
        )

        assert "error" in result
        assert result["error"]["code"] == "RECON_REQUIRED"

    @pytest.mark.asyncio
    async def test_resolve_tracks_sha_in_session(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock, tmp_path: Path
    ) -> None:
        """Resolved files' sha256 stored in session counters."""
        content = "tracked content\n"
        self._write_file(tmp_path, "tracked.py", content)
        session = app_ctx.session_manager.get_or_create.return_value
        session.counters = {"recon_called": 1}

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(path="tracked.py")],
        )

        expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        resolved_files = session.counters.get("resolved_files", {})
        assert resolved_files["tracked.py"] == expected_sha

    @pytest.mark.asyncio
    async def test_resolve_agentic_hint(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock, tmp_path: Path
    ) -> None:
        """Response includes agentic_hint with next steps."""
        self._write_file(tmp_path, "hint.py", "x = 1\n")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(path="hint.py")],
        )

        assert "agentic_hint" in result
        assert "refactor_edit" in result["agentic_hint"]
        assert "checkpoint" in result["agentic_hint"]
