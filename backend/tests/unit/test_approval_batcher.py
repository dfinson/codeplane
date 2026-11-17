"""Tests for backend.services.action_policy.batcher — ApprovalBatcher."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

from traceforge.governance import RecommendedAction

from backend.services.action_policy.batcher import (
    ApprovalBatcher,
    Batch,
    BatchResolution,
    GateAction,
    _action_description,
)


@dataclass
class FakeAction:
    kind: str = "shell"
    command: str | None = None
    path: str | None = None
    tool_name: str | None = None
    mcp_tool: str | None = None
    mcp_server: str | None = None
    job_id: str | None = None


@dataclass
class FakeClassification:
    recommended_action: RecommendedAction = RecommendedAction.ESCALATE
    reason_code: str = "blocked"
    reason: str = "blocked"
    risk_score: int = 0
    risk_band: str = "low"
    effect: str | None = None
    mechanism: str = "unknown"


# ── BatchResolution enum ──


class TestBatchResolution:
    def test_values(self):
        assert BatchResolution.approved == "approved"
        assert BatchResolution.rejected == "rejected"
        assert BatchResolution.partial == "partial"


# ── _action_description ──


class TestActionDescription:
    def test_command(self):
        action = FakeAction(command="rm -rf /tmp")
        assert _action_description(action) == "rm -rf /tmp"

    def test_tool_and_path(self):
        action = FakeAction(tool_name="Write", path="/src/app.py")
        assert _action_description(action) == "Write: /src/app.py"

    def test_tool_only(self):
        action = FakeAction(tool_name="Bash")
        assert _action_description(action) == "Tool: Bash"

    def test_mcp_tool(self):
        action = FakeAction(mcp_tool="lookup", mcp_server="repository")
        assert _action_description(action) == "MCP: repository/lookup"

    def test_path_only(self):
        action = FakeAction(path="/etc/passwd")
        assert _action_description(action) == "File: /etc/passwd"

    def test_kind_fallback(self):
        action = FakeAction(kind="file_write")
        assert _action_description(action) == "file_write"


# ── ApprovalBatcher ──


class TestApprovalBatcher:
    def test_init(self):
        bus = AsyncMock()
        batcher = ApprovalBatcher(bus)
        assert batcher._batch_window == 5.0

    def test_set_batch_window(self):
        bus = AsyncMock()
        batcher = ApprovalBatcher(bus)
        batcher.set_batch_window(10.0)
        assert batcher._batch_window == 10.0

    def test_get_batch_nonexistent(self):
        bus = AsyncMock()
        batcher = ApprovalBatcher(bus)
        assert batcher.get_batch("nonexistent") is None

    def test_get_pending_batches_empty(self):
        bus = AsyncMock()
        batcher = ApprovalBatcher(bus)
        assert batcher.get_pending_batches() == []
        assert batcher.get_pending_batches(job_id="job-1") == []

    def test_resolve_nonexistent_batch(self):
        bus = AsyncMock()
        batcher = ApprovalBatcher(bus)
        result = batcher.resolve_batch("nonexistent", BatchResolution.approved)
        assert result is False

    def test_cleanup_job_no_batch(self):
        bus = AsyncMock()
        batcher = ApprovalBatcher(bus)
        batcher.cleanup_job("job-1")  # Should not raise


# ── Batch.summarize ──


class TestBatchSummarize:
    def test_single_action(self):
        action = FakeAction(command="npm install lodash")
        ga = GateAction(id="1", action=action, classification=FakeClassification(), checkpoint_ref="")
        batch = Batch(id="b1", job_id="j1", actions=[ga])
        result = ApprovalBatcher._summarize(batch)
        assert result == "npm install lodash"

    def test_multiple_actions(self):
        a1 = GateAction(
            id="1",
            action=FakeAction(kind="shell"),
            classification=FakeClassification(),
            checkpoint_ref="",
        )
        a2 = GateAction(
            id="2",
            action=FakeAction(kind="file_write"),
            classification=FakeClassification(),
            checkpoint_ref="",
        )
        batch = Batch(id="b1", job_id="j1", actions=[a1, a2])
        result = ApprovalBatcher._summarize(batch)
        assert "2 actions" in result
        assert "file_write" in result
        assert "shell" in result
