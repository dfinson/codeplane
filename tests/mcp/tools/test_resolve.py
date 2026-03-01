"""Tests for recon_resolve tool (resolve.py).

Covers:
- ResolveTarget model validation
- _compute_file_sha256
- recon_resolve handler: full file, span, binary, not-found, candidate_id gate
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

_VALID_JUSTIFICATION = "Resolving files for test validation -- complete working bundle needed"


# =============================================================================
# ResolveTarget Model
# =============================================================================


class TestResolveTarget:
    """Tests for ResolveTarget Pydantic model."""

    def test_candidate_id_only(self) -> None:
        t = ResolveTarget(candidate_id="r:0")
        assert t.candidate_id == "r:0"
        assert t.start_line is None
        assert t.end_line is None

    def test_with_span(self) -> None:
        t = ResolveTarget(candidate_id="r:1", start_line=10, end_line=20)
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
        session.counters = {}
        session.candidate_maps = {}
        session.edit_tickets = {}
        session.last_recon_id = "test-recon-id"
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

    def _add_candidate(
        self,
        app_ctx: MagicMock,
        candidate_id: str,
        path: str,
        recon_id: str = "r",
    ) -> None:
        """Register a candidate_id -> path mapping on the mock session."""
        session = app_ctx.session_manager.get_or_create.return_value
        if recon_id not in session.candidate_maps:
            session.candidate_maps[recon_id] = {}
        session.candidate_maps[recon_id][candidate_id] = path

    @pytest.mark.asyncio
    async def test_resolve_full_file(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Resolve full file returns content, sha256, and candidate_id."""
        self._write_file(tmp_path, "hello.py", "print('hello')\n")
        self._add_candidate(app_ctx, "r:0", "hello.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        assert "resolved" in result
        assert len(result["resolved"]) == 1
        r = result["resolved"][0]
        assert r["candidate_id"] == "r:0"
        assert r["path"] == "hello.py"
        assert r["content"] == "print('hello')\n"
        assert "file_sha256" in r
        assert r["line_count"] == 1

    @pytest.mark.asyncio
    async def test_resolve_span(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Resolve span returns only requested lines."""
        content = "\n".join(f"line{i}" for i in range(1, 21)) + "\n"
        self._write_file(tmp_path, "big.py", content)
        self._add_candidate(app_ctx, "r:0", "big.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0", start_line=5, end_line=10)],
            justification=_VALID_JUSTIFICATION,
        )

        r = result["resolved"][0]
        assert "span" in r
        assert r["span"]["start_line"] == 5
        assert r["span"]["end_line"] == 10
        assert "line5" in r["content"]
        assert "line10" in r["content"]
        # Full file sha -- not span sha
        assert r["line_count"] == 20

    @pytest.mark.asyncio
    async def test_resolve_file_not_found(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
    ) -> None:
        """Mapped candidate_id pointing to non-existent file returns error."""
        self._add_candidate(app_ctx, "r:0", "nonexistent.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        assert len(result["resolved"]) == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["candidate_id"] == "r:0"
        assert result["errors"][0]["path"] == "nonexistent.py"

    @pytest.mark.asyncio
    async def test_resolve_binary_file(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Binary file returns error entry."""
        fp = tmp_path / "image.dat"
        fp.write_bytes(b"\x00\x01\x02\x03")
        self._add_candidate(app_ctx, "r:0", "image.dat")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        assert len(result["errors"]) == 1
        assert "Binary" in result["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_resolve_empty_targets_error(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
    ) -> None:
        """Empty targets list raises MCPError."""
        self._add_candidate(app_ctx, "r:0", "dummy.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        from codeplane.mcp.errors import MCPError

        with pytest.raises(MCPError, match="empty"):
            await resolve_fn(
                ctx=fastmcp_ctx,
                targets=[],
                justification=_VALID_JUSTIFICATION,
            )

    @pytest.mark.asyncio
    async def test_resolve_unknown_candidate_id(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Unknown candidate_id returns error entry (not in any recon map)."""
        self._write_file(tmp_path, "some.py", "content\n")
        self._add_candidate(app_ctx, "r:0", "some.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="unknown:99")],
            justification=_VALID_JUSTIFICATION,
        )

        assert len(result["resolved"]) == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["candidate_id"] == "unknown:99"
        assert "Unknown candidate_id" in result["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_resolve_no_candidate_maps_returns_recon_required(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
    ) -> None:
        """With no candidate maps (no prior recon), returns RECON_REQUIRED."""
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        assert "error" in result
        assert result["error"]["code"] == "RECON_REQUIRED"

    @pytest.mark.asyncio
    async def test_resolve_justification_too_short(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
    ) -> None:
        """Justification under 50 chars raises MCPError."""
        self._add_candidate(app_ctx, "r:0", "dummy.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        from codeplane.mcp.errors import MCPError

        with pytest.raises(MCPError, match="justification"):
            await resolve_fn(
                ctx=fastmcp_ctx,
                targets=[ResolveTarget(candidate_id="r:0")],
                justification="too short",
            )

    @pytest.mark.asyncio
    async def test_resolve_tracks_sha_in_session(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Resolved files' sha256 stored in session counters."""
        content = "tracked content\n"
        self._write_file(tmp_path, "tracked.py", content)
        self._add_candidate(app_ctx, "r:0", "tracked.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        session = app_ctx.session_manager.get_or_create.return_value
        expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        resolved_files = session.counters.get("resolved_files", {})
        assert resolved_files["tracked.py"] == expected_sha

    @pytest.mark.asyncio
    async def test_resolve_agentic_hint(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Response includes agentic_hint with next steps."""
        self._write_file(tmp_path, "hint.py", "x = 1\n")
        self._add_candidate(app_ctx, "r:0", "hint.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        assert "agentic_hint" in result
        assert "refactor_plan" in result["agentic_hint"]
        assert "checkpoint" in result["agentic_hint"]

    @pytest.mark.asyncio
    async def test_resolve_too_many_targets(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
    ) -> None:
        """More than _MAX_TARGETS raises MCPError."""
        for i in range(11):
            self._add_candidate(app_ctx, f"r:{i}", f"f{i}.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        from codeplane.mcp.errors import MCPError

        targets = [ResolveTarget(candidate_id=f"r:{i}") for i in range(11)]
        with pytest.raises(MCPError, match="Too many targets"):
            await resolve_fn(
                ctx=fastmcp_ctx,
                targets=targets,
                justification=_VALID_JUSTIFICATION,
            )

    @pytest.mark.asyncio
    async def test_resolve_session_failure_returns_recon_required(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If session manager raises, no candidate maps -> RECON_REQUIRED."""
        self._write_file(tmp_path, "ok.py", "x = 1\n")
        app_ctx.session_manager.get_or_create.side_effect = RuntimeError("boom")

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        assert "error" in result
        assert result["error"]["code"] == "RECON_REQUIRED"

    @pytest.mark.asyncio
    async def test_resolve_read_failure(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """File that exists but cannot be read returns error entry."""
        fp = tmp_path / "unreadable.py"
        fp.write_text("content", encoding="utf-8")
        fp.chmod(0o000)
        self._add_candidate(app_ctx, "r:0", "unreadable.py")

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        assert len(result["errors"]) == 1
        assert "Read failed" in result["errors"][0]["error"]
        # Restore permissions for cleanup
        fp.chmod(0o644)

    @pytest.mark.asyncio
    async def test_resolve_span_too_large(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Span exceeding _MAX_SPAN_LINES returns error."""
        # _MAX_SPAN_LINES is 500, create a file with 600 lines
        content = "\n".join(f"line{i}" for i in range(600)) + "\n"
        self._write_file(tmp_path, "huge.py", content)
        self._add_candidate(app_ctx, "r:0", "huge.py")

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0", start_line=1, end_line=600)],
            justification=_VALID_JUSTIFICATION,
        )

        assert len(result["errors"]) == 1
        assert "Span too large" in result["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_resolve_more_than_five_files_hint(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Agentic hint shows '+N more' when >5 files resolved."""
        for i in range(7):
            self._write_file(tmp_path, f"f{i}.py", f"x = {i}\n")
            self._add_candidate(app_ctx, f"r:{i}", f"f{i}.py")

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id=f"r:{i}") for i in range(7)],
            justification=_VALID_JUSTIFICATION,
        )

        assert len(result["resolved"]) == 7
        assert "(+2 more)" in result["agentic_hint"]

    @pytest.mark.asyncio
    async def test_resolve_no_edit_tickets(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Resolve no longer mints edit tickets (moved to refactor_plan)."""
        content = "ticket content\n"
        self._write_file(tmp_path, "ticketed.py", content)
        self._add_candidate(app_ctx, "r:0", "ticketed.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        # Response no longer includes edit_ticket
        resolved = result["resolved"]
        assert len(resolved) == 1
        assert "edit_ticket" not in resolved[0]

    @pytest.mark.asyncio
    async def test_resolve_increments_batch_count(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Resolve increments resolve_batch_count on session."""
        self._write_file(tmp_path, "batch.py", "x = 1\n")
        self._add_candidate(app_ctx, "r:0", "batch.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        session = app_ctx.session_manager.get_or_create.return_value
        session.resolve_batch_count = 0

        await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        assert session.resolve_batch_count == 1

    @pytest.mark.asyncio
    async def test_resolve_agentic_hint_mentions_plan(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Agentic hint mentions refactor_plan (not edit_ticket)."""
        self._write_file(tmp_path, "hint2.py", "x = 1\n")
        self._add_candidate(app_ctx, "r:0", "hint2.py")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        hint = result["agentic_hint"]
        assert "refactor_plan" in hint
        assert "edit_ticket" not in hint

    @pytest.mark.asyncio
    async def test_resolve_without_recon_raises(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Gap 4: Resolve before recon raises MCPError."""
        session = app_ctx.session_manager.get_or_create.return_value
        session.last_recon_id = None
        session.candidate_maps = {"r": {"r:0": "nope.py"}}

        self._write_file(tmp_path, "nope.py", "x\n")
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        from codeplane.mcp.errors import MCPError

        with pytest.raises(MCPError) as exc_info:
            await resolve_fn(
                ctx=fastmcp_ctx,
                targets=[ResolveTarget(candidate_id="r:0")],
                justification=_VALID_JUSTIFICATION,
            )
        assert "Recon must be called before resolve" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_resolve_hard_gate_at_batch_3(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Gap 1: Resolve batch count >= 3 in write mode raises MCPError."""
        self._write_file(tmp_path, "gate.py", "x = 1\n")
        self._add_candidate(app_ctx, "r:0", "gate.py")

        session = app_ctx.session_manager.get_or_create.return_value
        session.resolve_batch_count = 3
        session.read_only = False

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        from codeplane.mcp.errors import MCPError

        with pytest.raises(MCPError) as exc_info:
            await resolve_fn(
                ctx=fastmcp_ctx,
                targets=[ResolveTarget(candidate_id="r:0")],
                justification=_VALID_JUSTIFICATION,
            )
        assert "Resolve call limit" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_resolve_warns_at_batch_2(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Gap 1: Resolve batch count == 2 in write mode adds warning."""
        self._write_file(tmp_path, "warn.py", "x = 1\n")
        self._add_candidate(app_ctx, "r:0", "warn.py")

        session = app_ctx.session_manager.get_or_create.return_value
        session.resolve_batch_count = 1  # incremented to 2 inside handler
        session.read_only = False

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        assert "WARNING" in result["agentic_hint"]
        assert "LAST" in result["agentic_hint"]

    @pytest.mark.asyncio
    async def test_resolve_batch_3_allowed_in_read_only(
        self,
        mcp_app: FastMCP,
        app_ctx: MagicMock,
        fastmcp_ctx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Gap 1: Read-only mode bypasses resolve hard gate."""
        self._write_file(tmp_path, "ro.py", "x = 1\n")
        self._add_candidate(app_ctx, "r:0", "ro.py")

        session = app_ctx.session_manager.get_or_create.return_value
        session.resolve_batch_count = 5
        session.read_only = True

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        resolve_fn = tools["recon_resolve"].fn

        result: dict[str, Any] = await resolve_fn(
            ctx=fastmcp_ctx,
            targets=[ResolveTarget(candidate_id="r:0")],
            justification=_VALID_JUSTIFICATION,
        )

        # Should succeed — read-only bypasses the hard gate
        assert len(result["resolved"]) == 1
