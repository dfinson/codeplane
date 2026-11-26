"""Scout session, utility-model wiring, and failure regressions."""

from __future__ import annotations

import asyncio
import builtins
import os
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import CPLConfig
from backend.models.db import Base
from backend.models.events import EventKind, new_event
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.tools import preflight_curator
from backend.tests.unit.conftest import make_job

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def scout_session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo = str(tmp_path / "checkout")
        async with factory() as session:
            await ProjectRepository(session).create("proj-1", "Scout", [repo])
            await JobRepository(session).create(make_job(id="job-scout", repo=repo))
            await session.commit()
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def scout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scout_session_factory: async_sessionmaker[AsyncSession],
) -> SimpleNamespace:
    """Use startup's constructor without starting external services."""
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

    kwargs: dict[str, Any] = {
        "session_factory": scout_session_factory,
        "event_bus": MagicMock(),
        "config": config,
    }
    services = await lifespan._wire_core_services(**kwargs)
    curator = services.runtime_service.set_preflight_curator.call_args.args[0]
    (tmp_path / "job-worktree").mkdir()
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
async def test_managed_sdk_error_does_not_become_a_successful_brief(scout: SimpleNamespace) -> None:
    from backend.models.domain import SessionConfig
    from backend.services.copilot_adapter._adapter import CopilotAdapter

    translator = CopilotAdapter()
    sdk_session = SimpleNamespace(session_id="scout-sdk", on=MagicMock(), send=AsyncMock(), abort=AsyncMock())
    client = SimpleNamespace(
        create_session=AsyncMock(return_value=sdk_session), stop=AsyncMock(), force_stop=AsyncMock()
    )

    async def events(session_id: str):
        with patch("backend.services.copilot_adapter._adapter.create_copilot_client", return_value=client):
            admitted = await translator.create_session(
                SessionConfig(
                    workspace_path=scout.worktree,
                    prompt="Prepare a context brief",
                    job_id="job-scout",
                    model=scout.config.runtime.utility_model,
                    session_kind="preflight",
                )
            )
        assert admitted == session_id
        yield new_event(session_id, EventKind.message_assistant, {"content": "Still exploring; not a finished brief"})
        queue = translator._queues[session_id]
        sdk_error = SimpleNamespace(
            type=SimpleNamespace(value="session.error"),
            data=SimpleNamespace(to_dict=lambda: {"message": "SDK rejected Scout session"}),
        )
        await translator._process_sdk_event_inner(sdk_error, session_id, "job-scout", "", [False], queue)
        while not queue.empty():
            event = queue.get_nowait()
            if event is not None:
                yield event

    scout.adapter.stream_events.side_effect = events
    try:
        with pytest.raises(RuntimeError, match="SDK rejected Scout session"):
            await _curate(scout)
    finally:
        await translator.abort_session(sdk_session.session_id)
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
    timeout = asyncio.timeout(None)
    monkeypatch.setattr(preflight_curator.asyncio, "timeout", lambda _: timeout)

    async def events(session_id: str):
        yield new_event(session_id, EventKind.message_assistant, {"content": "Unfinished exploration"})
        # Expire after SDK creation, rather than racing the account lookup.
        timeout.reschedule(asyncio.get_running_loop().time())
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


@pytest.mark.asyncio
@pytest.mark.parametrize("ended", [False, True])
async def test_scout_missing_final_brief_is_not_an_empty_success(scout: SimpleNamespace, ended: bool) -> None:
    async def events(session_id: str):
        if ended:
            yield new_event(session_id, EventKind.session_ended, {})

    scout.adapter.stream_events.side_effect = events
    with pytest.raises(RuntimeError, match="final context brief"):
        await _curate(scout)
    scout.adapter.abort_session.assert_awaited_once_with("scout-sdk")


