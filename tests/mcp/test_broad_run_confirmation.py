"""Tests for run_test_targets scope enforcement.

Tests the 3-tier test scope gating:
- Scoped runs (affected_by / explicit targets): no gate
- Semi-broad (target_filter only): requires recent scoped test + gate
- Full suite (no params): requires recent scoped test + gate (250 chars)
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeplane.mcp.gate import (
    CallPatternDetector,
    CallRecord,
    GateManager,
)


@pytest.fixture
def mock_session() -> MagicMock:
    """Create a mock session with real GateManager and PatternDetector."""
    session = MagicMock()
    session.fingerprints = {}
    session.gate_manager = GateManager()
    session.pattern_detector = CallPatternDetector()
    return session


@pytest.fixture
def mock_session_manager(mock_session: MagicMock) -> MagicMock:
    """Create a mock session manager."""
    manager = MagicMock()
    manager.get_or_create.return_value = mock_session
    return manager


@pytest.fixture
def mock_test_ops() -> AsyncMock:
    """Create a mock TestOps with proper typed progress."""
    ops = AsyncMock()

    progress = MagicMock()
    progress.total = 5
    progress.completed = 0
    progress.passed = 0
    progress.failed = 0
    progress.skipped = 0

    progress.cases = MagicMock()
    progress.cases.total = 5
    progress.cases.passed = 5
    progress.cases.failed = 0
    progress.cases.skipped = 0
    progress.cases.errors = 0

    progress.targets = MagicMock()
    progress.targets.total = 1
    progress.targets.completed = 0
    progress.targets.running = 0
    progress.targets.failed = 0

    run_status = MagicMock()
    run_status.run_id = "test-run-001"
    run_status.status = "running"
    run_status.duration_seconds = 1.0
    run_status.progress = progress
    run_status.artifact_dir = "/tmp/test-artifacts"

    result = MagicMock()
    result.run_status = run_status
    result.targets = []
    result.failure_summary = None

    ops.run.return_value = result
    return ops


def _get_run_test_targets(
    mock_session_manager: MagicMock,
    mock_test_ops: AsyncMock | None = None,
) -> Any:
    """Register tools and return the run_test_targets function."""
    from codeplane.mcp.tools.testing import register_tools

    mcp = MagicMock()
    tools: dict[str, Any] = {}
    mcp.tool = lambda fn: tools.setdefault(fn.__name__, fn) or fn

    app_ctx = MagicMock()
    app_ctx.session_manager = mock_session_manager
    app_ctx.test_ops = mock_test_ops or AsyncMock()

    register_tools(mcp, app_ctx)
    return tools["run_test_targets"]


def _inject_scoped_test(session: MagicMock) -> None:
    """Simulate a recent scoped test run in the pattern detector window."""
    session.pattern_detector.record(
        tool_name="run_test_targets",
        category_override="test_scoped",
    )


async def _call_run(
    run_test_targets: Any,
    *,
    target_filter: str | None = None,
    targets: list[str] | None = None,
    affected_by: list[str] | None = None,
    failed_only: bool = False,
    confirmation_token: str | None = None,
    confirm_broad_run: str | None = None,
) -> dict[str, Any]:
    """Call run_test_targets with defaults."""
    ctx = MagicMock()
    ctx.session_id = "test-session"
    return await run_test_targets(
        ctx,
        targets=targets,
        affected_by=affected_by,
        target_filter=target_filter,
        test_filter=None,
        tags=None,
        failed_only=failed_only,
        parallelism=None,
        timeout_sec=None,
        fail_fast=False,
        coverage=False,
        coverage_dir=None,
        confirm_broad_run=confirm_broad_run,
        confirmation_token=confirmation_token,
    )


# =============================================================================
# Prerequisite enforcement: scoped test required
# =============================================================================


class TestScopedTestPrerequisite:
    """Tests that semi-broad and full-suite runs require a recent scoped test."""

    @pytest.mark.asyncio
    async def test_target_filter_without_scoped_test_blocked(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """target_filter alone without prior scoped test is hard-blocked."""
        _ = mock_session  # fixture dependency: sets up session on manager
        run = _get_run_test_targets(mock_session_manager)
        result = await _call_run(run, target_filter="tests/mcp/")

        assert result["status"] == "blocked"
        assert result["error"]["code"] == "SCOPED_TEST_REQUIRED"
        assert "gate" not in result  # No gate issued - hard block

    @pytest.mark.asyncio
    async def test_full_suite_without_scoped_test_blocked(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Full suite run without prior scoped test is hard-blocked."""
        _ = mock_session  # fixture dependency: sets up session on manager
        run = _get_run_test_targets(mock_session_manager)
        result = await _call_run(run)  # No params at all

        assert result["status"] == "blocked"
        assert result["error"]["code"] == "SCOPED_TEST_REQUIRED"
        assert "full test suite" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_scoped_test_unlocks_prerequisite(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """After a scoped test, semi-broad is gated (not hard-blocked)."""
        _inject_scoped_test(mock_session)
        run = _get_run_test_targets(mock_session_manager)
        result = await _call_run(run, target_filter="tests/mcp/")

        assert result["status"] == "blocked"
        # Now we get a gate (not SCOPED_TEST_REQUIRED)
        assert "gate" in result
        assert result["gate"]["kind"] == "broad_test_run"

    @pytest.mark.asyncio
    async def test_scoped_test_falls_off_window(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Scoped test evidence falls off after window fills with other calls."""
        _inject_scoped_test(mock_session)
        # Fill the window with other calls to push scoped test off
        for _ in range(15):
            mock_session.pattern_detector._window.append(
                CallRecord(
                    category="meta",
                    tool_name="describe",
                    files=[],
                    timestamp=time.monotonic(),
                    hit_count=0,
                )
            )

        run = _get_run_test_targets(mock_session_manager)
        result = await _call_run(run, target_filter="tests/mcp/")

        assert result["status"] == "blocked"
        assert result["error"]["code"] == "SCOPED_TEST_REQUIRED"


# =============================================================================
# Semi-broad (target_filter) gate validation
# =============================================================================


class TestSemiBroadGate:
    """Tests for target_filter gate with GateManager."""

    @pytest.mark.asyncio
    async def test_target_filter_issues_gate(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """target_filter with prerequisite met issues a gate."""
        _inject_scoped_test(mock_session)
        run = _get_run_test_targets(mock_session_manager)
        result = await _call_run(run, target_filter="tests/")

        assert result["status"] == "blocked"
        assert result["gate"]["kind"] == "broad_test_run"
        assert result["gate"]["reason_min_chars"] == 50
        assert len(result["gate"]["id"]) > 5

    @pytest.mark.asyncio
    async def test_wrong_token_rejected(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Wrong gate token is rejected."""
        _inject_scoped_test(mock_session)
        run = _get_run_test_targets(mock_session_manager)

        # Get the gate token first
        result1 = await _call_run(run, target_filter="tests/")
        assert result1["status"] == "blocked"

        # Try with wrong token
        result2 = await _call_run(
            run,
            target_filter="tests/",
            confirmation_token="wrong-token",
            confirm_broad_run="A" * 50,
        )
        assert result2["status"] == "blocked"
        assert result2["error"]["code"] == "GATE_VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_short_reason_rejected(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Reason under 50 chars is rejected."""
        _inject_scoped_test(mock_session)
        run = _get_run_test_targets(mock_session_manager)

        result1 = await _call_run(run, target_filter="tests/")
        token = result1["gate"]["id"]

        result2 = await _call_run(
            run,
            target_filter="tests/",
            confirmation_token=token,
            confirm_broad_run="too short",
        )
        assert result2["status"] == "blocked"
        assert result2["error"]["code"] == "GATE_VALIDATION_FAILED"
        assert "50 characters" in result2["error"]["message"]

    @pytest.mark.asyncio
    async def test_valid_confirmation_proceeds(
        self,
        mock_session: MagicMock,
        mock_session_manager: MagicMock,
        mock_test_ops: AsyncMock,
    ) -> None:
        """Valid token + reason proceeds with the test run."""
        _inject_scoped_test(mock_session)
        run = _get_run_test_targets(mock_session_manager, mock_test_ops)

        result1 = await _call_run(run, target_filter="tests/")
        token = result1["gate"]["id"]

        result2 = await _call_run(
            run,
            target_filter="tests/",
            confirmation_token=token,
            confirm_broad_run=(
                "I need to verify cross-module integration after refactoring "
                "the gate system across multiple files."
            ),
        )
        # Should have proceeded - no "blocked" status
        assert result2.get("status") != "blocked"
        assert "action" in result2


# =============================================================================
# Full-suite gate validation
# =============================================================================


class TestFullSuiteGate:
    """Tests for full suite gate (250-char reason)."""

    @pytest.mark.asyncio
    async def test_full_suite_issues_gate(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Full suite with prerequisite met issues a gate."""
        _inject_scoped_test(mock_session)
        run = _get_run_test_targets(mock_session_manager)
        result = await _call_run(run)

        assert result["status"] == "blocked"
        assert result["gate"]["kind"] == "full_test_suite"
        assert result["gate"]["reason_min_chars"] == 250

    @pytest.mark.asyncio
    async def test_full_suite_short_reason_rejected(
        self, mock_session: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """250 chars required for full suite - 100 is not enough."""
        _inject_scoped_test(mock_session)
        run = _get_run_test_targets(mock_session_manager)

        result1 = await _call_run(run)
        token = result1["gate"]["id"]

        result2 = await _call_run(
            run,
            confirmation_token=token,
            confirm_broad_run="A" * 100,  # Not enough - need 250
        )
        assert result2["status"] == "blocked"
        assert result2["error"]["code"] == "GATE_VALIDATION_FAILED"
        assert "250 characters" in result2["error"]["message"]


# =============================================================================
# Scoped runs: no gate
# =============================================================================


class TestNoConfirmationRequired:
    """Tests that scoped runs bypass all gating."""

    @pytest.mark.asyncio
    async def test_explicit_targets_no_confirmation(
        self,
        mock_session: MagicMock,
        mock_session_manager: MagicMock,
        mock_test_ops: AsyncMock,
    ) -> None:
        """Explicit targets proceed without any gate."""
        _ = mock_session  # fixture dependency: sets up session on manager
        run = _get_run_test_targets(mock_session_manager, mock_test_ops)
        result = await _call_run(run, targets=["test:tests/mcp/test_gate.py"])

        # Should proceed - no blocked status
        assert result.get("status") != "blocked"
        assert "action" in result

    @pytest.mark.asyncio
    async def test_failed_only_no_confirmation(
        self,
        mock_session: MagicMock,
        mock_session_manager: MagicMock,
        mock_test_ops: AsyncMock,
    ) -> None:
        """failed_only=True bypasses full-suite gate."""
        _ = mock_session  # fixture dependency: sets up session on manager
        run = _get_run_test_targets(mock_session_manager, mock_test_ops)
        result = await _call_run(run, failed_only=True)

        # Should proceed - failed_only excludes from full_suite detection
        assert result.get("status") != "blocked"
