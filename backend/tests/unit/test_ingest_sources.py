"""Control-plane tests for TraceForge-backed imported ingestion sources."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

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
from backend.services.ingest.claude_source import (
    _SESSION_FILE_RE,
    ClaudeSessionStateWatcher,
    _encode_cwd,
    _find_claude_pids_at_cwd,
    _is_claude_process_alive,
    _is_pid_alive,
)
from backend.services.ingest.copilot_source import SessionStateWatcher

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
    async def test_copilot_send_and_abort_use_steer(self, copilot_watcher: SessionStateWatcher) -> None:
        await copilot_watcher.send_message("sid", "hello")
        await copilot_watcher.abort_session("sid")

        copilot_watcher._steer.send_message.assert_awaited_once_with("sid", "hello")
        copilot_watcher._steer.abort.assert_awaited_once_with("sid")

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
