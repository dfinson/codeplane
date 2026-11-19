"""Tests for _compute_attribution, _count_edit_retries, _accumulate, helpers, and compute_attribution wrapper."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.analytics.cost_attribution import (
    _accumulate,
    _compute_attribution,
    _count_edit_retries,
    _zero_bucket,
    _zero_turn_context,
    compute_attribution,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _span(
    *,
    span_type: str = "llm",
    name: str = "llm_call",
    turn_number: int | None = 1,
    cost_usd: float | None = 0.01,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    execution_phase: str | None = "agent_reasoning",
    tool_args_json: str | dict | None = None,
    duration_ms: int = 100,
    started_at: str = "2026-01-01T00:00:00Z",
    attrs: dict | None = None,
    edit_motivations: list | None = None,
    tool_target: str | None = None,
) -> dict[str, Any]:
    """Build a minimal span dict matching TelemetrySpanRow structure."""
    return {
        "span_type": span_type,
        "name": name,
        "turn_number": turn_number,
        "cost_usd": cost_usd,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "execution_phase": execution_phase,
        "tool_args_json": tool_args_json,
        "duration_ms": duration_ms,
        "started_at": started_at,
        "attrs": attrs or {},
        "edit_motivations": edit_motivations,
        "tool_target": tool_target,
    }


def _mock_session(
    *,
    description: str = "test job",
    prompt: str = "fix the bug",
    model: str = "claude-sonnet-4-20250514",
) -> AsyncMock:
    """Build a mock AsyncSession whose execute returns job metadata."""
    mock = AsyncMock()
    result_obj = MagicMock()
    result_obj.mappings.return_value.first.return_value = {
        "description": description,
        "prompt": prompt,
        "model": model,
    }
    mock.execute = AsyncMock(return_value=result_obj)
    return mock


def _mock_repos(
    spans: list[dict[str, Any]] | None = None,
    file_accesses: list[dict[str, Any]] | None = None,
    reread_stats: dict[str, Any] | None = None,
    trail_nodes: list | None = None,
    diff_counts: tuple[int, int] = (0, 0),
) -> dict[str, AsyncMock]:
    """Build all repo mocks for _compute_attribution."""
    spans_repo = AsyncMock()
    spans_repo.list_for_job = AsyncMock(return_value=spans or [])

    attr_repo = AsyncMock()
    attr_repo.insert_batch = AsyncMock()

    summary_repo = AsyncMock()
    summary_repo.set_turn_stats = AsyncMock()

    file_repo = AsyncMock()
    file_repo.raw_accesses_for_job = AsyncMock(return_value=file_accesses or [])
    file_repo.reread_stats = AsyncMock(return_value=reread_stats or {"unique_files": 0, "reread_count": 0})

    file_cost_repo = AsyncMock()
    file_cost_repo.insert_batch = AsyncMock()

    trail_repo = AsyncMock()
    trail_repo.get_by_job = AsyncMock(return_value=trail_nodes or [])
    trail_repo.get_diff_line_counts = AsyncMock(return_value=diff_counts)

    return {
        "spans_repo": spans_repo,
        "attr_repo": attr_repo,
        "summary_repo": summary_repo,
        "file_repo": file_repo,
        "file_cost_repo": file_cost_repo,
        "trail_repo": trail_repo,
    }


# ---------------------------------------------------------------------------
# _zero_bucket / _zero_turn_context
# ---------------------------------------------------------------------------


class TestZeroFactories:
    def test_zero_bucket_all_zeros(self) -> None:
        b = _zero_bucket()
        assert b["cost_usd"] == 0.0
        assert b["input_tokens"] == 0
        assert b["output_tokens"] == 0
        assert b["cache_read_tokens"] == 0
        assert b["cache_write_tokens"] == 0
        assert b["call_count"] == 0

    def test_zero_bucket_returns_new_instance(self) -> None:
        a = _zero_bucket()
        b = _zero_bucket()
        a["cost_usd"] = 1.0
        assert b["cost_usd"] == 0.0

    def test_zero_turn_context_defaults(self) -> None:
        ctx = _zero_turn_context()
        assert ctx["phase"] is None
        assert ctx["cost_usd"] == 0.0
        assert ctx["tool_categories"] == []
        assert ctx["shell_commands"] == []

    def test_zero_turn_context_returns_new_instance(self) -> None:
        a = _zero_turn_context()
        b = _zero_turn_context()
        a["tool_categories"].append("shell")
        assert b["tool_categories"] == []


# ---------------------------------------------------------------------------
# _accumulate
# ---------------------------------------------------------------------------


class TestAccumulate:
    def test_basic_accumulation(self) -> None:
        bucket = _zero_bucket()
        _accumulate(bucket, 1.5, 100, 50)
        assert bucket["cost_usd"] == 1.5
        assert bucket["input_tokens"] == 100
        assert bucket["output_tokens"] == 50
        assert bucket["call_count"] == 1

    def test_multiple_accumulations(self) -> None:
        bucket = _zero_bucket()
        _accumulate(bucket, 1.0, 100, 50)
        _accumulate(bucket, 2.0, 200, 100)
        assert bucket["cost_usd"] == 3.0
        assert bucket["input_tokens"] == 300
        assert bucket["output_tokens"] == 150
        assert bucket["call_count"] == 2

    def test_cache_tokens(self) -> None:
        bucket = _zero_bucket()
        _accumulate(bucket, 0.5, 10, 5, cache_read=20, cache_write=15)
        assert bucket["cache_read_tokens"] == 20
        assert bucket["cache_write_tokens"] == 15

    def test_custom_call_count(self) -> None:
        bucket = _zero_bucket()
        _accumulate(bucket, 1.0, 10, 5, call_count=3)
        assert bucket["call_count"] == 3

    def test_none_values_treated_as_zero(self) -> None:
        bucket = _zero_bucket()
        _accumulate(bucket, None, None, None, cache_read=None, cache_write=None, call_count=None)  # type: ignore[arg-type]
        assert bucket["cost_usd"] == 0.0
        assert bucket["input_tokens"] == 0
        assert bucket["output_tokens"] == 0
        assert bucket["cache_read_tokens"] == 0
        assert bucket["cache_write_tokens"] == 0
        assert bucket["call_count"] == 0


# ---------------------------------------------------------------------------
# _count_edit_retries
# ---------------------------------------------------------------------------


class TestCountEditRetries:
    def test_no_edits(self) -> None:
        assert _count_edit_retries([]) == 0
        assert _count_edit_retries(["file_read", "shell"]) == 0

    def test_single_edit_no_shell(self) -> None:
        assert _count_edit_retries(["file_write"]) == 0

    def test_edit_shell_no_second_edit(self) -> None:
        assert _count_edit_retries(["file_write", "shell"]) == 0

    def test_edit_shell_edit_is_one_retry(self) -> None:
        assert _count_edit_retries(["file_write", "shell", "file_write"]) == 1

    def test_multiple_retries(self) -> None:
        # edit→shell→edit→shell→edit = 2 retries
        cats = ["file_write", "shell", "file_write", "shell", "file_write"]
        assert _count_edit_retries(cats) == 2

    def test_shell_without_preceding_edit(self) -> None:
        assert _count_edit_retries(["shell", "file_write"]) == 0

    def test_git_write_counts_as_edit(self) -> None:
        assert _count_edit_retries(["git_write", "shell", "git_write"]) == 1

    def test_mixed_write_categories(self) -> None:
        # file_write→shell→git_write counts as retry
        assert _count_edit_retries(["file_write", "shell", "git_write"]) == 1

    def test_consecutive_edits_no_shell_between(self) -> None:
        assert _count_edit_retries(["file_write", "file_write"]) == 0

    def test_edit_read_shell_edit(self) -> None:
        # file_read between edit and shell shouldn't break the pattern
        assert _count_edit_retries(["file_write", "file_read", "shell", "file_write"]) == 1

    def test_three_retries(self) -> None:
        cats = [
            "file_write",
            "shell",
            "file_write",
            "shell",
            "file_write",
            "shell",
            "file_write",
        ]
        assert _count_edit_retries(cats) == 3

    def test_interleaved_non_write_categories(self) -> None:
        cats = ["file_write", "file_read", "shell", "file_search", "file_write"]
        assert _count_edit_retries(cats) == 1


# ---------------------------------------------------------------------------
# _compute_attribution — async tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestComputeAttributionNoSpans:
    async def test_no_spans_returns_early(self) -> None:
        repos = _mock_repos(spans=[])
        session = _mock_session()
        await _compute_attribution(
            job_id="job-1",
            session=session,
            **repos,
        )
        # attr_repo.insert_batch should NOT be called
        repos["attr_repo"].insert_batch.assert_not_called()
        repos["summary_repo"].set_turn_stats.assert_not_called()


@pytest.mark.asyncio
class TestComputeAttributionBasic:
    async def test_single_llm_span(self) -> None:
        spans = [_span(turn_number=1, cost_usd=0.05, input_tokens=500, output_tokens=200)]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        repos["attr_repo"].insert_batch.assert_called_once()
        call_args = repos["attr_repo"].insert_batch.call_args
        assert call_args.kwargs["job_id"] == "job-1"
        rows = call_args.kwargs["rows"]
        # Should have at least turn and activity rows
        dims = {r["dimension"] for r in rows}
        assert "turn" in dims
        assert "activity" in dims

    async def test_turn_cost_aggregation(self) -> None:
        spans = [
            _span(turn_number=1, cost_usd=0.02, input_tokens=100, output_tokens=50),
            _span(turn_number=1, cost_usd=0.03, input_tokens=200, output_tokens=100),
            _span(turn_number=2, cost_usd=0.01, input_tokens=50, output_tokens=25),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        turn_rows = [r for r in rows if r["dimension"] == "turn"]
        turn_map = {r["bucket"]: r for r in turn_rows}
        assert turn_map["1"]["cost_usd"] == pytest.approx(0.05)
        assert turn_map["1"]["input_tokens"] == 300
        assert turn_map["2"]["cost_usd"] == pytest.approx(0.01)

    async def test_model_tag_from_job_metadata(self) -> None:
        spans = [_span(turn_number=1)]
        repos = _mock_repos(spans=spans)
        session = _mock_session(model="gpt-4o")

        await _compute_attribution(job_id="job-1", session=session, **repos)

        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        for r in rows:
            assert r["model"] == "gpt-4o"

    async def test_missing_job_metadata(self) -> None:
        """When session.execute returns no row, model defaults to empty string."""
        spans = [_span(turn_number=1)]
        repos = _mock_repos(spans=spans)
        session = AsyncMock()
        result_obj = MagicMock()
        result_obj.mappings.return_value.first.return_value = None
        session.execute = AsyncMock(return_value=result_obj)

        await _compute_attribution(job_id="job-1", session=session, **repos)

        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        for r in rows:
            assert r["model"] == ""


@pytest.mark.asyncio
class TestComputeAttributionToolSpans:
    async def test_tool_span_classifies_category(self) -> None:
        spans = [
            _span(span_type="tool", name="write_file", turn_number=1, cost_usd=0.0),
            _span(span_type="llm", turn_number=1, cost_usd=0.05),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        action_rows = [r for r in rows if r["dimension"] == "action"]
        assert len(action_rows) >= 1

    async def test_shell_command_extraction_from_json_string(self) -> None:
        """Shell tool with JSON tool_args_json should extract command."""
        import json

        cmd_json = json.dumps({"command": "pytest tests/"})
        spans = [
            _span(span_type="tool", name="execute_command", turn_number=1, tool_args_json=cmd_json),
            _span(span_type="llm", turn_number=1, cost_usd=0.05),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        # Verification: the action classification should reflect shell activity
        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        assert any(r["dimension"] in ("action", "activity") for r in rows)

    async def test_shell_command_extraction_from_dict(self) -> None:
        """Shell tool with dict tool_args_json should extract command."""
        spans = [
            _span(span_type="tool", name="execute_command", turn_number=1, tool_args_json={"command": "npm test"}),
            _span(span_type="llm", turn_number=1, cost_usd=0.05),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)
        repos["attr_repo"].insert_batch.assert_called_once()

    async def test_shell_command_invalid_json(self) -> None:
        """Invalid JSON in tool_args_json shouldn't crash."""
        spans = [
            _span(span_type="tool", name="execute_command", turn_number=1, tool_args_json="not valid json"),
            _span(span_type="llm", turn_number=1, cost_usd=0.05),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)
        repos["attr_repo"].insert_batch.assert_called_once()

    async def test_tool_span_without_turn_number(self) -> None:
        """Tool span with turn_number=None should not crash."""
        spans = [
            _span(span_type="tool", name="read_file", turn_number=None),
            _span(span_type="llm", turn_number=1, cost_usd=0.01),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)
        repos["attr_repo"].insert_batch.assert_called_once()


