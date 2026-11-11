"""Scout session, utility-model wiring, and failure regressions."""

from __future__ import annotations

import asyncio
import builtins
import inspect
import sys
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import CPLConfig
from backend.models.events import EventKind, new_event
from backend.services.tools import preflight_curator

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
async def scout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """Use startup's constructor, without starting services or requiring CodeRecon.

    The legacy argument/tool builder is stubbed only to let the baseline reach
    the session assertions. Neither supplies context or tools to the Scout.
    """
    import backend.lifespan as lifespan

    config = CPLConfig(repos=[str(tmp_path / "checkout")])
    config.runtime.utility_model = "gpt-4o-mini"
    adapter = AsyncMock()
    adapter.create_session.return_value = "scout-sdk"

    async def events(session_id: str):
        yield new_event(
            session_id,
            EventKind.tool_call_completed,
            {"tool_name": "view", "arguments": {"path": "src.py"}, "result": "def target(): pass", "success": True},
        )
        yield new_event(session_id, EventKind.message_assistant, {"content": "Found the target implementation."})
        yield new_event(session_id, EventKind.session_ended, {"result": "src.py contains target(); tests cover it."})

    adapter.stream_events = MagicMock(side_effect=events)
    registry = MagicMock()
    registry.get_adapter.return_value = adapter
    monkeypatch.setattr(lifespan, "AdapterRegistry", MagicMock(return_value=registry))
    for name in (
        "ApprovalService",
        "GitService",
        "DiffService",
        "PlatformRegistry",
        "MergeService",
        "SidecarSessionManager",
        "SidecarDispatcher",
        "NarratorCompleter",
        "SummarizationService",
        "StepTracker",
        "RuntimeService",
    ):
        instance = MagicMock()
        instance.start = AsyncMock()
        monkeypatch.setattr(lifespan, name, MagicMock(return_value=instance))

    legacy_tools = ModuleType("backend.services.coderecon.coderecon_tools")
    legacy_tools.build_coderecon_tools = MagicMock(return_value=None)
    monkeypatch.setitem(sys.modules, legacy_tools.__name__, legacy_tools)
    kwargs: dict[str, Any] = {
        "session_factory": MagicMock(),
        "event_bus": MagicMock(),
        "config": config,
    }
    if "coderecon_service" in inspect.signature(lifespan._wire_core_services).parameters:
        kwargs["coderecon_service"] = SimpleNamespace(available=False)
    services = await lifespan._wire_core_services(**kwargs)
    curator = services.runtime_service.set_preflight_curator.call_args.args[0]
    return SimpleNamespace(
        curator=curator,
        adapter=adapter,
        config=config,
        repo=str(tmp_path / "checkout"),
        worktree=str(tmp_path / "job-worktree"),
    )


async def _curate(scout: SimpleNamespace, **callbacks: Any):
    return await scout.curator.curate(
        "Find the target implementation",
        repo=scout.repo,
        worktree=scout.worktree,
        job_id="job-scout",
        **callbacks,
    )


@pytest.mark.asyncio
async def test_scout_uses_utility_model_and_repository_tools(scout: SimpleNamespace) -> None:
    on_tool_call = AsyncMock()
    on_reasoning = AsyncMock()
    report = await _curate(scout, on_tool_call=on_tool_call, on_reasoning=on_reasoning)

    scout.adapter.create_session.assert_awaited_once()
    session = scout.adapter.create_session.call_args.args[0]
    assert session.model == scout.config.runtime.utility_model, "Scout must request the configured cheaper model"
    assert session.workspace_path == scout.worktree, "Scout must read the job worktree, not the main checkout"
    assert session.session_kind == "preflight"
    assert session.job_id == "job-scout"
    denied = set(session.disallowed_tools)
    assert not denied.intersection({"Read", "Glob", "Grep", "LS", "view", "read_file", "list_dir", "grep_search"})
    assert {"Bash", "Write", "Edit", "MultiEdit", "Task", "WebFetch", "WebSearch", "run_in_terminal"} <= denied
    assert not getattr(session, "coderecon_tools", None)
    scout.adapter.stream_events.assert_called_once_with("scout-sdk")
    scout.adapter.complete.assert_not_awaited()
    assert report.brief == "src.py contains target(); tests cover it."
    assert len(report.tool_calls) == 1
    assert report.tool_calls[0].tool_name == "view"
    assert report.tool_calls[0].result_text == "def target(): pass"
    on_tool_call.assert_awaited_once_with(report.tool_calls[0])
    on_reasoning.assert_awaited_once_with("Found the target implementation.")


