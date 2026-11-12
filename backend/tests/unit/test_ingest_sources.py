"""Control-plane tests for TraceForge-backed imported ingestion sources."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import CPLConfig
from backend.models.db import Base
from backend.models.domain import Job, JobSource, JobState
from backend.models.events import EventKind
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.completers import copilot_steer
from backend.services.completers.copilot_steer import CopilotSteerClient
from backend.services.events.event_bus import EventBus
from backend.services.events.ingest_service import IngestService
from backend.services.ingest.claude_source import (
    _SESSION_FILE_RE,
    ClaudeSessionStateWatcher,
    _encode_cwd,
    _find_claude_pids_at_cwd,
    _is_claude_process_alive,
    _is_pid_alive,
)
from backend.services.ingest.copilot_source import SessionStateWatcher
from backend.services.runtime import RuntimeService
from backend.tests.unit.conftest import make_job

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


@pytest.fixture
async def db_session() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def event_processor() -> AsyncMock:
    processor = AsyncMock()
    processor.process_event = AsyncMock()
    processor.cleanup = MagicMock()
    return processor


@pytest.fixture
def runtime_service() -> MagicMock:
    service = MagicMock()
    service.register_external_session = AsyncMock()
    service.finalize_external_session = AsyncMock()
    return service


@pytest.fixture
def git_service() -> AsyncMock:
    git = AsyncMock()
    git.get_current_branch = AsyncMock(return_value="main")
    git.rev_parse = AsyncMock(return_value="abc123")
    git._run_git = AsyncMock(return_value="C:\\repo\\myproject")
    return git


@pytest.fixture
def claude_watcher(
    event_processor: AsyncMock,
    runtime_service: MagicMock,
    db_session: async_sessionmaker[AsyncSession],
    git_service: AsyncMock,
) -> ClaudeSessionStateWatcher:
    return ClaudeSessionStateWatcher(
        event_processor=event_processor,
        runtime_service=runtime_service,
        session_factory=db_session,
        config=CPLConfig(repos=["C:\\repo\\myproject"]),
        git_service=git_service,
    )


@pytest.fixture
def copilot_watcher(
    event_processor: AsyncMock,
    runtime_service: MagicMock,
    db_session: async_sessionmaker[AsyncSession],
    git_service: AsyncMock,
) -> SessionStateWatcher:
    return SessionStateWatcher(
        event_processor=event_processor,
        runtime_service=runtime_service,
        session_factory=db_session,
        config=CPLConfig(repos=["C:\\repo\\myproject"]),
        git_service=git_service,
        steer_client=AsyncMock(),
    )


_LOCAL_ID = "11111111-1111-4111-8111-111111111111"
_TASK_ID = "22222222-2222-4222-8222-222222222222"
_OTHER_TASK_ID = "33333333-3333-4333-8333-333333333333"


def _write_native_metadata(root: Path, **overrides: object) -> Path:
    directory = root / _LOCAL_ID
    directory.mkdir(parents=True, exist_ok=True)
    workspace = directory / "workspace.yaml"
    workspace.write_text(
        json.dumps(
            {"id": _LOCAL_ID, "mc_session_id": _LOCAL_ID, "mc_task_id": _TASK_ID, "remote_steerable": True} | overrides
        ),
        encoding="utf-8",
    )
    (directory / "events.jsonl").write_text(
        json.dumps({"type": "session.remote_steerable_changed", "data": {"remoteSteerable": True}}) + "\n",
        encoding="utf-8",
    )
    return workspace


@pytest.fixture
async def native_control_stack(tmp_path, monkeypatch):
    """Actual command stack, with persisted-job reads and the HTTP transport stubbed."""
    root = tmp_path / "session-state"
    workspace = _write_native_metadata(root)
    monkeypatch.setattr("backend.services.ingest.copilot_source._SESSION_STATE_DIR", root)
    monkeypatch.setattr("backend.services.events.ingest_service.load_config", CPLConfig)
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = AsyncMock()
    runtime = RuntimeService(
        session_factory=factory, event_bus=EventBus(), adapter_registry=MagicMock(), config=CPLConfig()
    )
    job = make_job(id="native-job", state="running", source=JobSource.copilot_cli, external_session_id=_LOCAL_ID)
    runtime._get_job = AsyncMock(return_value=job)
    runtime._external_generations[job.id] = "fixture-generation"
    runtime._resume_orphaned = AsyncMock()
    runtime.send_message = AsyncMock()
    steer = CopilotSteerClient("fixture-token")
    post = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("POST", "https://fixture.invalid")))
    get = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"id": _TASK_ID, "state": "idle", "remote_steerable": True},
            request=httpx.Request("GET", "https://fixture.invalid"),
        )
    )
    monkeypatch.setattr(steer._client, "post", post)
    monkeypatch.setattr(steer._client, "get", get)
    watcher = SessionStateWatcher(AsyncMock(), runtime, factory, CPLConfig(), steer_client=steer)
    ingest = IngestService(factory, steer_client=steer, session_state_watcher=watcher)
    ingest._get_job = AsyncMock(return_value=job)
    runtime._ingest_service = ingest
    try:
        yield SimpleNamespace(
            runtime=runtime,
            job=job,
            watcher=watcher,
            ingest=ingest,
            steer=steer,
            workspace=workspace,
            root=root,
            post=post,
            get=get,
        )
    finally:
        await steer.close()


class TestClaudeControlPlane:
    def test_encode_cwd_and_uuid_filter(self) -> None:
        assert _encode_cwd("/home/dave01/repos/project") == "-home-dave01-repos-project"
        assert _SESSION_FILE_RE.match("a1b2c3d4-e5f6-7890-abcd-ef1234567890.jsonl")
        assert _SESSION_FILE_RE.match("not-a-uuid.jsonl") is None

    def test_pending_messages_drain(self, claude_watcher: ClaudeSessionStateWatcher) -> None:
        claude_watcher._session_to_job["session-123"] = "job-abc"
        claude_watcher._pending_messages["job-abc"] = ["one", "two"]

        assert claude_watcher.get_pending_messages("session-123") == ["one", "two"]
        assert claude_watcher.get_pending_messages("session-123") == []

    @pytest.mark.anyio
    async def test_abort_queues_stop_hook_message(self, claude_watcher: ClaudeSessionStateWatcher) -> None:
        claude_watcher._session_to_job["session-456"] = "job-def"

        await claude_watcher.abort_session("job-def")

        messages = claude_watcher.get_pending_messages("session-456")
        assert len(messages) == 1
        assert "abort" in messages[0].lower()

    def test_scan_finds_managed_session(self, claude_watcher: ClaudeSessionStateWatcher, tmp_path: Path) -> None:
        repo_path = "/home/user/repos/myproject"
        claude_watcher._config.repos = [repo_path]
        project_dir = tmp_path / _encode_cwd(repo_path)
        project_dir.mkdir(parents=True)
        session_file = project_dir / "a1b2c3d4-e5f6-7890-abcd-ef1234567890.jsonl"
        session_file.write_text("", encoding="utf-8")
        claude_watcher._started_at = 0

        with patch("backend.services.ingest.claude_source._CLAUDE_PROJECTS_DIR", tmp_path):
            results = claude_watcher._scan_for_new_sessions()

        assert results == [("a1b2c3d4-e5f6-7890-abcd-ef1234567890", session_file, repo_path)]

    def test_cross_platform_liveness_smoke(self, tmp_path: Path) -> None:
        assert _is_pid_alive(999999999) is False
        assert _find_claude_pids_at_cwd(str(tmp_path / "missing")) == []
        assert isinstance(_is_claude_process_alive("missing", None), bool)

    @pytest.mark.anyio
    async def test_discovery_membership_includes_projects_and_legacy_config(
        self,
        claude_watcher: ClaudeSessionStateWatcher,
        db_session: async_sessionmaker[AsyncSession],
    ) -> None:
        claude_watcher._config.repos = ["C:\\legacy\\repo"]
        async with db_session() as session:
            await ProjectRepository(session).create("project-1", "Canonical", ["C:\\project\\repo"])
            await session.commit()

        assert await claude_watcher._managed_repo_paths() == [
            "C:\\legacy\\repo",
            "C:\\project\\repo",
        ]

    @pytest.mark.anyio
    async def test_double_finalization_guard(
        self,
        claude_watcher: ClaudeSessionStateWatcher,
        db_session: async_sessionmaker[AsyncSession],
        event_processor: AsyncMock,
    ) -> None:
        await _insert_job(db_session, "job-fin", JobSource.claude_cli, "sess-fin")
        claude_watcher._session_to_job["sess-fin"] = "job-fin"
        claude_watcher._job_to_session["job-fin"] = "sess-fin"

        await claude_watcher._finalize_session("job-fin")
        call_count = event_processor.process_event.call_count
        await claude_watcher._finalize_session("job-fin")

        assert event_processor.process_event.call_count == call_count


class TestCopilotControlPlane:
    def test_query_discovers_remote_steerable_session(
        self,
        copilot_watcher: SessionStateWatcher,
        tmp_path: Path,
    ) -> None:
        store_path = tmp_path / "session-store.db"
        state_dir = tmp_path / "session-state"
        session_id = "sid-123"
        session_dir = state_dir / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "events.jsonl").write_text(
            json.dumps({"type": "session.started", "data": {"remoteSteerable": True}}) + "\n",
            encoding="utf-8",
        )
        db = sqlite3.connect(store_path)
        db.execute("CREATE TABLE sessions (id TEXT, cwd TEXT, summary TEXT, created_at TEXT)")
        db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (session_id, "C:\\repo\\myproject", "summary", datetime.now(UTC).isoformat()),
        )
        db.commit()
        db.close()

        with (
            patch("backend.services.ingest.copilot_source._SESSION_STORE_PATH", store_path),
            patch("backend.services.ingest.copilot_source._SESSION_STATE_DIR", state_dir),
        ):
            results = copilot_watcher._query_new_sessions()

        assert results == [(session_id, "C:\\repo\\myproject", "summary")]

    @pytest.mark.anyio
    async def test_copilot_send_and_abort_use_steer(self, copilot_watcher: SessionStateWatcher, tmp_path: Path) -> None:
        _write_native_metadata(tmp_path)
        with patch("backend.services.ingest.copilot_source._SESSION_STATE_DIR", tmp_path):
            await copilot_watcher.send_message(_LOCAL_ID, "hello")
            await copilot_watcher.abort_session(_LOCAL_ID)

        copilot_watcher._steer.send_message.assert_awaited_once_with(_TASK_ID, "hello")
        copilot_watcher._steer.abort.assert_awaited_once_with(_TASK_ID)

    @pytest.mark.anyio
    async def test_discovery_membership_includes_projects_and_legacy_config(
        self,
        copilot_watcher: SessionStateWatcher,
        db_session: async_sessionmaker[AsyncSession],
    ) -> None:
        copilot_watcher._config.repos = ["C:\\legacy\\repo"]
        async with db_session() as session:
            await ProjectRepository(session).create("project-1", "Canonical", ["C:\\project\\repo"])
            await session.commit()

        assert await copilot_watcher._managed_repo_paths() == [
            "C:\\legacy\\repo",
            "C:\\project\\repo",
        ]

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("repo_path", "expected_name"),
        [
            ("C:\\repo\\myproject", "myproject"),
            ("/repo/myproject", "myproject"),
        ],
    )
    async def test_create_job_uses_deterministic_id(
        self,
        copilot_watcher: SessionStateWatcher,
        db_session: async_sessionmaker[AsyncSession],
        repo_path: str,
        expected_name: str,
    ) -> None:
        session_id = "sid-abc"

        async def _run_git(*args: str, cwd: str) -> str:
            if args == ("rev-parse", "--show-toplevel"):
                return repo_path
            if args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
                return f"{repo_path}/.git"
            raise AssertionError(f"Unexpected git invocation: {args!r} cwd={cwd!r}")

        copilot_watcher._git._run_git = AsyncMock(side_effect=_run_git)  # type: ignore[attr-defined]

        async with db_session() as session:
            await ProjectRepository(session).create("project-1", "Canonical", [repo_path])
            await session.commit()

        job = await copilot_watcher._create_job(session_id, repo_path)

        assert job is not None
        assert job.id == f"{expected_name}-{hashlib.sha256(session_id.encode()).hexdigest()[:12]}"
        async with db_session() as session:
            persisted = await JobRepository(session).get(job.id)
        assert persisted is not None


class TestNativeBinding:
    async def test_commands_use_published_target_not_local_identity(self, native_control_stack):
        stack = native_control_stack
        assert await stack.runtime.send_operator_message(stack.job.id, "fixture") is True
        await stack.ingest.abort_session(stack.job.id)
        assert [call.args[0] for call in stack.post.await_args_list] == [
            f"https://api.enterprise.githubcopilot.com/agents/tasks/{_TASK_ID}/steer"
        ] * 2
        assert [call.kwargs["json"] for call in stack.post.await_args_list] == [
            {"content": "fixture", "type": "user"},
            {"type": "abort"},
        ]
        assert stack.job.external_session_id == _LOCAL_ID
        stack.runtime.send_message.assert_not_awaited()
        stack.runtime._resume_orphaned.assert_not_awaited()

    @pytest.mark.parametrize(
        "defect",
        [
            "missing",
            "yaml",
            "sequence",
            "unsafe",
            "missing_task",
            "task_type",
            "task_url",
            "task_path",
            "id_mismatch",
            "session_mismatch",
            "missing_session",
            "missing_id",
            "metadata_off",
            "metadata_string",
            "marker_off",
            "marker_missing",
            "missing_flag",
        ],
    )
    async def test_invalid_or_disabled_binding_denies_control(self, native_control_stack, defect):
        stack = native_control_stack
        metadata = json.loads(stack.workspace.read_text(encoding="utf-8"))
        raw = None
        if defect == "missing":
            stack.workspace.unlink()
        elif defect in {"yaml", "sequence", "unsafe"}:
            raw = {"yaml": "id: [", "sequence": "[]", "unsafe": "!!python/object:builtins.object {}"}[defect]
        elif defect.startswith("missing_"):
            metadata.pop(
                {
                    "missing_task": "mc_task_id",
                    "missing_session": "mc_session_id",
                    "missing_id": "id",
                    "missing_flag": "remote_steerable",
                }[defect]
            )
        elif defect.startswith("task_"):
            metadata["mc_task_id"] = {"task_type": 123, "task_url": "https://fixture.invalid", "task_path": "../other"}[
                defect
            ]
        elif defect in {"id_mismatch", "session_mismatch"}:
            metadata["id" if defect == "id_mismatch" else "mc_session_id"] = _OTHER_TASK_ID
        elif defect.startswith("metadata_"):
            metadata["remote_steerable"] = False if defect == "metadata_off" else "true"
        elif defect == "marker_missing":
            stack.workspace.with_name("events.jsonl").unlink()
        else:
            with stack.workspace.with_name("events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps({"type": "session.remote_steerable_changed", "data": {"remoteSteerable": False}}) + "\n"
                )
        if defect != "missing":
            stack.workspace.write_text(raw if raw is not None else json.dumps(metadata), encoding="utf-8")
        for operation in (
            stack.runtime.send_operator_message(stack.job.id, "fixture"),
            stack.ingest.abort_session(stack.job.id),
        ):
            with pytest.raises(copilot_steer.NativeDeliveryError) as error:
                await operation
            assert error.value.uncertain is False
        assert await stack.ingest.confirms_live(stack.job) is False
        stack.post.assert_not_awaited()
        stack.get.assert_not_awaited()
        stack.runtime._resume_orphaned.assert_not_awaited()

    @pytest.mark.parametrize("local_id", ["../escape", "..\\escape", "C:\\elsewhere", "", "not-a-uuid"])
    async def test_invalid_local_path_is_not_a_control_target(self, native_control_stack, local_id):
        stack = native_control_stack
        with pytest.raises(copilot_steer.NativeDeliveryError):
            await stack.watcher.send_message(local_id, "fixture")
        stack.post.assert_not_awaited()

    async def test_metadata_outside_owned_root_is_rejected(self, native_control_stack, monkeypatch, tmp_path):
        from pathlib import Path

        stack = native_control_stack
        original_resolve = Path.resolve
        outside = tmp_path / "outside" / "workspace.yaml"

        def redirected(path, *args, **kwargs):
            if path == stack.workspace:
                return outside
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", redirected)
        with pytest.raises(copilot_steer.NativeDeliveryError):
            await stack.runtime.send_operator_message(stack.job.id, "fixture")
        stack.post.assert_not_awaited()

    async def test_ingest_has_no_direct_local_id_fallback(self, native_control_stack):
        stack = native_control_stack
        stack.ingest._session_state_watcher = None
        for operation in (
            stack.ingest.send_operator_message(stack.job.id, "fixture"),
            stack.ingest.abort_session(stack.job.id),
        ):
            with pytest.raises(copilot_steer.NativeDeliveryError):
                await operation
        assert await stack.ingest.confirms_live(stack.job) is False
        stack.post.assert_not_awaited()
        stack.get.assert_not_awaited()

    async def test_no_client_is_typed_unavailable(self, native_control_stack):
        stack = native_control_stack
        stack.watcher._steer = None
        with pytest.raises(copilot_steer.NativeDeliveryError, match="unavailable"):
            await stack.runtime.send_operator_message(stack.job.id, "fixture")
        with pytest.raises(copilot_steer.NativeDeliveryError, match="unavailable"):
            await stack.ingest.abort_session(stack.job.id)
        assert await stack.ingest.confirms_live(stack.job) is False
        stack.post.assert_not_awaited()


class TestNativeBindingLiveness:
    async def test_live_check_does_not_read_metadata_or_poll_cloud(self, native_control_stack):
        stack = native_control_stack
        with (
            patch.object(stack.watcher, "_is_remote_steerable") as markers,
            patch("pathlib.Path.read_text", side_effect=AssertionError("Liveness must not read metadata")),
        ):
            assert await stack.ingest.confirms_live(stack.job) is False
        markers.assert_not_called()
        stack.get.assert_not_awaited()

    @pytest.mark.parametrize("binding_state", ["published", "missing", "mismatch", "off"])
    async def test_startup_reattaches_without_metadata_scan_or_cloud_lookup(
        self, native_control_stack, db_session, binding_state
    ):
        stack = native_control_stack
        await _insert_job(db_session, stack.job.id, JobSource.copilot_cli, _LOCAL_ID)
        stack.watcher._session_factory = db_session
        stack.watcher._start_tail = MagicMock()
        stack.watcher._finalize_session = AsyncMock()
        stack.watcher._runtime = MagicMock(register_external_session=AsyncMock())
        if binding_state == "missing":
            stack.workspace.unlink()
        elif binding_state != "published":
            data = json.loads(stack.workspace.read_text(encoding="utf-8"))
            data["mc_session_id" if binding_state == "mismatch" else "remote_steerable"] = (
                _OTHER_TASK_ID if binding_state == "mismatch" else False
            )
            stack.workspace.write_text(json.dumps(data), encoding="utf-8")
        with (
            patch.object(stack.watcher, "_is_remote_steerable") as markers,
            patch("pathlib.Path.read_text", side_effect=AssertionError("Startup must not read control metadata")),
        ):
            await stack.watcher._load_existing_sessions()
        markers.assert_not_called()
        stack.watcher._finalize_session.assert_not_awaited()
        assert stack.watcher._session_to_job == {_LOCAL_ID: stack.job.id}
        stack.watcher._start_tail.assert_called_once_with(
            _LOCAL_ID, stack.job.id, stack.root / _LOCAL_ID / "events.jsonl", initial_skip_count=0
        )
        async with db_session() as session:
            persisted = await JobRepository(session).get(stack.job.id)
        assert persisted.state == JobState.running and persisted.external_session_id == _LOCAL_ID
        stack.get.assert_not_awaited()

    async def test_tailing_does_not_schedule_diagnostic_liveness_poll(self, native_control_stack):
        stack = native_control_stack
        stack.watcher._running = True
        stack.watcher._session_to_job[_LOCAL_ID] = stack.job.id
        stack.watcher._finalize_session = AsyncMock()
        tail_started = asyncio.Event()
        keep_tailing = asyncio.Event()

        async def tail(*args, **kwargs):
            tail_started.set()
            await keep_tailing.wait()

        stack.watcher._tail_traceforge_events = AsyncMock(side_effect=tail)
        with patch.object(stack.watcher, "_is_remote_steerable") as markers:
            stack.watcher._start_tail(_LOCAL_ID, stack.job.id, stack.workspace.with_name("events.jsonl"))
            tasks = list(stack.watcher._tail_tasks.values()) + list(stack.watcher._liveness_tasks.values())
            try:
                await asyncio.wait_for(tail_started.wait(), timeout=1)
                assert not stack.watcher._liveness_tasks, "No diagnostic-only periodic poll should be scheduled"
                stack.watcher._tail_traceforge_events.assert_awaited_once()
                assert stack.watcher._tail_traceforge_events.call_args.kwargs["finalize_on_raw"] is (
                    stack.watcher._raw_terminal
                )
                markers.assert_not_called()
                stack.get.assert_not_awaited()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        stack.watcher._finalize_session.assert_not_awaited()

    @pytest.mark.parametrize(
        ("record", "reason"),
        [
            ({"type": "session.shutdown", "data": {}}, None),
            ({"type": "session.error", "data": {"message": "fixture failure"}}, "fixture failure"),
        ],
    )
    async def test_explicit_source_terminal_still_finalizes(self, native_control_stack, db_session, record, reason):
        stack = native_control_stack
        await _insert_job(db_session, stack.job.id, JobSource.copilot_cli, _LOCAL_ID)
        stack.watcher._session_factory = db_session
        stack.watcher._finalize_session = AsyncMock()
        stack.watcher._handle_tf_event = AsyncMock()
        events = stack.workspace.with_name("events.jsonl")
        events.write_text(json.dumps(record) + "\n", encoding="utf-8")
        await asyncio.wait_for(
            stack.watcher._tail_traceforge_events(
                _LOCAL_ID, stack.job.id, events, finalize_on_raw=stack.watcher._raw_terminal
            ),
            timeout=10,
        )
        stack.watcher._finalize_session.assert_awaited_once_with(stack.job.id, error_reason=reason)
        stack.get.assert_not_awaited()


class TestNativeControlMarkers:
    @pytest.mark.parametrize(
        ("initial", "updates", "expected"),
        [
            (False, [True], True),
            (False, [True, False], False),
            (True, [False], False),
            (True, [False, True], True),
        ],
    )
    def test_latest_valid_control_marker_wins(
        self, copilot_watcher: SessionStateWatcher, tmp_path: Path, initial: bool, updates: list[bool], expected: bool
    ) -> None:
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        events = [{"type": "session.start", "data": {"remoteSteerable": initial}}]
        events.extend(
            {"type": "session.remote_steerable_changed", "data": {"remoteSteerable": value}} for value in updates
        )
        (session_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
        )
        with patch("backend.services.ingest.copilot_source._SESSION_STATE_DIR", tmp_path):
            assert copilot_watcher._is_remote_steerable("session") is expected

    @pytest.mark.parametrize("kind", ["session.start", "session.started", "session.resume"])
    def test_supported_initial_and_resume_markers(
        self, copilot_watcher: SessionStateWatcher, tmp_path: Path, kind: str
    ) -> None:
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "events.jsonl").write_text(
            json.dumps({"type": "session.start", "data": {"remoteSteerable": False}})
            + "\n"
            + json.dumps({"type": kind, "data": {"remoteSteerable": True}})
            + "\n",
            encoding="utf-8",
        )
        with patch("backend.services.ingest.copilot_source._SESSION_STATE_DIR", tmp_path):
            assert copilot_watcher._is_remote_steerable("session") is True

    def test_unrelated_invalid_and_partial_records_cannot_override_valid_control(
        self, copilot_watcher: SessionStateWatcher, tmp_path: Path
    ) -> None:
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        events = [
            {"type": "session.start", "data": {"remoteSteerable": True}},
            {"type": "session.remote_steerable_changed", "data": {"remoteSteerable": False}},
            {"type": "assistant.message", "data": {"remoteSteerable": True}},
            {"type": "session.remote_steerable_changed", "data": {"remoteSteerable": "true"}},
            {"type": "session.remote_steerable_changed", "data": None},
            ["not", "an", "event"],
        ]
        (session_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + '\n{"type":"session.remote_steerable_changed",',
            encoding="utf-8",
        )
        with patch("backend.services.ingest.copilot_source._SESSION_STATE_DIR", tmp_path):
            assert copilot_watcher._is_remote_steerable("session") is False

    def test_dynamic_discovery_keeps_repository_and_creation_cutoff(
        self, copilot_watcher: SessionStateWatcher, tmp_path: Path
    ) -> None:
        store = tmp_path / "sessions.db"
        state = tmp_path / "state"
        rows = [
            ("eligible", "C:\\repo\\myproject", "eligible", "2026-01-03T00:00:00+00:00"),
            ("historical", "C:\\repo\\myproject", "historical", "2026-01-01T00:00:00+00:00"),
            ("unregistered", "C:\\repo\\elsewhere", "unregistered", "2026-01-03T00:00:00+00:00"),
        ]
        with sqlite3.connect(store) as db:
            db.execute("CREATE TABLE sessions (id TEXT, cwd TEXT, summary TEXT, created_at TEXT)")
            db.executemany("INSERT INTO sessions VALUES (?, ?, ?, ?)", rows)
        for session_id, *_ in rows:
            directory = state / session_id
            directory.mkdir(parents=True)
            (directory / "events.jsonl").write_text(
                json.dumps({"type": "session.start", "data": {"remoteSteerable": False}})
                + "\n"
                + json.dumps({"type": "session.remote_steerable_changed", "data": {"remoteSteerable": True}})
                + "\n",
                encoding="utf-8",
            )
        copilot_watcher._started_at = "2026-01-02T00:00:00+00:00"
        with (
            patch("backend.services.ingest.copilot_source._SESSION_STORE_PATH", store),
            patch("backend.services.ingest.copilot_source._SESSION_STATE_DIR", state),
        ):
            assert copilot_watcher._query_new_sessions() == [("eligible", "C:\\repo\\myproject", "eligible")]


class TestTraceForgeReattach:
    @pytest.mark.anyio
    async def test_reattach_skips_already_emitted_events(
        self,
        claude_watcher: ClaudeSessionStateWatcher,
        event_processor: AsyncMock,
        db_session: async_sessionmaker[AsyncSession],
        tmp_path: Path,
    ) -> None:
        await _insert_job(db_session, "job-skip", JobSource.claude_cli, "sess-skip")
        jsonl = tmp_path / "sess-skip.jsonl"
        jsonl.write_text(
            "\n".join(
                [
                    json.dumps({"type": "user", "message": {"content": "first"}}),
                    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "second"}]}}),
                    json.dumps({"type": "last-prompt"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        claude_watcher._running = True
        claude_watcher._session_to_job["sess-skip"] = "job-skip"
        claude_watcher._job_to_session["job-skip"] = "sess-skip"
        claude_watcher._job_worktrees["job-skip"] = "C:\\repo\\myproject"
        claude_watcher._job_base_refs["job-skip"] = "HEAD"
        from backend.persistence.job_repo import JobRepository
        from backend.services.ingest._base import _advance_prefix, _canonical_record, _NativeIdentity

        async with db_session() as session:
            repo = JobRepository(session)
            job = await repo.get("job-skip")
            identity = _NativeIdentity("claude", "sess-skip", "job-skip", job.created_at)
            await repo.update_ingest_checkpoint(
                "job-skip",
                {
                    "version": 1,
                    "record_count": 1,
                    "external_session_id": "sess-skip",
                    "mapping_sha256": identity.mapping_digest,
                    "prefix_sha256": _advance_prefix(
                        bytes(32), _canonical_record({"type": "user", "message": {"content": "first"}})
                    ).hex(),
                },
                1,
            )
            await session.commit()

        await claude_watcher._tail_traceforge_events(
            "sess-skip",
            "job-skip",
            jsonl,
            initial_skip_count=1,
            finalize_on_raw=claude_watcher._raw_terminal,
        )

        transcript_kinds = [
            call.args[1].kind for call in event_processor.process_event.call_args_list if not call.kwargs.get("replay")
        ]
        assert EventKind.message_user not in transcript_kinds
        assert EventKind.message_assistant in transcript_kinds
        assert any(call.kwargs.get("replay") for call in event_processor.process_event.call_args_list)


async def _insert_job(
    db_session: async_sessionmaker[AsyncSession],
    job_id: str,
    source: JobSource,
    external_session_id: str,
) -> None:
    async with db_session() as session:
        existing = await ProjectRepository(session).get("proj-1")
        if existing is None:
            await ProjectRepository(session).create("proj-1", "Test Project", ["C:\\repo\\myproject"])
        await JobRepository(session).create(
            Job(
                id=job_id,
                project_id="proj-1",
                repo="C:\\repo\\myproject",
                prompt="",
                state=JobState.running,
                source=source,
                external_session_id=external_session_id,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                sdk="claude" if source == JobSource.claude_cli else "copilot",
                base_ref="HEAD",
                branch="main",
                worktree_path="C:\\repo\\myproject",
                session_id=None,
            )
        )
        await session.commit()