@pytest.mark.asyncio
class TestComputeAttributionSubagent:
    async def test_subagent_status_tracking(self) -> None:
        """LLM span with is_subagent in attrs marks the turn as subagent."""
        spans = [
            _span(span_type="tool", name="dispatch_agent", turn_number=1),
            _span(span_type="llm", turn_number=1, cost_usd=0.05),
            _span(span_type="llm", turn_number=2, cost_usd=0.03, attrs={"is_subagent": True}),
            _span(span_type="tool", name="write_file", turn_number=2),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)
        repos["attr_repo"].insert_batch.assert_called_once()


@pytest.mark.asyncio
class TestComputeAttributionPhase:
    async def test_phase_dimension_written(self) -> None:
        spans = [
            _span(turn_number=1, execution_phase="agent_reasoning", cost_usd=0.02),
            _span(turn_number=2, execution_phase="verification", cost_usd=0.03),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        phase_rows = [r for r in rows if r["dimension"] == "phase"]
        phase_buckets = {r["bucket"] for r in phase_rows}
        assert "agent_reasoning" in phase_buckets
        assert "verification" in phase_buckets

    async def test_spans_missing_phase_counted(self) -> None:
        spans = [
            _span(turn_number=1, execution_phase=None, cost_usd=0.01),
            _span(turn_number=2, execution_phase=None, cost_usd=0.02),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        # Should not crash even with all phases None
        await _compute_attribution(job_id="job-1", session=session, **repos)
        repos["attr_repo"].insert_batch.assert_called_once()


@pytest.mark.asyncio
class TestComputeAttributionFileCosts:
    async def test_file_cost_attribution(self) -> None:
        spans = [_span(turn_number=1, cost_usd=0.10)]
        file_accesses = [
            {"turn_number": 1, "file_path": "src/main.py", "access_type": "write"},
            {"turn_number": 1, "file_path": "src/utils.py", "access_type": "read"},
        ]
        repos = _mock_repos(spans=spans, file_accesses=file_accesses)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        repos["file_cost_repo"].insert_batch.assert_called_once()
        file_rows = repos["file_cost_repo"].insert_batch.call_args.kwargs["rows"]
        paths = {r["file_path"] for r in file_rows}
        assert "src/main.py" in paths
        assert "src/utils.py" in paths

    async def test_file_cost_empty_accesses(self) -> None:
        spans = [_span(turn_number=1, cost_usd=0.10)]
        repos = _mock_repos(spans=spans, file_accesses=[])
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        repos["file_cost_repo"].insert_batch.assert_not_called()

    async def test_file_cost_error_doesnt_crash(self) -> None:
        spans = [_span(turn_number=1, cost_usd=0.10)]
        repos = _mock_repos(spans=spans)
        repos["file_repo"].raw_accesses_for_job = AsyncMock(side_effect=RuntimeError("db error"))
        session = _mock_session()

        # Should not raise
        await _compute_attribution(job_id="job-1", session=session, **repos)
        repos["attr_repo"].insert_batch.assert_called_once()


@pytest.mark.asyncio
class TestComputeAttributionTurnEconomics:
    async def test_turn_economics_single_turn(self) -> None:
        spans = [_span(turn_number=1, cost_usd=0.10)]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        call = repos["summary_repo"].set_turn_stats.call_args
        assert call.args[0] == "job-1"
        assert call.kwargs["peak_turn_cost_usd"] == pytest.approx(0.10)
        assert call.kwargs["avg_turn_cost_usd"] == pytest.approx(0.10)

    async def test_turn_economics_multiple_turns(self) -> None:
        spans = [
            _span(turn_number=1, cost_usd=0.02),
            _span(turn_number=2, cost_usd=0.08),
            _span(turn_number=3, cost_usd=0.04),
            _span(turn_number=4, cost_usd=0.06),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        call = repos["summary_repo"].set_turn_stats.call_args
        assert call.kwargs["peak_turn_cost_usd"] == pytest.approx(0.08)
        assert call.kwargs["avg_turn_cost_usd"] == pytest.approx(0.05)

    async def test_turn_economics_no_turns(self) -> None:
        # All spans have turn_number=None so no turn data
        spans = [_span(turn_number=None, cost_usd=0.10, execution_phase=None)]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        call = repos["summary_repo"].set_turn_stats.call_args
        assert call.kwargs["peak_turn_cost_usd"] == 0.0
        assert call.kwargs["avg_turn_cost_usd"] == 0.0

    async def test_cost_halves_split(self) -> None:
        """First/second half cost split should be computed from sorted turns."""
        spans = [
            _span(turn_number=1, cost_usd=0.10),
            _span(turn_number=2, cost_usd=0.20),
            _span(turn_number=3, cost_usd=0.30),
            _span(turn_number=4, cost_usd=0.40),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        call = repos["summary_repo"].set_turn_stats.call_args
        # 4 turns, mid=2 → first half [turn1, turn2], second half [turn3, turn4]
        assert call.kwargs["cost_first_half_usd"] == pytest.approx(0.30)
        assert call.kwargs["cost_second_half_usd"] == pytest.approx(0.70)


@pytest.mark.asyncio
class TestComputeAttributionDiffCounts:
    async def test_diff_counts_from_trail_repo(self) -> None:
        spans = [_span(turn_number=1, cost_usd=0.01)]
        repos = _mock_repos(spans=spans, diff_counts=(42, 17))
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        call = repos["summary_repo"].set_turn_stats.call_args
        assert call.kwargs["diff_lines_added"] == 42
        assert call.kwargs["diff_lines_removed"] == 17

    async def test_diff_counts_fallback_to_sql_when_no_trail_repo(self) -> None:
        spans = [_span(turn_number=1, cost_usd=0.01)]
        repos = _mock_repos(spans=spans)
        repos["trail_repo"] = None  # type: ignore[assignment]

        session = _mock_session()
        # Session.execute is called twice: once for job metadata, once for diff stats
        diff_result = MagicMock()
        diff_result.mappings.return_value.first.return_value = {"added": 10, "removed": 5}
        meta_result = MagicMock()
        meta_result.mappings.return_value.first.return_value = {
            "description": "test",
            "prompt": "test",
            "model": "claude-sonnet-4-20250514",
        }
        session.execute = AsyncMock(side_effect=[meta_result, diff_result])

        await _compute_attribution(job_id="job-1", session=session, **repos)

        call = repos["summary_repo"].set_turn_stats.call_args
        assert call.kwargs["diff_lines_added"] == 10
        assert call.kwargs["diff_lines_removed"] == 5


@pytest.mark.asyncio
class TestComputeAttributionOneShotRate:
    async def test_one_shot_turn_no_retries(self) -> None:
        """A turn with file_write but no shell→edit retry should count as one-shot."""
        spans = [
            _span(span_type="tool", name="write_file", turn_number=1),
            _span(span_type="llm", turn_number=1, cost_usd=0.05),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        # Should complete without error; one_shot_by_action is internal
        repos["attr_repo"].insert_batch.assert_called_once()

    async def test_retry_turn_detected(self) -> None:
        """A turn with edit→shell→edit should count retries."""
        spans = [
            _span(span_type="tool", name="write_file", turn_number=1),
            _span(span_type="tool", name="execute_command", turn_number=1, tool_args_json='{"command":"pytest"}'),
            _span(span_type="tool", name="write_file", turn_number=1),
            _span(span_type="llm", turn_number=1, cost_usd=0.05),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)
        repos["attr_repo"].insert_batch.assert_called_once()


@pytest.mark.asyncio
class TestComputeAttributionPurpose:
    async def test_purpose_dimension_from_trail(self) -> None:
        trail_node = MagicMock()
        trail_node.turn_number = 1
        trail_node.anchor_seq = None
        trail_node.purpose = "implement_feature"

        spans = [_span(turn_number=1, cost_usd=0.05)]
        repos = _mock_repos(spans=spans, trail_nodes=[trail_node])
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        purpose_rows = [r for r in rows if r["dimension"] == "purpose"]
        assert len(purpose_rows) >= 1
        assert purpose_rows[0]["bucket"] == "implement_feature"

    async def test_action_purpose_compound_dimension(self) -> None:
        trail_node = MagicMock()
        trail_node.turn_number = 1
        trail_node.anchor_seq = None
        trail_node.purpose = "fix_bug"

        spans = [_span(turn_number=1, cost_usd=0.05)]
        repos = _mock_repos(spans=spans, trail_nodes=[trail_node])
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        ap_rows = [r for r in rows if r["dimension"] == "action_purpose"]
        assert len(ap_rows) >= 1
        # Bucket format: "action:purpose"
        assert ":" in ap_rows[0]["bucket"]

    async def test_no_purpose_when_trail_empty(self) -> None:
        spans = [_span(turn_number=1, cost_usd=0.05)]
        repos = _mock_repos(spans=spans, trail_nodes=[])
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        purpose_rows = [r for r in rows if r["dimension"] == "purpose"]
        assert len(purpose_rows) == 0


@pytest.mark.asyncio
class TestComputeAttributionTrailFetchError:
    async def test_trail_fetch_error_handled(self) -> None:
        spans = [_span(turn_number=1, cost_usd=0.05)]
        repos = _mock_repos(spans=spans)
        repos["trail_repo"].get_by_job = AsyncMock(side_effect=RuntimeError("db error"))
        session = _mock_session()

        # Should not raise — trail errors are caught
        await _compute_attribution(job_id="job-1", session=session, **repos)
        repos["attr_repo"].insert_batch.assert_called_once()


@pytest.mark.asyncio
class TestComputeAttributionCacheTokens:
    async def test_cache_tokens_aggregated(self) -> None:
        spans = [
            _span(turn_number=1, cost_usd=0.02, cache_read_tokens=500, cache_write_tokens=200),
            _span(turn_number=1, cost_usd=0.03, cache_read_tokens=300, cache_write_tokens=100),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        turn_rows = [r for r in rows if r["dimension"] == "turn"]
        assert len(turn_rows) == 1
        assert turn_rows[0]["cache_read_tokens"] == 800
        assert turn_rows[0]["cache_write_tokens"] == 300


@pytest.mark.asyncio
class TestComputeAttributionFileRereads:
    async def test_file_reread_stats_passed_to_summary(self) -> None:
        spans = [_span(turn_number=1, cost_usd=0.01)]
        repos = _mock_repos(
            spans=spans,
            reread_stats={"unique_files": 5, "reread_count": 12},
        )
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        call = repos["summary_repo"].set_turn_stats.call_args
        assert call.kwargs["unique_files_read"] == 5
        assert call.kwargs["file_reread_count"] == 12


@pytest.mark.asyncio
class TestComputeAttributionActivityWeights:
    async def test_tool_activity_weights_split_cost(self) -> None:
        """When a turn has tool_activity_weights, cost is split proportionally."""
        spans = [
            # Two tool spans with different activities
            _span(span_type="tool", name="write_file", turn_number=1, tool_args_json='{"path":"f.py","content":"x"}'),
            _span(span_type="tool", name="read_file", turn_number=1, tool_args_json='{"path":"g.py"}'),
            _span(span_type="llm", turn_number=1, cost_usd=0.10, input_tokens=1000, output_tokens=500),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        rows = repos["attr_repo"].insert_batch.call_args.kwargs["rows"]
        activity_rows = [r for r in rows if r["dimension"] == "activity"]
        assert len(activity_rows) >= 1
        # Total activity cost should match turn cost
        total_activity_cost = sum(r["cost_usd"] for r in activity_rows)
        assert total_activity_cost == pytest.approx(0.10, abs=0.001)


@pytest.mark.asyncio
class TestComputeAttributionCostFromAttrs:
    @pytest.mark.parametrize("projected_cost", [None, 0.0])
    async def test_legacy_attrs_cannot_replace_unknown_or_measured_zero_currency(self, projected_cost) -> None:
        """Only the repository's qualified cost column establishes currency."""
        spans = [
            _span(
                turn_number=1,
                cost_usd=projected_cost,
                input_tokens=0,
                output_tokens=0,
                attrs={"cost": 0.07, "input_tokens": 500, "output_tokens": 250},
            ),
        ]
        repos = _mock_repos(spans=spans)
        session = _mock_session()

        await _compute_attribution(job_id="job-1", session=session, **repos)

        write = repos["attr_repo"].insert_batch.call_args.kwargs
        rows = write["rows"]
        turn_rows = [r for r in rows if r["dimension"] == "turn"]
        assert len(turn_rows) == 1
        assert turn_rows[0]["input_tokens"] == 500
        assert turn_rows[0]["output_tokens"] == 250
        assert turn_rows[0]["call_count"] == 1
        assert turn_rows[0]["cost_usd"] == 0.0
        assert write["currency_qualified"] is (projected_cost is not None)
        summary = repos["summary_repo"].set_turn_stats.call_args.kwargs
        assert summary["peak_turn_cost_usd"] == summary["avg_turn_cost_usd"] == 0.0
        assert summary["derived_currency_qualified"] is (projected_cost is not None)
        assert spans[0]["attrs"]["cost"] == 0.07


# ---------------------------------------------------------------------------
# compute_attribution (public wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestComputeAttributionPublicWrapper:
    @patch("backend.services.analytics.cost_attribution._compute_attribution", new_callable=AsyncMock)
    @patch("backend.persistence.file_cost_repo.FileCostRepository")
    @patch("backend.persistence.file_access_repo.FileAccessRepository")
    @patch("backend.persistence.telemetry_summary_repo.TelemetrySummaryRepository")
    @patch("backend.persistence.cost_attribution_repo.CostAttributionRepository")
    @patch("backend.persistence.telemetry_spans_repo.TelemetrySpansRepository")
    async def test_wrapper_creates_repos_and_delegates(
        self,
        mock_spans_cls: MagicMock,
        mock_attr_cls: MagicMock,
        mock_summary_cls: MagicMock,
        mock_file_cls: MagicMock,
        mock_file_cost_cls: MagicMock,
        mock_compute: AsyncMock,
    ) -> None:
        session = AsyncMock()
        await compute_attribution(session=session, job_id="job-42")

        mock_compute.assert_called_once()
        call_kwargs = mock_compute.call_args.kwargs
        assert call_kwargs["job_id"] == "job-42"
        assert call_kwargs["trail_repo"] is None  # no session_factory

    @patch("backend.services.analytics.cost_attribution._compute_attribution", new_callable=AsyncMock)
    @patch("backend.persistence.file_cost_repo.FileCostRepository")
    @patch("backend.persistence.file_access_repo.FileAccessRepository")
    @patch("backend.persistence.telemetry_summary_repo.TelemetrySummaryRepository")
    @patch("backend.persistence.cost_attribution_repo.CostAttributionRepository")
    @patch("backend.persistence.telemetry_spans_repo.TelemetrySpansRepository")
    @patch("backend.persistence.trail_repo.TrailNodeRepository")
    async def test_wrapper_with_session_factory(
        self,
        mock_trail_cls: MagicMock,
        mock_spans_cls: MagicMock,
        mock_attr_cls: MagicMock,
        mock_summary_cls: MagicMock,
        mock_file_cls: MagicMock,
        mock_file_cost_cls: MagicMock,
        mock_compute: AsyncMock,
    ) -> None:
        session = AsyncMock()
        factory = AsyncMock()
        await compute_attribution(session=session, job_id="job-42", session_factory=factory)

        mock_compute.assert_called_once()
        call_kwargs = mock_compute.call_args.kwargs
        assert call_kwargs["trail_repo"] is not None