@pytest.mark.asyncio
async def test_scout_report_accumulates_usage_and_observed_model(scout: SimpleNamespace) -> None:
    async def events(session_id: str):
        yield new_event(
            session_id,
            EventKind.telemetry_usage,
            {"model": "utility-observed", "input_tokens": 120, "output_tokens": 30, "cost_usd": 0.0015},
        )
        yield new_event(session_id, EventKind.message_assistant, {"content": "Inspecting one more caller"})
        yield new_event(
            session_id,
            EventKind.telemetry_usage,
            {"model": "utility-observed", "input_tokens": 80, "output_tokens": 20, "cost_usd": 0.0005},
        )
        yield new_event(session_id, EventKind.session_ended, {"result": "src.py contains the shared parser."})

    scout.adapter.stream_events.side_effect = events
    report = await _curate(scout)

    assert getattr(report, "input_tokens", None) == 200
    assert getattr(report, "output_tokens", None) == 50
    assert getattr(report, "cost_usd", None) == pytest.approx(0.002)
    assert getattr(report, "model", None) == "utility-observed"
    assert report.brief == "src.py contains the shared parser."


@pytest.mark.asyncio
async def test_scout_error_does_not_promote_partial_reasoning_to_brief(scout: SimpleNamespace) -> None:
    async def events(session_id: str):
        yield new_event(session_id, EventKind.message_assistant, {"content": "Still exploring; not a final brief"})
        yield new_event(session_id, EventKind.session_error, {"message": "Scout transport failed"})

    scout.adapter.stream_events.side_effect = events
    with pytest.raises(RuntimeError, match="Scout transport failed"):
        await _curate(scout)
    scout.adapter.abort_session.assert_awaited_once_with("scout-sdk")


@pytest.mark.asyncio
async def test_scout_does_not_import_structural_tools(scout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    attempted: list[str] = []
    original_import = builtins.__import__

    def observe_import(name: str, *args: Any, **kwargs: Any):
        if name.startswith(("coderecon", "backend.services.coderecon")):
            attempted.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", observe_import)
    await _curate(scout)
    assert attempted == [], "Independent Scout execution must not import a retired structural toolkit"


@pytest.mark.asyncio
async def test_scout_timeout_aborts_instead_of_returning_partial_brief(
    scout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preflight_curator, "_SESSION_TIMEOUT_S", 0.01)

    async def events(session_id: str):
        yield new_event(session_id, EventKind.message_assistant, {"content": "Unfinished exploration"})
        await asyncio.Event().wait()

    scout.adapter.stream_events.side_effect = events
    with pytest.raises(TimeoutError):
        await _curate(scout)
    scout.adapter.abort_session.assert_awaited_once_with("scout-sdk")


@pytest.mark.asyncio
async def test_scout_task_cancellation_aborts_its_sdk_session(scout: SimpleNamespace) -> None:
    started = asyncio.Event()

    async def events(session_id: str):
        started.set()
        await asyncio.Event().wait()
        yield new_event(session_id, EventKind.session_ended, {"result": "must not be returned"})

    scout.adapter.stream_events.side_effect = events
    task = asyncio.create_task(_curate(scout))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    scout.adapter.abort_session.assert_awaited_once_with("scout-sdk")


@pytest.mark.asyncio
async def test_scout_stream_failure_aborts_and_propagates(scout: SimpleNamespace) -> None:
    async def events(session_id: str):
        yield new_event(session_id, EventKind.message_assistant, {"content": "Unfinished exploration"})
        raise OSError("stream disconnected")

    scout.adapter.stream_events.side_effect = events
    with pytest.raises(OSError, match="stream disconnected"):
        await _curate(scout)
    scout.adapter.abort_session.assert_awaited_once_with("scout-sdk")


@pytest.mark.asyncio
async def test_scout_empty_success_has_no_invented_brief(scout: SimpleNamespace) -> None:
    async def events(session_id: str):
        yield new_event(session_id, EventKind.session_ended, {"result": ""})

    scout.adapter.stream_events.side_effect = events
    report = await _curate(scout)
    assert report.brief == ""
    assert report.tool_calls == []