@pytest.mark.asyncio
@pytest.mark.parametrize("worktree_kind", ["missing", "file", "unreadable"])
async def test_scout_requires_a_readable_worktree(
    scout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, worktree_kind: str
) -> None:
    if worktree_kind == "missing":
        scout.worktree = str(tmp_path / "missing")
    elif worktree_kind == "file":
        path = tmp_path / "not-a-directory"
        path.write_text("not a worktree")
        scout.worktree = str(path)
    else:
        monkeypatch.setattr(preflight_curator.os, "scandir", MagicMock(side_effect=PermissionError("unreadable")))
    with pytest.raises(OSError):
        await _curate(scout)
    scout.adapter.create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_scout_stops_at_the_turn_bound(scout: SimpleNamespace) -> None:
    async def events(session_id: str):
        for _ in range(16):
            yield new_event(session_id, EventKind.turn_started, {})
        yield new_event(session_id, EventKind.message_assistant, {"content": "too late"})

    scout.adapter.stream_events.side_effect = events
    with pytest.raises(RuntimeError, match="15-turn limit"):
        await _curate(scout)
    scout.adapter.abort_session.assert_awaited_once_with("scout-sdk")


def test_scout_denies_symlink_escape(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = worktree / "linked"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        assert not preflight_curator.scout_read_allowed(str(worktree), "read", "view", ["linked/private.py"])
        assert not preflight_curator.scout_read_allowed(str(worktree), "read", "Glob", ["linked/**/*.py"])
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


@pytest.mark.parametrize("pattern", ["**/*.py", "src/[ab]?.py"])
def test_scout_allows_workspace_glob_patterns(tmp_path: Path, pattern: str) -> None:
    assert preflight_curator.scout_read_allowed(str(tmp_path), "read", "Glob", [pattern])


@pytest.mark.parametrize("pattern", ["../outside/**/*.py", "**/../../outside.py", "**/{../../outside,src}/*.py"])
def test_scout_rejects_glob_traversal(tmp_path: Path, pattern: str) -> None:
    assert not preflight_curator.scout_read_allowed(str(tmp_path), "read", "Glob", [pattern])


@pytest.mark.skipif(os.name != "nt", reason="Windows alias semantics")
@pytest.mark.parametrize("pattern", ["**/.. /outside", "**/NUL", "**/file:stream", "**/dir./file"])
def test_scout_rejects_aliases_after_glob_wildcards(tmp_path: Path, pattern: str) -> None:
    assert not preflight_curator.scout_read_allowed(str(tmp_path), "read", "Glob", [pattern])


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_scout_does_not_promote_pre_tool_narration_at_stream_end(scout: SimpleNamespace, success: bool) -> None:
    async def events(session_id: str):
        yield new_event(
            session_id,
            EventKind.message_assistant,
            {"content": "Let me create a temporary script that modifies sum.py and execute it:"},
        )
        yield new_event(
            session_id,
            EventKind.tool_call_completed,
            {"tool_name": "create", "arguments": {"path": "fix.py"}, "success": success},
        )

    scout.adapter.stream_events.side_effect = events
    with pytest.raises(RuntimeError, match="final context brief"):
        await _curate(scout)
    scout.adapter.abort_session.assert_awaited_once_with("scout-sdk")


@pytest.mark.asyncio
async def test_scout_accepts_final_brief_after_tools_without_explicit_result(scout: SimpleNamespace) -> None:
    brief = "sum.py:add subtracts its arguments. check.py expects add(2, 3) == 5. Preserve the sentinel file."

    async def events(session_id: str):
        yield new_event(session_id, EventKind.message_assistant, {"content": "Reading the implementation."})
        yield new_event(session_id, EventKind.tool_call_completed, {"tool_name": "view", "success": True})
        yield new_event(session_id, EventKind.message_assistant, {"content": brief})
        yield new_event(session_id, EventKind.telemetry_usage, {"input_tokens": 10, "output_tokens": 5})

    scout.adapter.stream_events.side_effect = events
    report = await _curate(scout)
    assert report.brief == brief
    assert report.input_tokens == 10
    assert report.output_tokens == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["Let me create a temporary script before I finish.", ""])
async def test_scout_rejects_pending_tool_request_without_execution_events(
    scout: SimpleNamespace, content: str
) -> None:
    async def events(session_id: str):
        yield new_event(session_id, EventKind.message_assistant, {"content": "Earlier partial context."})
        yield new_event(session_id, EventKind.message_assistant, {"content": content, "has_tool_requests": True})

    scout.adapter.stream_events.side_effect = events
    with pytest.raises(RuntimeError, match="final context brief"):
        await _curate(scout)
