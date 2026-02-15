"""Tests for run_test_targets broad run confirmation.

Tests the two-phase confirmation required when using target_filter
without explicit targets or affected_by.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeplane.mcp.tools.testing import _BROAD_RUN_TOKEN_KEY


@pytest.fixture
def mock_session() -> MagicMock:
    """Create a mock session with fingerprints storage."""
    session = MagicMock()
    session.fingerprints = {}
    return session


@pytest.fixture
def mock_session_manager(mock_session: MagicMock) -> MagicMock:
    """Create a mock session manager."""
    manager = MagicMock()
    manager.get_or_create.return_value = mock_session
    return manager


@pytest.fixture
def mock_test_ops() -> AsyncMock:
    """Create a mock TestOps with proper typed progress.

    Matches TestResult structure: result.run_status.progress.cases.total
    """
    ops = AsyncMock()

    # Create properly typed mock progress with int values
    progress = MagicMock()
    progress.total = 5
    progress.completed = 0
    progress.passed = 0
    progress.failed = 0
    progress.skipped = 0

    # Progress.cases with proper int attributes
    progress.cases = MagicMock()
    progress.cases.total = 5
    progress.cases.passed = 3
    progress.cases.failed = 0
    progress.cases.skipped = 0
    progress.cases.errors = 0

    # Progress.targets
    progress.targets = MagicMock()
    progress.targets.total = 1
    progress.targets.completed = 1
    progress.targets.running = 0
    progress.targets.failed = 0

    # run_status must have progress, duration_seconds, compute_poll_hint
    run_status = MagicMock()
    run_status.run_id = "test-run-123"
    run_status.status = "completed"
    run_status.duration_seconds = 1.5
    run_status.artifact_dir = "/tmp/test"
    run_status.progress = progress
    run_status.failures = []
    run_status.compute_poll_hint.return_value = None

    # TestResult structure
    result = MagicMock()
    result.action = "run"
    result.run_status = run_status

    ops.run.return_value = result
    return ops


class TestBroadRunPhase1Blocking:
    """Tests for Phase 1: target_filter without confirmation gets blocked."""

    @pytest.mark.asyncio
    async def test_target_filter_alone_returns_token(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Using target_filter alone returns a confirmation token."""
        _ = mock_session  # Fixture dependency for mock_session_manager
        from codeplane.mcp.tools.testing import register_tools

        mcp = MagicMock()
        tools: dict[str, Any] = {}

        def capture_tool(fn: Any) -> Any:
            tools[fn.__name__] = fn
            return fn

        mcp.tool = capture_tool

        app_ctx = MagicMock()
        app_ctx.session_manager = mock_session_manager
        app_ctx.test_ops = AsyncMock()

        register_tools(mcp, app_ctx)
        run_test_targets = tools["run_test_targets"]

        ctx = MagicMock()
        ctx.session_id = "test-session"

        result = await run_test_targets(
            ctx,
            targets=None,
            affected_by=None,
            target_filter="tests/mcp/",
            test_filter=None,
            tags=None,
            failed_only=False,
            parallelism=None,
            timeout_sec=None,
            fail_fast=False,
            coverage=False,
            coverage_dir=None,
            confirm_broad_run=None,
            confirmation_token=None,
        )

        assert result["run_status"]["status"] == "blocked"
        assert result.get("requires_confirmation") is True
        assert "confirmation_token" in result
        assert len(result["confirmation_token"]) > 10

    @pytest.mark.asyncio
    async def test_token_stored_in_session(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Token is stored in session fingerprints."""
        from codeplane.mcp.tools.testing import register_tools

        mcp = MagicMock()
        tools: dict[str, Any] = {}
        mcp.tool = lambda fn: tools.setdefault(fn.__name__, fn) or fn

        app_ctx = MagicMock()
        app_ctx.session_manager = mock_session_manager
        app_ctx.test_ops = AsyncMock()

        register_tools(mcp, app_ctx)
        run_test_targets = tools["run_test_targets"]

        ctx = MagicMock()
        ctx.session_id = "test-session"

        result = await run_test_targets(
            ctx,
            targets=None,
            affected_by=None,
            target_filter="tests/",
            test_filter=None,
            tags=None,
            failed_only=False,
            parallelism=None,
            timeout_sec=None,
            fail_fast=False,
            coverage=False,
            coverage_dir=None,
            confirm_broad_run=None,
            confirmation_token=None,
        )

        assert _BROAD_RUN_TOKEN_KEY in mock_session.fingerprints
        stored_token = mock_session.fingerprints[_BROAD_RUN_TOKEN_KEY]
        assert stored_token == result["confirmation_token"]


class TestBroadRunPhase2Validation:
    """Tests for Phase 2: Validating confirmation token and reason."""

    @pytest.mark.asyncio
    async def test_wrong_token_rejected(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Wrong token is rejected."""
        from codeplane.mcp.tools.testing import register_tools

        mcp = MagicMock()
        tools: dict[str, Any] = {}
        mcp.tool = lambda fn: tools.setdefault(fn.__name__, fn) or fn

        app_ctx = MagicMock()
        app_ctx.session_manager = mock_session_manager
        app_ctx.test_ops = AsyncMock()

        register_tools(mcp, app_ctx)
        run_test_targets = tools["run_test_targets"]

        ctx = MagicMock()
        ctx.session_id = "test-session"
        mock_session.fingerprints[_BROAD_RUN_TOKEN_KEY] = "correct-token-123"

        result = await run_test_targets(
            ctx,
            targets=None,
            affected_by=None,
            target_filter="tests/",
            test_filter=None,
            tags=None,
            failed_only=False,
            parallelism=None,
            timeout_sec=None,
            fail_fast=False,
            coverage=False,
            coverage_dir=None,
            confirm_broad_run="valid reason over 15 chars",
            confirmation_token="wrong-token",
        )

        assert result["error"] == "TOKEN_MISMATCH"

    @pytest.mark.asyncio
    async def test_short_reason_rejected(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Reason under 15 chars is rejected."""
        from codeplane.mcp.tools.testing import register_tools

        mcp = MagicMock()
        tools: dict[str, Any] = {}
        mcp.tool = lambda fn: tools.setdefault(fn.__name__, fn) or fn

        app_ctx = MagicMock()
        app_ctx.session_manager = mock_session_manager
        app_ctx.test_ops = AsyncMock()

        register_tools(mcp, app_ctx)
        run_test_targets = tools["run_test_targets"]

        ctx = MagicMock()
        ctx.session_id = "test-session"

        token = "valid-token-xyz"
        mock_session.fingerprints[_BROAD_RUN_TOKEN_KEY] = token

        result = await run_test_targets(
            ctx,
            targets=None,
            affected_by=None,
            target_filter="tests/",
            test_filter=None,
            tags=None,
            failed_only=False,
            parallelism=None,
            timeout_sec=None,
            fail_fast=False,
            coverage=False,
            coverage_dir=None,
            confirm_broad_run="short",
            confirmation_token=token,
        )

        assert result["error"] == "REASON_TOO_SHORT"


class TestBroadRunSuccessfulConfirmation:
    """Tests for successful broad run confirmation."""

    @pytest.mark.asyncio
    async def test_valid_confirmation_proceeds(
        self, mock_session: MagicMock, mock_session_manager: MagicMock, mock_test_ops: AsyncMock
    ) -> None:
        """Valid token + reason proceeds with run."""
        from codeplane.mcp.tools.testing import register_tools

        mcp = MagicMock()
        tools: dict[str, Any] = {}
        mcp.tool = lambda fn: tools.setdefault(fn.__name__, fn) or fn

        app_ctx = MagicMock()
        app_ctx.session_manager = mock_session_manager
        app_ctx.test_ops = mock_test_ops

        register_tools(mcp, app_ctx)
        run_test_targets = tools["run_test_targets"]

        ctx = MagicMock()
        ctx.session_id = "test-session"

        token = "valid-token-abc123"
        mock_session.fingerprints[_BROAD_RUN_TOKEN_KEY] = token

        result = await run_test_targets(
            ctx,
            targets=None,
            affected_by=None,
            target_filter="tests/mcp/",
            test_filter=None,
            tags=None,
            failed_only=False,
            parallelism=None,
            timeout_sec=None,
            fail_fast=False,
            coverage=False,
            coverage_dir=None,
            confirm_broad_run="testing the full mcp test suite",
            confirmation_token=token,
        )

        mock_test_ops.run.assert_called_once()
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_token_cleared_after_use(
        self, mock_session: MagicMock, mock_session_manager: MagicMock, mock_test_ops: AsyncMock
    ) -> None:
        """Token is cleared from session after successful use."""
        from codeplane.mcp.tools.testing import register_tools

        mcp = MagicMock()
        tools: dict[str, Any] = {}
        mcp.tool = lambda fn: tools.setdefault(fn.__name__, fn) or fn

        app_ctx = MagicMock()
        app_ctx.session_manager = mock_session_manager
        app_ctx.test_ops = mock_test_ops

        register_tools(mcp, app_ctx)
        run_test_targets = tools["run_test_targets"]

        ctx = MagicMock()
        ctx.session_id = "test-session"

        token = "single-use-token"
        mock_session.fingerprints[_BROAD_RUN_TOKEN_KEY] = token

        await run_test_targets(
            ctx,
            targets=None,
            affected_by=None,
            target_filter="tests/",
            test_filter=None,
            tags=None,
            failed_only=False,
            parallelism=None,
            timeout_sec=None,
            fail_fast=False,
            coverage=False,
            coverage_dir=None,
            confirm_broad_run="need to run full suite for CI validation",
            confirmation_token=token,
        )

        assert _BROAD_RUN_TOKEN_KEY not in mock_session.fingerprints


class TestNoConfirmationRequired:
    """Tests for paths that don't require confirmation."""

    @pytest.mark.asyncio
    async def test_explicit_targets_no_confirmation(
        self, mock_session: MagicMock, mock_session_manager: MagicMock, mock_test_ops: AsyncMock
    ) -> None:
        """Explicit targets don't require confirmation."""
        _ = mock_session  # Fixture dependency for mock_session_manager
        from codeplane.mcp.tools.testing import register_tools

        mcp = MagicMock()
        tools: dict[str, Any] = {}
        mcp.tool = lambda fn: tools.setdefault(fn.__name__, fn) or fn

        app_ctx = MagicMock()
        app_ctx.session_manager = mock_session_manager
        app_ctx.test_ops = mock_test_ops

        register_tools(mcp, app_ctx)
        run_test_targets = tools["run_test_targets"]

        ctx = MagicMock()
        ctx.session_id = "test-session"

        result = await run_test_targets(
            ctx,
            targets=["test:tests/foo.py", "test:tests/bar.py"],
            affected_by=None,
            target_filter=None,
            test_filter=None,
            tags=None,
            failed_only=False,
            parallelism=None,
            timeout_sec=None,
            fail_fast=False,
            coverage=False,
            coverage_dir=None,
            confirm_broad_run=None,
            confirmation_token=None,
        )

        mock_test_ops.run.assert_called_once()
        assert result.get("requires_confirmation") is not True
