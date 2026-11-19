"""Comprehensive tests for backend.services.analytics.telemetry_query_service.

Covers the TelemetryQueryService class (get_telemetry, _enrich_turn_curve)
and the module-level helper functions (_shell_display_name, _refine_tool_category).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.models.api_schemas import TelemetryCostBucket
from backend.services.analytics.telemetry_query_service import (
    TelemetryQueryService,
    _refine_tool_category,
    _shell_display_name,
)

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_summary(**overrides: object) -> dict:
    """Return a minimal valid summary dict, merging *overrides*."""
    base = {
        "model": "claude-sonnet-4-20250514",
        "duration_ms": 60_000,
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_tokens": 200,
        "cache_write_tokens": 100,
        "context_window_size": 128_000,
        "current_context_tokens": 40_000,
        "total_cost_usd": 0.12,
        "compactions": 0,
        "tokens_compacted": 0,
        "tool_call_count": 5,
        "total_tool_duration_ms": 3000,
        "llm_call_count": 3,
        "total_llm_duration_ms": 8000,
        "approval_count": 0,
        "approval_wait_ms": 0,
        "agent_messages": 2,
        "operator_messages": 1,
        "premium_requests": 1.0,
        "total_turns": 4,
        "peak_turn_cost_usd": 0.05,
        "avg_turn_cost_usd": 0.03,
        "cost_first_half_usd": 0.07,
        "cost_second_half_usd": 0.05,
        "quota_json": None,
        "status": "completed",
        "created_at": "2026-01-01T00:00:00+00:00",
        "diff_lines_added": 10,
        "diff_lines_removed": 5,
        "parallelism_ratio": 1.2,
        "idle_ms": 500,
    }
    base.update(overrides)
    return base


def _make_tool_span(
    name: str = "read_file",
    turn_number: int = 1,
    duration_ms: float = 100,
    started_at: float = 0,
    tool_args_json: str | None = None,
    edit_motivations: str | None = None,
    tool_target: str | None = None,
    attrs: dict | None = None,
) -> dict:
    return {
        "span_type": "tool",
        "name": name,
        "turn_number": turn_number,
        "duration_ms": duration_ms,
        "started_at": started_at,
        "tool_args_json": tool_args_json,
        "edit_motivations": edit_motivations,
        "tool_target": tool_target,
        "attrs": attrs or {},
    }


def _make_llm_span(
    model: str = "claude-sonnet-4-20250514",
    turn_number: int = 1,
    duration_ms: float = 500,
    started_at: float = 0,
    input_tokens: int = 300,
    output_tokens: int = 100,
    cache_read_tokens: int = 50,
    cache_write_tokens: int = 20,
    cost: float = 0.01,
    is_subagent: bool = False,
    num_turns: int = 1,
) -> dict:
    return {
        "span_type": "llm",
        "cost_usd": cost,
        "name": model,
        "turn_number": turn_number,
        "duration_ms": duration_ms,
        "started_at": started_at,
        "attrs": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cost": cost,
            "is_subagent": is_subagent,
            "num_turns": num_turns,
        },
    }


_UNSET = object()


def _build_service(
    summary=None,
    spans=None,
    attribution_rows=None,
    latency_rows=None,
    file_stats=None,
    top_files=None,
    job_row=_UNSET,
    sidecar_rows=None,
    test_co_mods=None,
):
    """Build a TelemetryQueryService with fully mocked repos."""
    summary_repo = AsyncMock()
    summary_repo.get = AsyncMock(return_value=summary)
    summary_repo.get_currency = AsyncMock(return_value=summary)
    summary_repo.batch_cost_usage = AsyncMock(return_value={})
    summary_repo.list_sidecars = AsyncMock(return_value=sidecar_rows or [])

    spans_repo = AsyncMock()
    spans_repo.list_for_job = AsyncMock(return_value=spans or [])
    spans_repo.test_co_modifications = AsyncMock(return_value=test_co_mods or [])

    cost_repo = AsyncMock()
    cost_repo.for_job = AsyncMock(return_value=attribution_rows or [])

    latency_repo = AsyncMock()
    latency_repo.for_job = AsyncMock(return_value=latency_rows or [])

    file_repo = AsyncMock()
    file_repo.reread_stats = AsyncMock(
        return_value=file_stats
        or {"total_accesses": 0, "unique_files": 0, "total_reads": 0, "total_writes": 0, "reread_count": 0}
    )
    file_repo.most_accessed_files = AsyncMock(return_value=top_files or [])

    job_repo = AsyncMock()
    if job_row is _UNSET:
        job_row = SimpleNamespace(sdk="claude-code")
    job_repo.get = AsyncMock(return_value=job_row)

    return TelemetryQueryService(
        cost_repo=cost_repo,
        file_repo=file_repo,
        job_repo=job_repo,
        latency_repo=latency_repo,
        spans_repo=spans_repo,
        summary_repo=summary_repo,
    )


# ═══════════════════════════════════════════════════════════════════════════
# _shell_display_name
# ═══════════════════════════════════════════════════════════════════════════


class TestShellDisplayNameExtra:
    """Extra branch coverage beyond test_telemetry_query_helpers.py."""

    def test_returns_tool_name_for_none_args(self):
        assert _shell_display_name("bash", None) == "bash"

    def test_returns_tool_name_for_empty_string(self):
        assert _shell_display_name("run_in_terminal", "") == "run_in_terminal"

    def test_returns_tool_name_for_invalid_json(self):
        assert _shell_display_name("bash", "{broken") == "bash"

    def test_no_command_key(self):
        assert _shell_display_name("bash", json.dumps({"other": "val"})) == "bash"

    def test_cd_prefix_stripping(self):
        args = json.dumps({"command": "cd /workspace && make build"})
        assert _shell_display_name("bash", args) == "make"

    def test_env_var_skipping(self):
        args = json.dumps({"command": "CI=true VERBOSE=1 pytest tests/"})
        assert _shell_display_name("bash", args) == "pytest"

    def test_sudo_nohup_skipping(self):
        args = json.dumps({"command": "sudo nohup python app.py"})
        assert _shell_display_name("bash", args) == "python"

    def test_time_skipping(self):
        args = json.dumps({"command": "time python script.py"})
        assert _shell_display_name("bash", args) == "python"

    def test_env_skipping(self):
        args = json.dumps({"command": "env python script.py"})
        assert _shell_display_name("bash", args) == "python"

    def test_compound_git(self):
        args = json.dumps({"command": "git push origin main"})
        assert _shell_display_name("bash", args) == "git push"

    def test_compound_npm_install(self):
        args = json.dumps({"command": "npm install --save-dev eslint"})
        assert _shell_display_name("bash", args) == "npm install"

    def test_compound_docker_compose(self):
        args = json.dumps({"command": "docker compose up"})
        assert _shell_display_name("bash", args) == "docker compose"

    def test_path_stripping(self):
        args = json.dumps({"command": "/usr/local/bin/node index.js"})
        assert _shell_display_name("bash", args) == "node"

    def test_simple_command_no_subcommand(self):
        args = json.dumps({"command": "ls"})
        assert _shell_display_name("bash", args) == "ls"

    def test_all_env_vars_returns_tool_name(self):
        """When every part is an env var assignment, falls through to tool_name."""
        args = json.dumps({"command": "A=1 B=2"})
        assert _shell_display_name("bash", args) == "bash"

    def test_compound_subcommand_starts_with_dash(self):
        """git --help: subcommand starts with dash, so only 'git' returned."""
        args = json.dumps({"command": "git --help"})
        assert _shell_display_name("bash", args) == "git"


# ═══════════════════════════════════════════════════════════════════════════
# _refine_tool_category
# ═══════════════════════════════════════════════════════════════════════════


class TestRefineToolCategoryExtra:
    def test_non_shell_passthrough(self):
        assert _refine_tool_category("read_file", None) == "file_read"
        assert _refine_tool_category("edit_file", None) == "file_write"

    def test_shell_git_write_refined(self):
        args = json.dumps({"command": "git commit -m 'msg'"})
        assert _refine_tool_category("bash", args) == "git_write"

    def test_shell_git_read_refined(self):
        args = json.dumps({"command": "git diff HEAD"})
        assert _refine_tool_category("bash", args) == "git_read"

    def test_shell_non_git(self):
        args = json.dumps({"command": "ls -la"})
        assert _refine_tool_category("bash", args) == "shell"

    def test_shell_no_args(self):
        assert _refine_tool_category("bash", None) == "shell"


# ═══════════════════════════════════════════════════════════════════════════
# TelemetryQueryService.get_telemetry
# ═══════════════════════════════════════════════════════════════════════════


class TestGetTelemetry:
    @pytest.mark.asyncio
    async def test_unavailable_when_summary_is_none(self):
        svc = _build_service(summary=None)
        resp = await svc.get_telemetry("job-1")
        assert resp.available is False
        assert resp.job_id == "job-1"

    @pytest.mark.asyncio
    async def test_basic_response_assembly(self):
        svc = _build_service(summary=_make_summary())
        resp = await svc.get_telemetry("job-1")
        assert resp.available is True
        assert resp.job_id == "job-1"
        assert resp.model == "claude-sonnet-4-20250514"
        assert resp.sdk == "claude-code"
        assert resp.duration_ms == 60_000
        assert resp.input_tokens == 1000
        assert resp.output_tokens == 500
        assert resp.cache_read_tokens == 200
        assert resp.total_tokens == 1700  # 1000 + 500 + 200
        assert resp.total_cost == 0.12
        assert resp.context_utilization == pytest.approx(40_000 / 128_000)
        assert resp.parallelism_ratio == pytest.approx(1.2)
        assert resp.idle_ms == 500

    @pytest.mark.asyncio
    async def test_context_utilization_zero_window(self):
        svc = _build_service(summary=_make_summary(context_window_size=0))
        resp = await svc.get_telemetry("job-1")
        assert resp.context_utilization == 0

    @pytest.mark.asyncio
    async def test_tool_span_processing(self):
        spans = [
            _make_tool_span(name="bash", tool_args_json=json.dumps({"command": "pytest tests/"})),
            _make_tool_span(name="edit_file", tool_args_json=None),
        ]
        svc = _build_service(summary=_make_summary(), spans=spans)
        resp = await svc.get_telemetry("job-1")
        assert len(resp.tool_calls) == 2
        # bash span → display_label = "pytest"
        assert resp.tool_calls[0].display_label == "pytest"
        # edit_file → humanized to "edit file"
        assert resp.tool_calls[1].display_label == "edit file"

    @pytest.mark.asyncio
    async def test_shell_span_no_display_label_when_same_as_name(self):
        """When _shell_display_name returns the raw tool name, display_label is None."""
        spans = [_make_tool_span(name="bash", tool_args_json=json.dumps({"command": ""}))]
        svc = _build_service(summary=_make_summary(), spans=spans)
        resp = await svc.get_telemetry("job-1")
        assert resp.tool_calls[0].display_label is None

    @pytest.mark.asyncio
    async def test_tool_name_without_underscore_no_label(self):
        """Tool names without underscores (and not shell) get no display label."""
        spans = [_make_tool_span(name="Read")]
        svc = _build_service(summary=_make_summary(), spans=spans)
        resp = await svc.get_telemetry("job-1")
        assert resp.tool_calls[0].display_label is None

    @pytest.mark.asyncio
    async def test_edit_motivations_json_parsing(self):
        motivations = json.dumps(["fix typo", "add test"])
        spans = [_make_tool_span(name="edit_file", edit_motivations=motivations)]
        svc = _build_service(summary=_make_summary(), spans=spans)
        resp = await svc.get_telemetry("job-1")
        assert resp.tool_calls[0].edit_motivations == ["fix typo", "add test"]

    @pytest.mark.asyncio
    async def test_edit_motivations_invalid_json(self):
        spans = [_make_tool_span(name="edit_file", edit_motivations="not json")]
        svc = _build_service(summary=_make_summary(), spans=spans)
        resp = await svc.get_telemetry("job-1")
        assert resp.tool_calls[0].edit_motivations is None

    @pytest.mark.asyncio
    async def test_edit_motivations_none_string(self):
        """edit_motivations is the string None → suppress silently."""
        spans = [_make_tool_span(name="edit_file", edit_motivations=None)]
        svc = _build_service(summary=_make_summary(), spans=spans)
        resp = await svc.get_telemetry("job-1")
        assert resp.tool_calls[0].edit_motivations is None

    @pytest.mark.asyncio
    async def test_llm_span_processing(self):
        spans = [_make_llm_span(model="gpt-4o", cost=0.02, is_subagent=True, num_turns=3)]
        svc = _build_service(summary=_make_summary(), spans=spans)
        resp = await svc.get_telemetry("job-1")
        assert len(resp.llm_calls) == 1
        llm = resp.llm_calls[0]
        assert llm.model == "gpt-4o"
        assert llm.cost == 0.02
        assert llm.is_subagent is True
        assert llm.call_count == 3

    @pytest.mark.asyncio
    async def test_cost_bucket_grouping(self):
        rows = [
            {
                "dimension": "activity",
                "bucket": "investigation",
                "cost_usd": 0.05,
                "input_tokens": 100,
                "output_tokens": 50,
                "call_count": 2,
            },
            {
                "dimension": "activity",
                "bucket": "implementation",
                "cost_usd": 0.03,
                "input_tokens": 80,
                "output_tokens": 40,
                "call_count": 1,
            },
            {
                "dimension": "phase",
                "bucket": "planning",
                "cost_usd": 0.04,
                "input_tokens": 90,
                "output_tokens": 45,
                "call_count": 1,
            },
        ]
        svc = _build_service(summary=_make_summary(), attribution_rows=rows)
        resp = await svc.get_telemetry("job-1")
        assert len(resp.cost_drivers.activity) == 2
        assert len(resp.cost_drivers.phase) == 1

    @pytest.mark.asyncio
    async def test_turn_curve_sorting(self):
        rows = [
            {
                "dimension": "turn",
                "bucket": "3",
                "cost_usd": 0.01,
                "input_tokens": 10,
                "output_tokens": 5,
                "call_count": 1,
            },
            {
                "dimension": "turn",
                "bucket": "1",
                "cost_usd": 0.02,
                "input_tokens": 20,
                "output_tokens": 10,
                "call_count": 1,
            },
            {
                "dimension": "turn",
                "bucket": "2",
                "cost_usd": 0.015,
                "input_tokens": 15,
                "output_tokens": 8,
                "call_count": 1,
            },
        ]
        svc = _build_service(summary=_make_summary(), attribution_rows=rows)
        resp = await svc.get_telemetry("job-1")
        buckets = resp.turn_economics.turn_curve
        assert [b.bucket for b in buckets] == ["1", "2", "3"]

    @pytest.mark.asyncio
    async def test_turn_curve_non_digit_bucket(self):
        """Non-digit bucket names sort as 0."""
        rows = [
            {
                "dimension": "turn",
                "bucket": "abc",
                "cost_usd": 0.01,
                "input_tokens": 0,
                "output_tokens": 0,
                "call_count": 0,
            },
            {
                "dimension": "turn",
                "bucket": "2",
                "cost_usd": 0.02,
                "input_tokens": 0,
                "output_tokens": 0,
                "call_count": 0,
            },
        ]
        svc = _build_service(summary=_make_summary(), attribution_rows=rows)
        resp = await svc.get_telemetry("job-1")
        assert resp.turn_economics.turn_curve[0].bucket == "abc"
        assert resp.turn_economics.turn_curve[1].bucket == "2"

    @pytest.mark.asyncio
    async def test_latency_bucketing(self):
        rows = [
            {
                "dimension": "category",
                "bucket": "tool",
                "wall_clock_ms": 500,
                "sum_duration_ms": 600,
                "span_count": 3,
                "p50_ms": 100,
                "p95_ms": 200,
                "max_ms": 250,
                "pct_of_total": 50.0,
            },
            {
                "dimension": "turn",
                "bucket": "2",
                "wall_clock_ms": 400,
                "sum_duration_ms": 450,
                "span_count": 2,
                "p50_ms": 150,
                "p95_ms": 300,
                "max_ms": 350,
                "pct_of_total": 40.0,
            },
            {
                "dimension": "turn",
                "bucket": "1",
                "wall_clock_ms": 600,
                "sum_duration_ms": 700,
                "span_count": 4,
                "p50_ms": 120,
                "p95_ms": 250,
                "max_ms": 300,
                "pct_of_total": 60.0,
            },
        ]
        svc = _build_service(summary=_make_summary(), latency_rows=rows)
        resp = await svc.get_telemetry("job-1")
        assert len(resp.latency_drivers.category) == 1
        # Turn latency curve sorted: turn 1 then turn 2
        assert resp.turn_latency.turn_curve[0].bucket == "1"
        assert resp.turn_latency.turn_curve[1].bucket == "2"
        assert resp.turn_latency.peak_turn_ms == 600
        assert resp.turn_latency.avg_turn_ms == 500  # (600+400)//2
        assert resp.turn_latency.first_half_ms == 600  # first half = [turn 1]
        assert resp.turn_latency.second_half_ms == 400  # second half = [turn 2]

    @pytest.mark.asyncio
    async def test_latency_empty_turns(self):
        svc = _build_service(summary=_make_summary(), latency_rows=[])
        resp = await svc.get_telemetry("job-1")
        assert resp.turn_latency.peak_turn_ms == 0
        assert resp.turn_latency.avg_turn_ms == 0

    @pytest.mark.asyncio
    async def test_review_complexity_quick(self):
        svc = _build_service(
            summary=_make_summary(diff_lines_added=10, diff_lines_removed=5, total_turns=3),
            file_stats={"unique_files": 2, "total_accesses": 5, "total_reads": 3, "total_writes": 2, "reread_count": 0},
        )
        resp = await svc.get_telemetry("job-1")
        assert resp.review_complexity.tier == "quick"
        assert resp.review_complexity.signals == []

    @pytest.mark.asyncio
    async def test_review_complexity_standard_large_diff(self):
        svc = _build_service(
            summary=_make_summary(diff_lines_added=400, diff_lines_removed=200, total_turns=5),
            file_stats={
                "unique_files": 3,
                "total_accesses": 0,
                "total_reads": 0,
                "total_writes": 0,
                "reread_count": 0,
            },
        )
        resp = await svc.get_telemetry("job-1")
        assert resp.review_complexity.tier == "standard"
        assert "large_diff" in resp.review_complexity.signals

    @pytest.mark.asyncio
    async def test_review_complexity_standard_many_turns(self):
        svc = _build_service(
            summary=_make_summary(diff_lines_added=10, diff_lines_removed=5, total_turns=25),
            file_stats={
                "unique_files": 3,
                "total_accesses": 0,
                "total_reads": 0,
                "total_writes": 0,
                "reread_count": 0,
            },
        )
        resp = await svc.get_telemetry("job-1")
        assert resp.review_complexity.tier == "standard"
        assert "many_turns" in resp.review_complexity.signals

    @pytest.mark.asyncio
    async def test_review_complexity_deep(self):
        svc = _build_service(
            summary=_make_summary(diff_lines_added=400, diff_lines_removed=200, total_turns=25),
            file_stats={
                "unique_files": 20,
                "total_accesses": 0,
                "total_reads": 0,
                "total_writes": 0,
                "reread_count": 0,
            },
        )
        resp = await svc.get_telemetry("job-1")
        assert resp.review_complexity.tier == "deep"
        assert len(resp.review_complexity.signals) >= 3

    @pytest.mark.asyncio
    async def test_quota_snapshot_parsing(self):
        quota = json.dumps(
            {
                "premium": {
                    "used_requests": 10,
                    "entitlement_requests": 100,
                    "remaining_percentage": 90,
                    "overage": 0,
                    "overage_allowed": False,
                    "is_unlimited": False,
                    "reset_date": "2026-02-01",
                }
            }
        )
        svc = _build_service(summary=_make_summary(quota_json=quota))
        resp = await svc.get_telemetry("job-1")
        assert resp.quota_snapshots is not None
        assert "premium" in resp.quota_snapshots
        assert resp.quota_snapshots["premium"].used_requests == 10
        assert resp.quota_snapshots["premium"].remaining_percentage == 90

    @pytest.mark.asyncio
    async def test_quota_snapshot_invalid_json(self):
        svc = _build_service(summary=_make_summary(quota_json="not json"))
        resp = await svc.get_telemetry("job-1")
        assert resp.quota_snapshots is None

    @pytest.mark.asyncio
    async def test_quota_snapshot_none(self):
        svc = _build_service(summary=_make_summary(quota_json=None))
        resp = await svc.get_telemetry("job-1")
        assert resp.quota_snapshots is None

    @pytest.mark.asyncio
    async def test_quota_snapshot_non_dict_value_skipped(self):
        quota = json.dumps({"premium": "not_a_dict", "standard": {"used_requests": 5}})
        svc = _build_service(summary=_make_summary(quota_json=quota))
        resp = await svc.get_telemetry("job-1")
        assert "premium" not in resp.quota_snapshots
        assert "standard" in resp.quota_snapshots

    @pytest.mark.asyncio
    async def test_sidecar_session_summaries(self):
        sidecars = [
            {
                "session_kind": "preflight",
                "input_tokens": 500,
                "output_tokens": 200,
                "total_cost_usd": 0.01,
                "llm_call_count": 1,
                "tool_call_count": 3,
            },
            {
                "session_kind": "memory",
                "input_tokens": 100,
                "output_tokens": 50,
                "total_cost_usd": 0.002,
                "llm_call_count": 1,
                "tool_call_count": 0,
            },
        ]
        svc = _build_service(summary=_make_summary(), sidecar_rows=sidecars)
        resp = await svc.get_telemetry("job-1")
        assert len(resp.sidecar_sessions) == 2
        assert resp.sidecar_sessions[0].session_kind == "preflight"
        assert resp.sidecar_sessions[1].total_cost_usd == pytest.approx(0.002)

    @pytest.mark.asyncio
    async def test_live_duration_for_running_job(self):
        now = datetime.now(UTC)
        created = (now - timedelta(minutes=5)).isoformat()
        svc = _build_service(summary=_make_summary(duration_ms=0, status="running", created_at=created))
        resp = await svc.get_telemetry("job-1")
        # Should be approximately 5 minutes in ms
        assert resp.duration_ms > 290_000  # allow some slack
        assert resp.duration_ms < 310_000

    @pytest.mark.asyncio
    async def test_live_duration_not_applied_when_status_not_running(self):
        svc = _build_service(summary=_make_summary(duration_ms=0, status="completed"))
        resp = await svc.get_telemetry("job-1")
        assert resp.duration_ms == 0

    @pytest.mark.asyncio
    async def test_live_duration_invalid_created_at(self):
        svc = _build_service(summary=_make_summary(duration_ms=0, status="running", created_at="invalid"))
        resp = await svc.get_telemetry("job-1")
        assert resp.duration_ms == 0

    @pytest.mark.asyncio
    async def test_live_duration_naive_datetime(self):
        """created_at without timezone info should be treated as UTC."""
        now = datetime.now(UTC)
        created = (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S")
        svc = _build_service(summary=_make_summary(duration_ms=0, status="running", created_at=created))
        resp = await svc.get_telemetry("job-1")
        assert resp.duration_ms > 25_000
        assert resp.duration_ms < 40_000

    @pytest.mark.asyncio
    async def test_job_row_none_returns_empty_sdk(self):
        svc = _build_service(summary=_make_summary(), job_row=None)
        resp = await svc.get_telemetry("job-1")
        assert resp.sdk == ""

    @pytest.mark.asyncio
    async def test_file_access_stats(self):
        stats = {"total_accesses": 20, "unique_files": 5, "total_reads": 12, "total_writes": 8, "reread_count": 3}
        files = [
            {"file_path": "src/main.py", "access_count": 6, "read_count": 4, "write_count": 2},
            {"file_path": "src/util.py", "access_count": 3, "read_count": 2, "write_count": 1},
        ]
        svc = _build_service(summary=_make_summary(), file_stats=stats, top_files=files)
        resp = await svc.get_telemetry("job-1")
        assert resp.file_access.stats.total_accesses == 20
        assert resp.file_access.stats.reread_count == 3
        assert len(resp.file_access.top_files) == 2
        assert resp.file_access.top_files[0].file_path == "src/main.py"

    @pytest.mark.asyncio
    async def test_tool_span_with_success_false(self):
        spans = [_make_tool_span(name="read_file", attrs={"success": False})]
        svc = _build_service(summary=_make_summary(), spans=spans)
        resp = await svc.get_telemetry("job-1")
        assert resp.tool_calls[0].success is False

    @pytest.mark.asyncio
    async def test_mixed_spans(self):
        spans = [
            _make_tool_span(name="read_file"),
            _make_llm_span(),
            _make_tool_span(name="bash", tool_args_json=json.dumps({"command": "ls"})),
            _make_llm_span(model="gpt-4o"),
        ]
        svc = _build_service(summary=_make_summary(), spans=spans)
        resp = await svc.get_telemetry("job-1")
        assert len(resp.tool_calls) == 2
        assert len(resp.llm_calls) == 2


# ═══════════════════════════════════════════════════════════════════════════
# TelemetryQueryService._enrich_turn_curve
# ═══════════════════════════════════════════════════════════════════════════


class TestEnrichTurnCurve:
    def test_empty_spans_sets_communication(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        TelemetryQueryService._enrich_turn_curve([bucket], [])
        assert bucket.activity == "communication"
        assert bucket.intent is None
        assert bucket.actions == []

    def test_no_matching_turn_sets_communication(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="5", cost_usd=0.01)
        spans = [_make_tool_span(name="read_file", turn_number=1)]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert bucket.activity == "communication"

    def test_report_intent_sets_intent(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(name="report_intent", turn_number=1, tool_args_json=json.dumps({"intent": "fix the bug"})),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert bucket.intent == "fix the bug"

    def test_report_intent_last_wins(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(name="report_intent", turn_number=1, tool_args_json=json.dumps({"intent": "first"})),
            _make_tool_span(name="report_intent", turn_number=1, tool_args_json=json.dumps({"intent": "second"})),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert bucket.intent == "second"

    def test_file_write_action(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(
                name="edit_file",
                turn_number=1,
                tool_args_json=json.dumps({"file_path": "/repo/.codeplane-worktrees/job-1/src/main.py"}),
            ),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert len(bucket.actions) == 1
        assert "edited" in bucket.actions[0].text
        assert "src/main.py" in bucket.actions[0].text

    def test_file_read_action(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(
                name="read_file",
                turn_number=1,
                tool_args_json=json.dumps({"path": "src/utils.py"}),
            ),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert any("read" in a.text for a in bucket.actions)

    def test_file_search_action_with_path(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(
                name="grep_search",
                turn_number=1,
                tool_args_json=json.dumps({"query": "def main"}),
            ),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert any("searched" in a.text for a in bucket.actions)

    def test_shell_command_action(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(
                name="bash",
                turn_number=1,
                tool_args_json=json.dumps({"command": "cd /tmp && pytest tests/unit/"}),
            ),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert any("ran pytest" in a.text for a in bucket.actions)

    def test_shell_compound_command_shortening(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(
                name="bash",
                turn_number=1,
                tool_args_json=json.dumps({"command": "npm install lodash"}),
            ),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert any("ran npm install" in a.text for a in bucket.actions)

    def test_git_action(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [_make_tool_span(name="git_diff", turn_number=1)]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert any("git diff" in a.text for a in bucket.actions)

    def test_agent_delegation_action(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [_make_tool_span(name="subagent", turn_number=1)]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert any("delegated to subagent" in a.text for a in bucket.actions)

    def test_browser_action(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [_make_tool_span(name="fetch_url", turn_number=1)]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert any("fetched fetch_url" in a.text for a in bucket.actions)

    def test_deduplication_with_counts(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(name="read_file", turn_number=1, tool_args_json=json.dumps({"path": "a/b.py"})),
            _make_tool_span(name="read_file", turn_number=1, tool_args_json=json.dumps({"path": "a/b.py"})),
            _make_tool_span(name="read_file", turn_number=1, tool_args_json=json.dumps({"path": "a/b.py"})),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        # Should be deduplicated with ×3
        assert len(bucket.actions) == 1
        assert "×3" in bucket.actions[0].text

    def test_no_dedup_for_single_action(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(name="read_file", turn_number=1, tool_args_json=json.dumps({"path": "a/b.py"})),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert len(bucket.actions) == 1
        assert "×" not in bucket.actions[0].text

    def test_activity_classification_from_tool_activities(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(name="edit_file", turn_number=1, tool_args_json=json.dumps({"file_path": "a.py"})),
            _make_tool_span(name="edit_file", turn_number=1, tool_args_json=json.dumps({"file_path": "b.py"})),
            _make_tool_span(name="read_file", turn_number=1, tool_args_json=json.dumps({"path": "c.py"})),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        # Two edits vs one read → majority is "implementation"
        assert bucket.activity == "implementation"

    def test_worktree_path_stripping(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(
                name="edit_file",
                turn_number=1,
                tool_args_json=json.dumps(
                    {"file_path": "/home/user/repo/.codeplane-worktrees/my-job/backend/services/svc.py"}
                ),
            ),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert "services/svc.py" in bucket.actions[0].text
        assert ".codeplane-worktrees" not in bucket.actions[0].text

    def test_long_path_truncated_to_last_two_segments(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(
                name="read_file",
                turn_number=1,
                tool_args_json=json.dumps({"path": "a/b/c/d/e.py"}),
            ),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert "d/e.py" in bucket.actions[0].text

    def test_file_write_falls_back_to_tool_target(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(name="edit_file", turn_number=1, tool_args_json=json.dumps({}), tool_target="target.py"),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert any("target.py" in a.text for a in bucket.actions)

    def test_file_search_short_query_not_truncated(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(
                name="grep_search",
                turn_number=1,
                tool_args_json=json.dumps({"query": "hello world"}),
            ),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert any("hello world" in a.text for a in bucket.actions)

    def test_llm_spans_ignored(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [_make_llm_span(model="gpt-4o", turn_number=1)]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert bucket.activity == "communication"
        assert bucket.actions == []

    def test_multiple_turns_enriched_independently(self):
        b1 = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        b2 = TelemetryCostBucket(dimension="turn", bucket="2", cost_usd=0.02)
        spans = [
            _make_tool_span(name="read_file", turn_number=1, tool_args_json=json.dumps({"path": "a.py"})),
            _make_tool_span(name="edit_file", turn_number=2, tool_args_json=json.dumps({"file_path": "b.py"})),
        ]
        TelemetryQueryService._enrich_turn_curve([b1, b2], spans)
        assert any("read" in a.text for a in b1.actions)
        assert any("edited" in a.text for a in b2.actions)

    def test_shell_empty_command_no_action(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(name="bash", turn_number=1, tool_args_json=json.dumps({"command": ""})),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        # Empty command → no "ran" action added
        assert not any("ran" in a.text for a in (bucket.actions or []))

    def test_invalid_tool_args_json_handled(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [_make_tool_span(name="edit_file", turn_number=1, tool_args_json="not json")]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        # Should not crash; action may be empty due to missing path
        assert bucket.activity is not None

    def test_tool_args_json_non_dict_parsed(self):
        """tool_args_json that parses to a non-dict (e.g. a list) is treated as empty args."""
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [_make_tool_span(name="edit_file", turn_number=1, tool_args_json=json.dumps([1, 2, 3]))]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert bucket.activity is not None

    def test_empty_path_no_action_added(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [_make_tool_span(name="edit_file", turn_number=1, tool_args_json=json.dumps({"file_path": ""}))]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        # Empty path → no "edited" action
        assert not any("edited" in a.text for a in (bucket.actions or []))

    def test_report_intent_empty_string_not_set(self):
        bucket = TelemetryCostBucket(dimension="turn", bucket="1", cost_usd=0.01)
        spans = [
            _make_tool_span(name="report_intent", turn_number=1, tool_args_json=json.dumps({"intent": ""})),
        ]
        TelemetryQueryService._enrich_turn_curve([bucket], spans)
        assert bucket.intent is None
