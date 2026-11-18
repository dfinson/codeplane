"""§6.1 equivalence oracle for imported ingestion: same vendor log ⇒ same canonical stream.

Drives the real ``NativeJsonlSource`` tail path and bundled Copilot/Claude mappings
against recorded vendor JSONL and asserts the emitted canonical ``SessionEvent``
stream carries the same dotted ``EventKind``s and the same salient payload/metadata
fields the retired hand-rolled watchers produced. Version-1 replay explicitly uses
the optional legacy implementation rather than fabricating a new-decoder checkpoint.

Claude fixtures are ported from the deleted ``test_claude_session_watcher.py``
inline JSONL shapes (user / assistant-text / thinking / tool_use / tool_result).
Copilot fixtures use the documented ``{type, data}`` session-event wire shape.

This is the imported half of the A2 collapse acceptance test; the funnel behavior
(turn/step annotation) is covered by ``test_event_processor.py`` and the telemetry
DB rollups by ``test_telemetry_subscriber.py``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import CPLConfig
from backend.models.db import Base
from backend.models.domain import Job, JobSource, JobState
from backend.models.events import EventKind, SessionEvent
from backend.persistence.database import _set_sqlite_pragmas
from backend.persistence.job_repo import JobRepository
from backend.services.ingest.claude_source import ClaudeSessionStateWatcher
from backend.services.ingest.copilot_source import SessionStateWatcher

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

pytestmark = pytest.mark.anyio


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


def _runtime_service() -> MagicMock:
    service = MagicMock()
    service.register_external_session = AsyncMock()
    service.finalize_external_session = AsyncMock()
    return service


def _git_service() -> AsyncMock:
    git = AsyncMock()
    git.get_current_branch = AsyncMock(return_value="main")
    git.rev_parse = AsyncMock(return_value="abc123")
    git._run_git = AsyncMock(return_value="C:\\repo\\myproject")
    return git


@pytest.fixture
def claude_watcher(
    event_processor: AsyncMock,
    db_session: async_sessionmaker[AsyncSession],
) -> ClaudeSessionStateWatcher:
    return ClaudeSessionStateWatcher(
        event_processor=event_processor,
        runtime_service=_runtime_service(),
        session_factory=db_session,
        config=CPLConfig(repos=["C:\\repo\\myproject"]),
        git_service=_git_service(),
    )


@pytest.fixture
def copilot_watcher(
    event_processor: AsyncMock,
    db_session: async_sessionmaker[AsyncSession],
) -> SessionStateWatcher:
    return SessionStateWatcher(
        event_processor=event_processor,
        runtime_service=_runtime_service(),
        session_factory=db_session,
        config=CPLConfig(repos=["C:\\repo\\myproject"]),
        git_service=_git_service(),
        steer_client=AsyncMock(),
    )


async def _insert_job(
    db_session: async_sessionmaker[AsyncSession],
    job_id: str,
    source: JobSource,
    external_session_id: str,
) -> None:
    async with db_session() as session:
        from backend.persistence.project_repo import ProjectRepository

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


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def _prime(watcher: object, session_id: str, job_id: str) -> None:
    watcher._running = True  # type: ignore[attr-defined]
    watcher._session_to_job[session_id] = job_id  # type: ignore[attr-defined]
    watcher._job_worktrees[job_id] = "C:\\repo\\myproject"  # type: ignore[attr-defined]
    watcher._job_base_refs[job_id] = "HEAD"  # type: ignore[attr-defined]
    # Skip the first-prompt background write so the tail schedules no bg tasks.
    watcher._prompt_captured.add(job_id)  # type: ignore[attr-defined]


# Injected by the ingest control plane (``_finalize_session``) through the same
# ``process_event`` seam on terminal — not mapping output, so excluded from the
# parse-equivalence assertions below.
_CONTROL_PLANE_KINDS = frozenset({EventKind.job_state_changed, EventKind.job_review})


def _emitted(event_processor: AsyncMock) -> list[SessionEvent]:
    return [
        call.args[1]
        for call in event_processor.process_event.call_args_list
        if call.args[1].kind not in _CONTROL_PLANE_KINDS
    ]


class TestClaudeImportedEquivalence:
    """Claude Code JSONL ⇒ canonical stream via the bundled ``claude`` mapping."""

    async def test_full_stream_kinds_and_salient_fields(
        self,
        claude_watcher: ClaudeSessionStateWatcher,
        event_processor: AsyncMock,
        db_session: async_sessionmaker[AsyncSession],
        tmp_path: Path,
    ) -> None:
        await _insert_job(db_session, "job-cl", JobSource.claude_cli, "sess-cl")
        _prime(claude_watcher, "sess-cl", "job-cl")

        jsonl = tmp_path / "sess-cl.jsonl"
        _write_jsonl(
            jsonl,
            [
                {"type": "user", "message": {"content": "Fix the bug in main.py"}},
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "Let me look at that file."}],
                        "model": "claude-sonnet-4",
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "thinking", "thinking": "Analyze the code first.", "signature": "s"}]
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"path": "main.py"}}]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "file contents here"}]
                    },
                },
                {"type": "last-prompt"},
            ],
        )

        await asyncio.wait_for(
            claude_watcher._tail_traceforge_events(
                "sess-cl", "job-cl", jsonl, finalize_on_raw=claude_watcher._raw_terminal
            ),
            timeout=10,
        )

        events = _emitted(event_processor)
        assert [e.kind for e in events] == [
            EventKind.message_user,
            EventKind.message_assistant,
            EventKind.llm_reasoning_chunk,
            EventKind.tool_call_started,
            EventKind.tool_call_completed,
        ]
        # every imported Claude event is attributed to the claude framework
        assert all(e.metadata.source_framework == "claude" for e in events)

        user, assistant, reasoning, started, completed = events
        assert user.payload["content"] == "Fix the bug in main.py"
        assert assistant.payload["content"] == "Let me look at that file."
        assert reasoning.payload["content"] == "Analyze the code first."

        assert started.payload["tool_name"] == "Read"
        assert started.payload["arguments"] == {"path": "main.py"}
        assert started.payload["tool_call_id"] == "tu_1"
        # motivation is derived natively from the preceding assistant text
        assert started.metadata.motivation is not None
        assert started.metadata.motivation.intent == "Let me look at that file."

        assert completed.payload["tool_call_id"] == "tu_1"
        assert completed.payload["success"] is True
        assert completed.payload["result"] == "file contents here"

    async def test_reattach_skip_is_deterministic(
        self,
        claude_watcher: ClaudeSessionStateWatcher,
        event_processor: AsyncMock,
        db_session: async_sessionmaker[AsyncSession],
        tmp_path: Path,
    ) -> None:
        """Same log + initial_skip_count ⇒ only the un-emitted tail is re-produced."""
        await _insert_job(db_session, "job-re", JobSource.claude_cli, "sess-re")
        _prime(claude_watcher, "sess-re", "job-re")

        jsonl = tmp_path / "sess-re.jsonl"
        _write_jsonl(
            jsonl,
            [
                {"type": "user", "message": {"content": "first"}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "second"}]}},
                {"type": "last-prompt"},
            ],
        )

        from backend.persistence.job_repo import JobRepository
        from backend.services.ingest._base import _advance_prefix, _canonical_record, _LegacyNativeIdentity

        async with db_session() as session:
            repo = JobRepository(session)
            job = await repo.get("job-re")
            identity = _LegacyNativeIdentity("claude", "sess-re", "job-re", job.created_at)
            await repo.update_ingest_checkpoint(
                "job-re",
                {
                    "version": 1,
                    "record_count": 1,
                    "external_session_id": "sess-re",
                    "mapping_sha256": identity.mapping_digest,
                    "prefix_sha256": _advance_prefix(
                        bytes(32), _canonical_record({"type": "user", "message": {"content": "first"}})
                    ).hex(),
                },
                1,
            )
            await session.commit()

        await asyncio.wait_for(
            claude_watcher._tail_traceforge_events(
                "sess-re", "job-re", jsonl, initial_skip_count=1, finalize_on_raw=claude_watcher._raw_terminal
            ),
            timeout=10,
        )

        kinds = [
            call.args[1].kind
            for call in event_processor.process_event.call_args_list
            if not call.kwargs.get("replay") and call.args[1].kind not in _CONTROL_PLANE_KINDS
        ]
        assert EventKind.message_user not in kinds
        assert kinds == [EventKind.message_assistant]
        async with db_session() as session:
            retained = await JobRepository(session).get_ingest_checkpoint("job-re")
        assert retained["version"] == 1
        assert retained["mapping_sha256"] == identity.mapping_digest
        assert "source" not in retained


class TestCopilotImportedEquivalence:
    """Copilot CLI session JSONL ⇒ canonical stream via the bundled ``copilot`` mapping."""

    async def test_full_stream_kinds_and_salient_fields(
        self,
        copilot_watcher: SessionStateWatcher,
        event_processor: AsyncMock,
        db_session: async_sessionmaker[AsyncSession],
        tmp_path: Path,
    ) -> None:
        await _insert_job(db_session, "job-co", JobSource.copilot_cli, "sess-co")
        _prime(copilot_watcher, "sess-co", "job-co")

        jsonl = tmp_path / "events.jsonl"
        _write_jsonl(
            jsonl,
            [
                {"type": "user.message", "data": {"content": "Fix the bug"}},
                {"type": "assistant.reasoning", "data": {"content": "Consider the edit"}},
                {"type": "assistant.message", "data": {"content": "I'll edit the file"}},
                {
                    "type": "tool.execution_start",
                    "data": {"toolCallId": "tc1", "toolName": "str_replace_editor", "arguments": {"path": "app.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "data": {"toolCallId": "tc1", "success": True, "result": {"content": "edited"}},
                },
                {
                    "type": "assistant.usage",
                    "data": {"model": "gpt-4o", "inputTokens": 100, "outputTokens": 50, "cost": 0.25, "duration": 1200},
                },
                {"type": "session.workspace_file_changed", "data": {"path": "app.py", "operation": "edit"}},
                {"type": "session.shutdown", "data": {"shutdownType": "normal"}},
            ],
        )

        await asyncio.wait_for(
            copilot_watcher._tail_traceforge_events("sess-co", "job-co", jsonl),
            timeout=10,
        )

        events = _emitted(event_processor)
        assert [e.kind for e in events] == [
            EventKind.message_user,
            EventKind.llm_reasoning_chunk,
            EventKind.message_assistant,
            EventKind.tool_call_started,
            EventKind.tool_call_completed,
            EventKind.telemetry_usage,
            EventKind.file_edited,
            EventKind.session_ended,
        ]
        assert all(e.metadata.source_framework == "copilot" for e in events)

        by_kind = {e.kind: e for e in events}
        assert by_kind[EventKind.message_user].payload["content"] == "Fix the bug"
        assert by_kind[EventKind.llm_reasoning_chunk].payload["content"] == "Consider the edit"
        assert by_kind[EventKind.message_assistant].payload["content"] == "I'll edit the file"

        started = by_kind[EventKind.tool_call_started]
        assert started.payload["tool_name"] == "str_replace_editor"
        assert started.payload["arguments"] == {"path": "app.py"}
        assert started.payload["tool_call_id"] == "tc1"

        completed = by_kind[EventKind.tool_call_completed]
        assert completed.payload["tool_call_id"] == "tc1"
        assert completed.payload["success"] is True
        assert completed.payload["result"] == "edited"
        # Copilot's tool.execution_complete carries no tool_name — pairing is by id.
        assert "tool_name" not in completed.payload

        usage = by_kind[EventKind.telemetry_usage]
        assert usage.payload["model"] == "gpt-4o"
        assert usage.payload["input_tokens"] == 100
        assert usage.payload["output_tokens"] == 50
        assert usage.payload["cost_usd"] == 0.25
        assert usage.payload["duration_ms"] == 1200

        edited = by_kind[EventKind.file_edited]
        assert edited.payload["path"] == "app.py"
        assert edited.payload["operation"] == "edit"
