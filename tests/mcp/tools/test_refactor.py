"""Tests for MCP refactor tools.

Verifies summary helpers, serialization, and recon/justification gates.
"""

from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from codeplane.mcp._compat import get_tools_sync
from codeplane.mcp.errors import MCPError, MCPErrorCode
from codeplane.mcp.tools.refactor import (
    _display_refactor,
    _serialize_refactor_result,
    _summarize_refactor,
)


class TestSummarizeRefactor:
    """Tests for _summarize_refactor helper."""

    def test_cancelled(self) -> None:
        """Cancelled status."""
        result = _summarize_refactor(status="cancelled", files_affected=0, preview=None)
        assert "cancelled" in result

    def test_applied(self) -> None:
        """Applied status."""
        result = _summarize_refactor(status="applied", files_affected=5, preview=None)
        assert "applied" in result
        assert "5 files" in result

    def test_pending_preview(self) -> None:
        """Pending with preview."""
        preview = MagicMock()
        preview.high_certainty_count = 10
        preview.medium_certainty_count = 3
        preview.low_certainty_count = 2

        result = _summarize_refactor(status="pending", files_affected=4, preview=preview)
        assert "preview" in result
        assert "15 changes" in result  # 10 + 3 + 2
        assert "4 files" in result

    def test_pending_with_low_certainty(self) -> None:
        """Pending with low certainty matches."""
        preview = MagicMock()
        preview.high_certainty_count = 5
        preview.medium_certainty_count = 0
        preview.low_certainty_count = 3

        result = _summarize_refactor(status="pending", files_affected=2, preview=preview)
        assert "need review" in result
        assert "3" in result

    def test_unknown_status(self) -> None:
        """Unknown status returns status itself."""
        result = _summarize_refactor(status="unknown", files_affected=0, preview=None)
        assert result == "unknown"


class TestDisplayRefactor:
    """Tests for _display_refactor helper."""

    def test_cancelled_message(self) -> None:
        """Cancelled message."""
        result = _display_refactor(
            status="cancelled", files_affected=0, preview=None, refactor_id="abc123"
        )
        assert "cancelled" in result.lower()

    def test_applied_message(self) -> None:
        """Applied message."""
        result = _display_refactor(
            status="applied", files_affected=5, preview=None, refactor_id="abc123"
        )
        assert "applied" in result.lower()
        assert "5" in result

    def test_pending_message_with_id(self) -> None:
        """Pending shows refactor ID."""
        preview = MagicMock()
        preview.high_certainty_count = 10
        preview.medium_certainty_count = 0
        preview.low_certainty_count = 0

        result = _display_refactor(
            status="pending", files_affected=3, preview=preview, refactor_id="abc123"
        )
        assert "abc123" in result
        assert "preview" in result.lower() or "ready" in result.lower()

    def test_pending_with_review_needed(self) -> None:
        """Pending with low certainty shows review needed."""
        preview = MagicMock()
        preview.high_certainty_count = 5
        preview.medium_certainty_count = 0
        preview.low_certainty_count = 2

        result = _display_refactor(
            status="pending", files_affected=2, preview=preview, refactor_id="xyz789"
        )
        assert "review" in result.lower()


class TestSerializeRefactorResult:
    """Tests for _serialize_refactor_result helper."""

    def test_basic_result(self) -> None:
        """Basic result serialization."""
        result = MagicMock()
        result.refactor_id = "test-123"
        result.status = "pending"
        result.preview = None
        result.applied = None
        result.divergence = None

        output = _serialize_refactor_result(result)
        assert output["refactor_id"] == "test-123"
        assert output["status"] == "pending"
        assert "summary" in output
        assert "display_to_user" in output

    def test_with_preview(self) -> None:
        """Result with preview."""
        result = MagicMock()
        result.refactor_id = "test-456"
        result.status = "pending"
        result.applied = None
        result.divergence = None

        # Setup preview
        preview = MagicMock()
        preview.files_affected = 3
        preview.high_certainty_count = 10
        preview.medium_certainty_count = 2
        preview.low_certainty_count = 1
        preview.verification_required = False
        preview.edits = []
        result.preview = preview

        output = _serialize_refactor_result(result)
        assert "preview" in output
        assert output["preview"]["files_affected"] == 3
        assert output["preview"]["high_certainty_count"] == 10

    def test_with_applied_delta(self) -> None:
        """Result with applied delta."""
        result = MagicMock()
        result.refactor_id = "test-789"
        result.status = "applied"
        result.preview = None
        result.divergence = None

        applied = MagicMock()
        applied.files_changed = 5
        result.applied = applied

        output = _serialize_refactor_result(result)
        assert "5 files" in output["summary"]

    def test_with_divergence(self) -> None:
        """Result with divergence."""
        result = MagicMock()
        result.refactor_id = "test-div"
        result.status = "diverged"
        result.preview = None
        result.applied = None

        divergence = MagicMock()
        divergence.conflicting_hunks = 2
        divergence.resolution_options = ["abort", "force"]
        result.divergence = divergence

        output = _serialize_refactor_result(result)
        assert "divergence" in output
        assert output["divergence"]["conflicting_hunks"] == 2

    def test_preview_with_verification(self) -> None:
        """Preview requiring verification."""
        result = MagicMock()
        result.refactor_id = "test-verify"
        result.status = "pending"
        result.applied = None
        result.divergence = None

        preview = MagicMock()
        preview.files_affected = 2
        preview.high_certainty_count = 3
        preview.medium_certainty_count = 0
        preview.low_certainty_count = 2
        preview.verification_required = True
        preview.verification_guidance = "Review these files carefully"
        # Create a low-certainty hunk so low_certainty_matches is populated
        low_hunk = MagicMock()
        low_hunk.old = "old_text"
        low_hunk.new = "new_text"
        low_hunk.line = 10
        low_hunk.certainty = "low"
        file_edit = MagicMock()
        file_edit.path = "a.py"
        file_edit.hunks = [low_hunk]
        preview.edits = [file_edit]
        result.preview = preview

        output = _serialize_refactor_result(result)
        assert output["preview"]["verification_required"] is True
        assert len(output["preview"]["low_certainty_matches"]) == 1
        assert output["preview"]["low_certainty_matches"][0]["path"] == "a.py"
        assert output["preview"]["low_certainty_matches"][0]["certainty"] == "low"


class TestRefactorReconGate:
    """Tests for recon + justification gates on rename/move/impact."""

    @pytest.fixture
    def mcp_app(self) -> FastMCP:
        return FastMCP("test")

    @pytest.fixture
    def app_ctx(self) -> MagicMock:
        ctx = MagicMock()
        session = MagicMock()
        session.candidate_maps = {}  # Empty = no recon called
        session.edits_since_checkpoint = 0
        ctx.session_manager.get_or_create.return_value = session
        return ctx

    @pytest.fixture
    def fastmcp_ctx(self) -> MagicMock:
        ctx = MagicMock(spec=["session_id"])
        ctx.session_id = "test-session"
        return ctx

    @pytest.mark.asyncio
    async def test_rename_without_recon_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """refactor_rename without recon raises INVALID_PARAMS."""
        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        rename_fn = tools["refactor_rename"].fn

        with pytest.raises(MCPError) as exc_info:
            await rename_fn(
                ctx=fastmcp_ctx,
                symbol="OldName",
                new_name="NewName",
                justification="x" * 60,
            )
        assert exc_info.value.code == MCPErrorCode.INVALID_PARAMS
        assert "Recon required" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_rename_without_justification_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """refactor_rename with short justification raises INVALID_PARAMS."""
        session = app_ctx.session_manager.get_or_create.return_value
        session.candidate_maps = {"r1": {"r1:0": "foo.py"}}  # recon was called

        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        rename_fn = tools["refactor_rename"].fn

        with pytest.raises(MCPError) as exc_info:
            await rename_fn(
                ctx=fastmcp_ctx,
                symbol="OldName",
                new_name="NewName",
                justification="too short",
            )
        assert exc_info.value.code == MCPErrorCode.INVALID_PARAMS
        assert "justification" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_move_without_recon_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """refactor_move without recon raises INVALID_PARAMS."""
        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        move_fn = tools["refactor_move"].fn

        with pytest.raises(MCPError) as exc_info:
            await move_fn(
                ctx=fastmcp_ctx,
                from_path="old.py",
                to_path="new.py",
                justification="x" * 60,
            )
        assert exc_info.value.code == MCPErrorCode.INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_impact_without_recon_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """refactor_impact without recon raises INVALID_PARAMS."""
        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        impact_fn = tools["refactor_impact"].fn

        with pytest.raises(MCPError) as exc_info:
            await impact_fn(
                ctx=fastmcp_ctx,
                target="SomeSymbol",
                justification="x" * 60,
            )
        assert exc_info.value.code == MCPErrorCode.INVALID_PARAMS


# =============================================================================
# refactor_plan tool
# =============================================================================


class TestRefactorPlan:
    """Tests for refactor_plan tool with batching enforcement."""

    _DESCRIPTION = "Updating foo.py to fix the return value of the main function for correctness"

    @pytest.fixture
    def mcp_app(self) -> FastMCP:
        return FastMCP("test")

    @pytest.fixture
    def app_ctx(self) -> MagicMock:
        ctx = MagicMock()
        session = MagicMock()
        session.candidate_maps = {
            "r1": {"r1:0": "foo.py", "r1:1": "bar.py"},
        }
        session.counters = {
            "resolved_files": {"foo.py": "a" * 64, "bar.py": "b" * 64},
        }
        session.edit_tickets = {}
        session.active_plan = None
        session.read_only = False
        session.edits_since_checkpoint = 0
        ctx.session_manager.get_or_create.return_value = session
        return ctx

    @pytest.fixture
    def fastmcp_ctx(self) -> MagicMock:
        ctx = MagicMock(spec=["session_id"])
        ctx.session_id = "test-session"
        return ctx

    @pytest.mark.asyncio
    async def test_plan_creates_plan_with_tickets(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Basic plan creation mints edit tickets and sets active_plan."""
        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        plan_fn = tools["refactor_plan"].fn

        result = await plan_fn(
            ctx=fastmcp_ctx,
            edit_targets=["r1:0"],
            description=self._DESCRIPTION,
            expected_edit_calls=1,
            batch_justification=None,
            recon_id=None,
        )

        assert "plan_id" in result
        assert len(result["edit_targets"]) == 1
        assert result["expected_edit_calls"] == 1
        session = app_ctx.session_manager.get_or_create.return_value
        assert session.active_plan is not None

    @pytest.mark.asyncio
    async def test_plan_default_one_call(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Default expected_edit_calls is 1."""
        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        plan_fn = tools["refactor_plan"].fn

        result = await plan_fn(
            ctx=fastmcp_ctx,
            edit_targets=["r1:0"],
            description=self._DESCRIPTION,
            expected_edit_calls=1,
            batch_justification=None,
            recon_id=None,
        )

        assert result["expected_edit_calls"] == 1

    @pytest.mark.asyncio
    async def test_plan_multi_call_requires_justification(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """expected_edit_calls > 1 without batch_justification raises."""
        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        plan_fn = tools["refactor_plan"].fn

        with pytest.raises(MCPError) as exc_info:
            await plan_fn(
                ctx=fastmcp_ctx,
                edit_targets=["r1:0"],
                description=self._DESCRIPTION,
                expected_edit_calls=2,
                batch_justification=None,
                recon_id=None,
            )
        assert "batch_justification" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_plan_short_justification_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """batch_justification under 100 chars raises."""
        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        plan_fn = tools["refactor_plan"].fn

        with pytest.raises(MCPError) as exc_info:
            await plan_fn(
                ctx=fastmcp_ctx,
                edit_targets=["r1:0"],
                description=self._DESCRIPTION,
                expected_edit_calls=2,
                batch_justification="too short",
                recon_id=None,
            )
        assert "batch_justification" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_plan_multi_with_valid_justification(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """expected_edit_calls > 1 with valid justification succeeds."""
        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        plan_fn = tools["refactor_plan"].fn

        result = await plan_fn(
            ctx=fastmcp_ctx,
            edit_targets=["r1:0", "r1:1"],
            description=self._DESCRIPTION,
            expected_edit_calls=2,
            batch_justification="x" * 110,
            recon_id=None,
        )

        assert result["expected_edit_calls"] == 2

    @pytest.mark.asyncio
    async def test_plan_rejects_unresolved_target(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Target not resolved via recon_resolve is rejected."""
        session = app_ctx.session_manager.get_or_create.return_value
        session.counters = {"resolved_files": {}}

        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        plan_fn = tools["refactor_plan"].fn

        with pytest.raises(MCPError, match="not been resolved"):
            await plan_fn(
                ctx=fastmcp_ctx,
                edit_targets=["r1:0"],
                description=self._DESCRIPTION,
                expected_edit_calls=1,
                batch_justification=None,
                recon_id=None,
            )

    @pytest.mark.asyncio
    async def test_plan_rejects_when_plan_exists(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Cannot create plan when one already active."""
        session = app_ctx.session_manager.get_or_create.return_value
        session.active_plan = MagicMock(plan_id="existing")

        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        plan_fn = tools["refactor_plan"].fn

        with pytest.raises(MCPError, match="Active plan"):
            await plan_fn(
                ctx=fastmcp_ctx,
                edit_targets=["r1:0"],
                description=self._DESCRIPTION,
                expected_edit_calls=1,
                batch_justification=None,
                recon_id=None,
            )

    @pytest.mark.asyncio
    async def test_plan_read_only_blocked(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Read-only session cannot create plans."""
        session = app_ctx.session_manager.get_or_create.return_value
        session.read_only = True

        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        plan_fn = tools["refactor_plan"].fn

        with pytest.raises(MCPError, match="read-only"):
            await plan_fn(
                ctx=fastmcp_ctx,
                edit_targets=["r1:0"],
                description=self._DESCRIPTION,
                expected_edit_calls=1,
                batch_justification=None,
                recon_id=None,
            )

    @pytest.mark.asyncio
    async def test_plan_mints_tickets_on_session(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Plan mints edit tickets into session.edit_tickets."""
        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        plan_fn = tools["refactor_plan"].fn

        session = app_ctx.session_manager.get_or_create.return_value

        await plan_fn(
            ctx=fastmcp_ctx,
            edit_targets=["r1:0"],
            description=self._DESCRIPTION,
            expected_edit_calls=1,
            batch_justification=None,
            recon_id=None,
        )

        assert len(session.edit_tickets) == 1
        ticket = next(iter(session.edit_tickets.values()))
        assert ticket.path == "foo.py"
        assert ticket.issued_by == "plan"

    @pytest.mark.asyncio
    async def test_plan_empty_targets_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Empty edit_targets raises."""
        from codeplane.mcp.tools.refactor import register_tools

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        plan_fn = tools["refactor_plan"].fn

        with pytest.raises(MCPError, match="empty"):
            await plan_fn(
                ctx=fastmcp_ctx,
                edit_targets=[],
                description=self._DESCRIPTION,
                expected_edit_calls=1,
                batch_justification=None,
                recon_id=None,
            )
