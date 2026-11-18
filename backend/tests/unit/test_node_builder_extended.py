"""Extended coverage tests for backend/services/trail/node_builder.py.

Targets the ~110 missed lines: snapshot save/load, session_resumed
rehydration (lossy + snapshot), transcript handling (operator, assistant,
tool_call), approval with/without active step, canceled steps, job_canceled
terminal status, classify_and_emit error recovery, write sub-node DB
failures, and edge cases in _extract_snippet and classify_step.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from traceforge.types import EventMetadata, ToolMotivation

from backend.models.db import Base, JobRow, JobTelemetrySpanRow, ProjectRow, TrailNodeRow
from backend.models.events import EventKind, SessionEvent, new_event
from backend.persistence.trail_repo import TrailNodeRepository
from backend.services.trail.models import (
    MESSAGE_SIGNAL_BUFFER_SIZE,
    Activity,
    PlanStep,
    TrailJobState,
)
from backend.services.trail.node_builder import (
    TrailNodeBuilder,
    _extract_snippet,
    classify_step,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def trail_repo(session_factory):
    return TrailNodeRepository(session_factory)


@pytest.fixture
def job_state():
    return {}


@pytest.fixture
def builder(session_factory, job_state, trail_repo):
    return TrailNodeBuilder(
        session_factory=session_factory,
        job_state=job_state,
        repo=trail_repo,
    )


def _make_event(
    kind: EventKind = EventKind.job_state_changed,
    job_id: str = "job-1",
    payload: dict | None = None,
    metadata: EventMetadata | None = None,
) -> SessionEvent:
    return new_event(
        session_id=job_id,
        timestamp=datetime.now(UTC),
        kind=kind,
        payload=payload or {},
        metadata=metadata,
    )


def _started_event(job_id: str = "job-1") -> SessionEvent:
    return _make_event(
        EventKind.job_state_changed,
        job_id=job_id,
        payload={"previous_state": "queued", "new_state": "running"},
    )


async def _insert_job_row(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: str,
    prompt: str = "Fix the bug",
    trail_state_snapshot: str | None = None,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            ProjectRow(
                id="proj-1",
                name="Test Project",
                repo_paths='["/tmp/repo"]',
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        row = JobRow(
            id=job_id,
            repo="/tmp/repo",
            project_id="proj-1",
            prompt=prompt,
            state="running",
            branch="main",
            base_ref="main",
            sdk="claude_code",
            created_at=now,
            updated_at=now,
        )
        if trail_state_snapshot is not None:
            row.trail_state_snapshot = trail_state_snapshot
        session.add(row)
        await session.commit()


async def _insert_file_write_span(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: str,
    turn_id: str,
    name: str = "write_file",
    tool_target: str = "src/a.py",
    tool_args_json: str | None = None,
    motivation_summary: str | None = None,
    edit_motivations: str | None = None,
    is_retry: bool = False,
    error_kind: str | None = None,
    preceding_context: str | None = None,
) -> None:
    async with session_factory() as session:
        span = JobTelemetrySpanRow(
            job_id=job_id,
            span_type="tool",
            name=name,
            started_at=str(datetime.now(UTC).timestamp()),
            duration_ms="100",
            attrs_json="{}",
            created_at=datetime.now(UTC),
            tool_category="file_write",
            tool_target=tool_target,
            tool_args_json=tool_args_json,
            turn_id=turn_id,
            motivation_summary=motivation_summary,
            edit_motivations=edit_motivations,
            is_retry=is_retry,
            error_kind=error_kind,
            preceding_context=preceding_context,
        )
        session.add(span)
        await session.commit()


# ---------------------------------------------------------------------------
# _extract_snippet edge cases
# ---------------------------------------------------------------------------


class TestExtractSnippetExtended:
    def test_non_dict_json_returns_empty(self):
        """JSON that parses to a list (not dict) should return empty."""
        assert _extract_snippet(json.dumps([1, 2, 3]), "write") == ""

    def test_old_string_alias(self):
        """old_string / new_string keys (third alias) are recognized."""
        args = json.dumps({"old_string": "alpha", "new_string": "beta"})
        result = _extract_snippet(args, "edit")
        assert "- alpha" in result
        assert "+ beta" in result

    def test_content_key(self):
        """The 'content' key path is used for file creation."""
        args = json.dumps({"content": "line1\nline2"})
        result = _extract_snippet(args, "create")
        assert "+ line1" in result
        assert "+ line2" in result

    def test_new_text_alias(self):
        """newText (camelCase variant) is recognized."""
        args = json.dumps({"newText": "inserted text"})
        result = _extract_snippet(args, "insert")
        assert "+ inserted text" in result

    def test_multiline_truncation(self):
        """Lines beyond max_lines (8) are truncated."""
        lines = [f"line{i}" for i in range(20)]
        args = json.dumps({"file_text": "\n".join(lines)})
        result = _extract_snippet(args, "create")
        # Should have at most 8 '+' lines
        plus_lines = [ln for ln in result.splitlines() if ln.startswith("+ ")]
        assert len(plus_lines) == 8

    def test_blank_lines_stripped_from_content(self):
        """Blank lines in file_text content are skipped."""
        args = json.dumps({"file_text": "\n\nimport os\n\n\nprint('hi')\n\n"})
        result = _extract_snippet(args, "create")
        plus_lines = [ln for ln in result.splitlines() if ln.startswith("+ ")]
        assert len(plus_lines) == 2

    def test_only_old_str_no_new_str(self):
        """When only old_str is present (deletion), only '-' lines appear."""
        args = json.dumps({"old_str": "removed"})
        result = _extract_snippet(args, "edit")
        assert "- removed" in result
        assert "+" not in result

    def test_only_new_str_no_old_str(self):
        """When only new_str is present (insertion), only '+' lines appear."""
        args = json.dumps({"new_str": "added"})
        result = _extract_snippet(args, "edit")
        assert "+ added" in result
        assert "-" not in result


# ---------------------------------------------------------------------------
# classify_step edge cases
# ---------------------------------------------------------------------------


class TestClassifyStepExtended:
    def test_same_sha_no_files_returns_shell(self):
        assert classify_step({"start_sha": "aaa", "end_sha": "aaa"}) == "shell"

    def test_only_start_sha_returns_shell(self):
        assert classify_step({"start_sha": "aaa"}) == "shell"

    def test_only_end_sha_returns_shell(self):
        assert classify_step({"end_sha": "bbb"}) == "shell"

    def test_empty_files_lists_returns_shell(self):
        assert classify_step({"files_written": [], "files_read": []}) == "shell"


# ---------------------------------------------------------------------------
# handle_event dispatch + safety-net
# ---------------------------------------------------------------------------


class TestHandleEventDispatch:
    async def test_unknown_event_kind_ignored(self, builder):
        """Events not in the dispatch table are silently ignored."""
        event = _make_event(EventKind.diff_updated, payload={"diff": "..."})
        await builder.handle_event(event)  # should not raise

    async def test_job_state_changed_non_running_ignored(self, builder, job_state):
        """job_state_changed with new_state != 'running' does nothing."""
        event = _make_event(
            EventKind.job_state_changed,
            payload={"new_state": "queued"},
        )
        await builder.handle_event(event)
        assert "job-1" not in job_state

    async def test_job_state_changed_already_tracked_ignored(self, builder, job_state, session_factory):
        """If job already in _job_state, a second running event is ignored."""
        await _insert_job_row(session_factory, "job-1")
        await builder.handle_event(_started_event())
        assert "job-1" in job_state
        old_seq = job_state["job-1"].next_seq
        # Fire again
        await builder.handle_event(_started_event())
        # Seq should not have advanced (no duplicate goal node)
        assert job_state["job-1"].next_seq == old_seq

    async def test_safety_net_catches_exceptions(self, builder, job_state, trail_repo):
        """handle_event swallows exceptions from inner handlers."""
        job_state["job-1"] = TrailJobState()
        # Patch repo.create to explode
        trail_repo.create = AsyncMock(side_effect=RuntimeError("boom"))
        event = _make_event(
            EventKind.step_completed,
            payload={"step_id": "s1", "files_read": ["a.py"]},
        )
        # Should not raise
        await builder.handle_event(event)


# ---------------------------------------------------------------------------
# _on_job_started — prompt from DB
# ---------------------------------------------------------------------------


class TestJobStartedPrompt:
    async def test_goal_node_captures_prompt_from_db(self, builder, session_factory, trail_repo, job_state):
        """When the JobRow exists, its prompt is stored in the goal node."""
        await _insert_job_row(session_factory, "job-1", prompt="Implement feature X")
        await builder.handle_event(_started_event())

        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 1
        assert nodes[0].intent == "Implement feature X"
        assert job_state["job-1"].job_prompt == "Implement feature X"

    async def test_goal_node_empty_prompt_when_no_job_row(self, builder, trail_repo, job_state):
        """When no JobRow exists, prompt defaults to empty."""
        await builder.handle_event(_started_event())
        nodes = await trail_repo.get_by_job("job-1")
        assert nodes[0].intent is None or nodes[0].intent == ""


# ---------------------------------------------------------------------------
# _on_step_started
# ---------------------------------------------------------------------------


class TestStepStarted:
    async def test_step_started_sets_active_step_id(self, builder, job_state):
        job_state["job-1"] = TrailJobState()
        event = _make_event(
            EventKind.step_started,
            payload={"step_id": "step-42"},
        )
        await builder.handle_event(event)
        assert job_state["job-1"].active_step_id == "step-42"

    async def test_step_started_unknown_job_ignored(self, builder, job_state):
        event = _make_event(
            EventKind.step_started,
            job_id="no-such-job",
            payload={"step_id": "s1"},
        )
        await builder.handle_event(event)
        assert "no-such-job" not in job_state


# ---------------------------------------------------------------------------
# _on_step_completed — canceled status, tool_names, diff stats
# ---------------------------------------------------------------------------


class TestStepCompleted:
    async def test_canceled_step_skipped(self, builder, trail_repo, job_state):
        """Steps with status='canceled' produce no trail node."""
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.step_completed,
            payload={"step_id": "s1", "status": "canceled"},
        )
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 0

    async def test_tool_names_serialized(self, builder, trail_repo, job_state):
        """tool_names from payload are JSON-serialized on the node."""
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.step_completed,
            payload={
                "step_id": "s1",
                "tool_names": ["read_file", "write_file"],
                "tool_count": 2,
            },
        )
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 1
        assert json.loads(nodes[0].tool_names) == ["read_file", "write_file"]
        assert nodes[0].tool_count == 2

    async def test_diff_additions_deletions(self, builder, trail_repo, job_state):
        """diff_additions and diff_deletions are stored from payload."""
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.step_completed,
            payload={
                "step_id": "s1",
                "files_written": ["a.py"],
                "diff_additions": 15,
                "diff_deletions": 3,
            },
        )
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        assert nodes[0].diff_additions == 15
        assert nodes[0].diff_deletions == 3

    async def test_step_completed_emits_pending_events(self, builder, trail_repo, job_state):
        """Pending events accumulated before step_completed are flushed."""
        state = TrailJobState(active_goal_id="g1", active_step_id="s1")
        pending_event = _make_event(
            EventKind.approval_requested,
            payload={"description": "Deploy to prod?"},
        )
        state.pending_events.append(pending_event)
        job_state["job-1"] = state

        event = _make_event(
            EventKind.step_completed,
            payload={"step_id": "s1", "files_read": ["a.py"]},
        )
        await builder.handle_event(event)

        nodes = await trail_repo.get_by_job("job-1")
        kinds = [n.kind for n in nodes]
        assert "request" in kinds
        request_node = next(n for n in nodes if n.kind == "request")
        assert request_node.intent == "Deploy to prod?"

    async def test_step_completed_clears_pending(self, builder, job_state):
        """After flushing, pending_events is empty."""
        state = TrailJobState(active_goal_id="g1", active_step_id="s1")
        state.pending_events.append(_make_event(EventKind.approval_requested, payload={"description": "x"}))
        job_state["job-1"] = state
        await builder.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={"step_id": "s1"},
            )
        )
        assert len(state.pending_events) == 0

    async def test_files_deduped_writes_before_reads(self, builder, trail_repo, job_state):
        """files list is deduped with writes first."""
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.step_completed,
            payload={
                "step_id": "s1",
                "files_written": ["b.py", "a.py"],
                "files_read": ["a.py", "c.py"],
            },
        )
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        files = json.loads(nodes[0].files)
        # b.py, a.py from writes, then c.py from reads (a.py deduped)
        assert files == ["b.py", "a.py", "c.py"]


# ---------------------------------------------------------------------------
# _on_phase_changed
# ---------------------------------------------------------------------------


class TestPhaseChanged:
    async def test_phase_updates_state(self, builder, job_state, trail_repo):
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.execution_phase_changed,
            payload={"phase": "verification"},
        )
        await builder.handle_event(event)
        assert job_state["job-1"].current_phase == "verification"
        nodes = await trail_repo.get_by_job("job-1")
        assert nodes[0].kind == "summarize"
        assert nodes[0].intent == "Phase: verification"

    async def test_phase_changed_no_state_ignored(self, builder, job_state, trail_repo):
        event = _make_event(
            EventKind.execution_phase_changed,
            job_id="unknown",
            payload={"phase": "coding"},
        )
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("unknown")
        assert len(nodes) == 0


# ---------------------------------------------------------------------------
# _on_approval_requested — deferred vs immediate
# ---------------------------------------------------------------------------


class TestApprovalRequested:
    async def test_approval_immediate_when_no_active_step(self, builder, trail_repo, job_state):
        """Without active_step_id, approval creates node immediately."""
        job_state["job-1"] = TrailJobState(active_goal_id="g1", active_step_id=None)
        event = _make_event(
            EventKind.approval_requested,
            payload={"description": "Run tests?"},
        )
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 1
        assert nodes[0].kind == "request"
        assert nodes[0].intent == "Run tests?"

    async def test_approval_deferred_when_active_step(self, builder, job_state):
        """With active_step_id set, approval is deferred."""
        state = TrailJobState(active_goal_id="g1", active_step_id="s1")
        job_state["job-1"] = state
        event = _make_event(
            EventKind.approval_requested,
            payload={"description": "Deploy?"},
        )
        await builder.handle_event(event)
        assert len(state.pending_events) == 1

    async def test_approval_no_state_ignored(self, builder, job_state):
        event = _make_event(
            EventKind.approval_requested,
            job_id="ghost",
            payload={"description": "hi"},
        )
        await builder.handle_event(event)
        assert "ghost" not in job_state


# ---------------------------------------------------------------------------
# _on_job_terminal — completed, failed, canceled, job_review
# ---------------------------------------------------------------------------


class TestJobTerminal:
    async def test_job_canceled_status(self, builder, trail_repo, job_state):
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(EventKind.job_canceled, payload={})
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        assert nodes[0].intent == "Job canceled"
        assert "job-1" not in job_state

    async def test_job_review_status(self, builder, trail_repo, job_state):
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(EventKind.job_review, payload={})
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        assert nodes[0].intent == "Job ready for review"
        assert "job-1" not in job_state

    async def test_job_terminal_no_state_ignored(self, builder, trail_repo, job_state):
        event = _make_event(EventKind.job_completed, job_id="no-state", payload={})
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("no-state")
        assert len(nodes) == 0


# ---------------------------------------------------------------------------
# _on_transcript_updated — operator, assistant, tool_call
# ---------------------------------------------------------------------------


class TestTranscriptOperator:
    async def test_operator_creates_request_node(self, builder, trail_repo, job_state):
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.message_user,
            payload={"role": "operator", "content": "Focus on auth module"},
        )
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 1
        assert nodes[0].kind == "request"
        assert nodes[0].agent_message == "Focus on auth module"

    async def test_user_role_also_creates_request_node(self, builder, trail_repo, job_state):
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.message_user,
            payload={"role": "user", "content": "Please check"},
        )
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 1
        assert nodes[0].agent_message == "Please check"

    async def test_operator_empty_content_skipped(self, builder, trail_repo, job_state):
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.message_user,
            payload={"role": "operator", "content": "   "},
        )
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 0

    async def test_operator_populates_recent_messages(self, builder, job_state):
        state = TrailJobState(active_goal_id="g1")
        job_state["job-1"] = state
        event = _make_event(
            EventKind.message_user,
            payload={"role": "operator", "content": "hello"},
        )
        await builder.handle_event(event)
        assert "[operator] hello" in state.recent_messages

    async def test_operator_truncates_recent_messages(self, builder, job_state):
        state = TrailJobState(active_goal_id="g1")
        state.recent_messages = [f"msg-{i}" for i in range(MESSAGE_SIGNAL_BUFFER_SIZE)]
        job_state["job-1"] = state
        event = _make_event(
            EventKind.message_user,
            payload={"role": "operator", "content": "overflow"},
        )
        await builder.handle_event(event)
        assert len(state.recent_messages) == MESSAGE_SIGNAL_BUFFER_SIZE
        assert state.recent_messages[-1] == "[operator] overflow"


class TestTranscriptAssistant:
    async def test_assistant_populates_recent_messages(self, builder, job_state):
        state = TrailJobState(active_goal_id="g1")
        job_state["job-1"] = state
        event = _make_event(
            EventKind.message_assistant,
            payload={"role": "assistant", "content": "I will fix the bug"},
        )
        await builder.handle_event(event)
        assert "[assistant] I will fix the bug" in state.recent_messages

    async def test_agent_role_also_works(self, builder, job_state):
        state = TrailJobState(active_goal_id="g1")
        job_state["job-1"] = state
        event = _make_event(
            EventKind.message_assistant,
            payload={"role": "agent", "content": "Working on it"},
        )
        await builder.handle_event(event)
        assert "[assistant] Working on it" in state.recent_messages

    async def test_assistant_empty_content_skipped(self, builder, job_state):
        state = TrailJobState(active_goal_id="g1")
        job_state["job-1"] = state
        event = _make_event(
            EventKind.message_assistant,
            payload={"role": "assistant", "content": ""},
        )
        await builder.handle_event(event)
        assert len(state.recent_messages) == 0

    async def test_assistant_long_message_truncated(self, builder, job_state):
        state = TrailJobState(active_goal_id="g1")
        job_state["job-1"] = state
        long_msg = "x" * 500
        event = _make_event(
            EventKind.message_assistant,
            payload={"role": "assistant", "content": long_msg},
        )
        await builder.handle_event(event)
        stored = state.recent_messages[-1]
        # Summary is truncated to 200 chars
        assert len(stored) == len("[assistant] ") + 200

    async def test_assistant_truncates_buffer(self, builder, job_state):
        state = TrailJobState(active_goal_id="g1")
        state.recent_messages = [f"m{i}" for i in range(MESSAGE_SIGNAL_BUFFER_SIZE)]
        job_state["job-1"] = state
        event = _make_event(
            EventKind.message_assistant,
            payload={"role": "assistant", "content": "newest"},
        )
        await builder.handle_event(event)
        assert len(state.recent_messages) == MESSAGE_SIGNAL_BUFFER_SIZE


class TestTranscriptToolCall:
    async def test_tool_call_updates_write_node(self, builder, trail_repo, job_state):
        """tool_call transcript updates matching write sub-node metadata."""
        # Pre-create a write node
        node = TrailNodeRow(
            id="w1",
            job_id="job-1",
            seq=1,
            anchor_seq=1,
            kind="write",
            deterministic_kind="write",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            parent_id="p1",
            turn_id="turn-1",
            tool_name="str_replace_editor",
        )
        await trail_repo.create(node)
        job_state["job-1"] = TrailJobState(active_goal_id="g1")

        event = _make_event(
            EventKind.tool_call_completed,
            payload={
                "turn_id": "turn-1",
                "tool_name": "str_replace_editor",
                "success": True,
            },
            metadata=EventMetadata(
                tool_display="Edit main.py",
                motivation=ToolMotivation(intent="Fix import"),
            ),
        )
        await builder.handle_event(event)

        updated = await trail_repo.get("w1")
        assert updated.tool_display == "Edit main.py"
        assert updated.tool_intent == "Fix import"
        assert updated.tool_success is True

    async def test_tool_call_report_intent_skipped(self, builder, job_state):
        """report_intent tool calls are skipped."""
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.tool_call_completed,
            payload={
                "turn_id": "turn-1",
                "tool_name": "report_intent",
            },
        )
        # Should not raise or do anything
        await builder.handle_event(event)

    async def test_tool_call_empty_tool_name_skipped(self, builder, job_state):
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.tool_call_completed,
            payload={"turn_id": "t1", "tool_name": ""},
        )
        await builder.handle_event(event)

    async def test_tool_call_no_turn_id_skipped(self, builder, job_state):
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.tool_call_completed,
            payload={"tool_name": "write_file"},
        )
        await builder.handle_event(event)

    async def test_tool_call_no_job_id_skipped(self, builder, job_state):
        """tool_call with no job_id on the event is skipped."""
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.tool_call_completed,
            job_id="job-1",
            payload={
                "turn_id": "t1",
                "tool_name": "write_file",
            },
        )
        # Override event session_id to empty to test the guard
        event = event.model_copy(update={"session_id": ""})
        await builder.handle_event(event)

    async def test_tool_success_false(self, builder, trail_repo, job_state):
        node = TrailNodeRow(
            id="w2",
            job_id="job-1",
            seq=1,
            anchor_seq=1,
            kind="write",
            deterministic_kind="write",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            parent_id="p1",
            turn_id="turn-1",
            tool_name="write_file",
        )
        await trail_repo.create(node)
        job_state["job-1"] = TrailJobState(active_goal_id="g1")

        event = _make_event(
            EventKind.tool_call_completed,
            payload={
                "turn_id": "turn-1",
                "tool_name": "write_file",
                "success": False,
            },
        )
        await builder.handle_event(event)
        updated = await trail_repo.get("w2")
        assert updated.tool_success is False

    async def test_transcript_unknown_role_ignored(self, builder, job_state, trail_repo):
        job_state["job-1"] = TrailJobState(active_goal_id="g1")
        event = _make_event(
            EventKind.message_assistant,
            payload={"role": "system", "content": "internal"},
        )
        await builder.handle_event(event)
        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 0

    async def test_transcript_no_state_ignored(self, builder, job_state):
        event = _make_event(
            EventKind.message_user,
            job_id="ghost",
            payload={"role": "operator", "content": "hi"},
        )
        await builder.handle_event(event)


# ---------------------------------------------------------------------------
# _classify_and_emit — error recovery
# ---------------------------------------------------------------------------


class TestClassifyAndEmit:
    async def test_classify_and_emit_swallows_error(self, session_factory, trail_repo, job_state):
        """_classify_and_emit catches exceptions and resets enrichment."""
        plan_manager = MagicMock()
        plan_manager.classify_turn = AsyncMock(side_effect=RuntimeError("boom"))

        builder = TrailNodeBuilder(
            session_factory=session_factory,
            job_state=job_state,
            repo=trail_repo,
            plan_manager=plan_manager,
        )
        state = TrailJobState(active_goal_id="g1")
        job_state["job-1"] = state

        event = _make_event(
            EventKind.step_completed,
            payload={"step_id": "s1", "files_read": ["a.py"]},
        )
        # Should not raise
        await builder.handle_event(event)
        await builder.flush_background_tasks()
        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 1

    async def test_classify_delegates_to_plan_manager(self, session_factory, trail_repo, job_state):
        """Plan classification still runs after ActivityTracker retirement."""
        plan_manager = MagicMock()
        plan_manager.classify_turn = AsyncMock(return_value="ps-1")

        builder = TrailNodeBuilder(
            session_factory=session_factory,
            job_state=job_state,
            repo=trail_repo,
            plan_manager=plan_manager,
        )
        state = TrailJobState(active_goal_id="g1")
        job_state["job-1"] = state

        event = _make_event(
            EventKind.step_completed,
            payload={
                "step_id": "s1",
                "turn_id": "turn-1",
                "files_read": ["a.py"],
                "agent_message": "Reading code",
                "duration_ms": 500,
            },
        )
        await builder.handle_event(event)
        await builder.flush_background_tasks()
        plan_manager.classify_turn.assert_awaited_once()


# ---------------------------------------------------------------------------
# _create_write_sub_nodes — DB error handling
# ---------------------------------------------------------------------------


class TestWriteSubNodeErrors:
    async def test_db_error_in_write_sub_nodes_is_swallowed(self, session_factory, job_state, trail_repo):
        """DBAPIError during write sub-node creation doesn't break the step."""
        builder = TrailNodeBuilder(
            session_factory=session_factory,
            job_state=job_state,
            repo=trail_repo,
        )
        state = TrailJobState(active_goal_id="g1")
        job_state["job-1"] = state

        # Insert a span so the code tries to create write nodes
        await _insert_file_write_span(
            session_factory,
            job_id="job-1",
            turn_id="turn-1",
            tool_target="src/a.py",
        )

        # Patch create_many to fail
        original_create_many = trail_repo.create_many
        trail_repo.create_many = AsyncMock(side_effect=DBAPIError("fake", {}, Exception("db down")))

        event = _make_event(
            EventKind.step_completed,
            payload={
                "step_id": "s1",
                "turn_id": "turn-1",
                "files_written": ["src/a.py"],
            },
        )
        await builder.handle_event(event)

        # The modify node should still be created
        nodes = await trail_repo.get_by_job("job-1")
        modify_nodes = [n for n in nodes if n.kind == "modify"]
        assert len(modify_nodes) == 1

        trail_repo.create_many = original_create_many


# ---------------------------------------------------------------------------
# _save_snapshot / _load_snapshot
# ---------------------------------------------------------------------------


class TestSnapshotPersistence:
    async def test_save_and_load_snapshot(self, builder, session_factory, job_state):
        """Snapshot is saved to JobRow and can be loaded back."""
        await _insert_job_row(session_factory, "job-1", prompt="Fix it")
        state = TrailJobState(
            active_goal_id="g1",
            next_seq=10,
            job_prompt="Fix it",
            current_phase="coding",
        )
        state.plan_steps = [PlanStep(plan_step_id="ps-1", label="Step 1", status="active", order=0)]
        job_state["job-1"] = state

        await builder._save_snapshot("job-1", state)
        loaded = await builder._load_snapshot("job-1")

        assert loaded is not None
        assert loaded.active_goal_id == "g1"
        assert loaded.next_seq == 10
        assert loaded.job_prompt == "Fix it"
        assert loaded.current_phase == "coding"
        assert len(loaded.plan_steps) == 1
        assert loaded.plan_steps[0].label == "Step 1"

    async def test_load_snapshot_returns_none_when_no_row(self, builder):
        result = await builder._load_snapshot("nonexistent")
        assert result is None

    async def test_load_snapshot_returns_none_on_corrupt_json(self, builder, session_factory):
        """Corrupt JSON in trail_state_snapshot returns None."""
        await _insert_job_row(
            session_factory,
            "job-1",
            trail_state_snapshot="not valid json {{{",
        )
        result = await builder._load_snapshot("job-1")
        assert result is None

    async def test_save_snapshot_error_swallowed(self, builder, job_state):
        """_save_snapshot catches exceptions silently."""
        state = TrailJobState()
        # Use a bogus job_id that doesn't exist — update will affect 0 rows but not crash
        await builder._save_snapshot("nonexistent-job", state)
        # Should not raise


# ---------------------------------------------------------------------------
# _on_session_resumed — snapshot-based and lossy recovery
# ---------------------------------------------------------------------------


class TestSessionResumed:
    async def test_session_resumed_already_tracked_is_noop(self, builder, job_state):
        """If job is already in _job_state, session_resumed does nothing."""
        job_state["job-1"] = TrailJobState(next_seq=42)
        event = _make_event(EventKind.session_resumed, payload={})
        await builder.handle_event(event)
        assert job_state["job-1"].next_seq == 42  # unchanged

    async def test_session_resumed_snapshot_recovery(self, builder, session_factory, job_state, trail_repo):
        """When a snapshot exists, state is restored from it."""
        state = TrailJobState(
            active_goal_id="g1",
            next_seq=5,
            job_prompt="Fix auth",
            current_phase="coding",
        )
        state.plan_steps = [
            PlanStep(plan_step_id="ps-1", label="Investigate", status="done", order=0),
        ]
        state.activities = [
            Activity(activity_id="act-1", label="Checking", status="done"),
        ]
        snapshot_json = json.dumps(state.to_snapshot())
        await _insert_job_row(
            session_factory,
            "job-1",
            prompt="Fix auth",
            trail_state_snapshot=snapshot_json,
        )

        event = _make_event(EventKind.session_resumed, payload={})
        await builder.handle_event(event)

        restored = job_state["job-1"]
        assert restored.active_goal_id == "g1"
        assert restored.job_prompt == "Fix auth"
        assert len(restored.plan_steps) == 1
        assert len(restored.activities) == 1

    async def test_session_resumed_snapshot_seq_correction(self, builder, session_factory, job_state, trail_repo):
        """Snapshot next_seq is corrected if persisted nodes have higher seq."""
        state = TrailJobState(active_goal_id="g1", next_seq=3)
        snapshot_json = json.dumps(state.to_snapshot())
        await _insert_job_row(session_factory, "job-1", trail_state_snapshot=snapshot_json)
        # Create a persisted node with seq=10
        node = TrailNodeRow(
            id="n1",
            job_id="job-1",
            seq=10,
            anchor_seq=10,
            kind="explore",
            deterministic_kind="explore",
            timestamp=datetime.now(UTC),
            enrichment="pending",
        )
        await trail_repo.create(node)

        event = _make_event(EventKind.session_resumed, payload={})
        await builder.handle_event(event)

        assert job_state["job-1"].next_seq == 11  # max_seq(10) + 1

    async def test_session_resumed_lossy_fallback(self, builder, session_factory, job_state, trail_repo):
        """Without snapshot, state is reconstructed from trail nodes."""
        await _insert_job_row(session_factory, "job-1", prompt="Fix it")

        # Create a goal node
        goal = TrailNodeRow(
            id="goal-1",
            job_id="job-1",
            seq=1,
            anchor_seq=1,
            kind="goal",
            deterministic_kind="goal",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            intent="Fix the login bug",
        )
        await trail_repo.create(goal)

        # Create a work node with activity info
        work = TrailNodeRow(
            id="work-1",
            job_id="job-1",
            seq=2,
            anchor_seq=2,
            kind="modify",
            deterministic_kind="modify",
            timestamp=datetime.now(UTC),
            enrichment="pending",
            turn_id="t1",
            title="Editing auth module",
            activity_id="act-1",
            activity_label="Authentication fix",
            plan_item_label="Fix auth",
        )
        await trail_repo.create(work)

        event = _make_event(EventKind.session_resumed, payload={})
        await builder.handle_event(event)

        restored = job_state["job-1"]
        assert restored.next_seq == 3  # max_seq(2) + 1
        assert restored.active_goal_id == "goal-1"
        assert restored.job_prompt == "Fix the login bug"
        # Activity should be reconstructed
        assert len(restored.activities) == 1
        assert restored.activities[0].activity_id == "act-1"
        assert len(restored.activity_steps) == 1
        assert restored.activity_steps[0].turn_id == "t1"

    async def test_session_resumed_lossy_no_goal(self, builder, session_factory, job_state, trail_repo):
        """Lossy fallback without a goal node still initializes state."""
        await _insert_job_row(session_factory, "job-1", prompt="test")
        event = _make_event(EventKind.session_resumed, payload={})
        await builder.handle_event(event)
        restored = job_state["job-1"]
        assert restored.next_seq == 1  # no nodes → max_seq = 0, next = 1
        assert restored.active_goal_id is None

    async def test_session_resumed_lossy_plan_from_events(self, builder, session_factory, job_state, trail_repo):
        """Lossy fallback reconstructs plan from PlanStepUpdated events."""
        await _insert_job_row(session_factory, "job-1")

        # Insert plan step events
        from backend.persistence.event_repo import EventRepository

        async with session_factory() as session:
            event_repo = EventRepository(session)
            for i, status in enumerate(["pending", "active"]):
                ev = new_event(
                    session_id="job-1",
                    timestamp=datetime.now(UTC),
                    kind=EventKind.plan_step_updated,
                    payload={
                        "plan_step_id": f"ps-{i}",
                        "label": f"Step {i}",
                        "status": status,
                        "order": i,
                    },
                )
                await event_repo.append(ev)
            await session.commit()

        event = _make_event(EventKind.session_resumed, payload={})
        await builder.handle_event(event)

        restored = job_state["job-1"]
        assert restored.plan_established is True
        assert len(restored.plan_steps) == 2
        assert restored.plan_steps[0].label == "Step 0"
        assert restored.plan_steps[1].label == "Step 1"
        assert restored.active_idx == 1  # the "active" step

    async def test_session_resumed_lossy_duplicate_activity_deduped(
        self, builder, session_factory, job_state, trail_repo
    ):
        """Multiple work nodes with the same activity_id don't create duplicates."""
        await _insert_job_row(session_factory, "job-1")

        for i in range(3):
            node = TrailNodeRow(
                id=f"w{i}",
                job_id="job-1",
                seq=i + 1,
                anchor_seq=i + 1,
                kind="modify",
                deterministic_kind="modify",
                timestamp=datetime.now(UTC),
                enrichment="pending",
                turn_id=f"t{i}",
                title=f"Edit {i}",
                activity_id="act-same",
                activity_label="Same activity",
            )
            await trail_repo.create(node)

        event = _make_event(EventKind.session_resumed, payload={})
        await builder.handle_event(event)

        restored = job_state["job-1"]
        assert len(restored.activities) == 1
        assert restored.activities[0].activity_id == "act-same"
        assert len(restored.activity_steps) == 3


# ---------------------------------------------------------------------------
# _on_job_terminal saves snapshot before cleanup
# ---------------------------------------------------------------------------


class TestJobTerminalSnapshot:
    async def test_terminal_saves_snapshot_then_cleans_up(self, builder, session_factory, job_state, trail_repo):
        """Terminal event saves snapshot before deleting from _job_state."""
        await _insert_job_row(session_factory, "job-1")
        state = TrailJobState(active_goal_id="g1", next_seq=5, job_prompt="Fix it")
        job_state["job-1"] = state

        event = _make_event(EventKind.job_completed, payload={})
        await builder.handle_event(event)

        # State should be cleaned up
        assert "job-1" not in job_state

        # But snapshot should be persisted
        async with session_factory() as session:
            from sqlalchemy import select as sa_select

            result = await session.execute(sa_select(JobRow.trail_state_snapshot).where(JobRow.id == "job-1"))
            raw = result.scalar_one_or_none()
            assert raw is not None
            data = json.loads(raw)
            assert data["job_prompt"] == "Fix it"


# ---------------------------------------------------------------------------
# _step_completed → _classify_and_emit → _save_snapshot integration
# ---------------------------------------------------------------------------


class TestStepCompletedIntegration:
    async def test_step_completed_saves_snapshot(self, builder, session_factory, job_state, trail_repo):
        """Every step_completed saves a snapshot of the current state."""
        await _insert_job_row(session_factory, "job-1")

        # Start the job
        await builder.handle_event(_started_event())

        # Complete a step
        event = _make_event(
            EventKind.step_completed,
            payload={"step_id": "s1", "files_read": ["a.py"]},
        )
        await builder.handle_event(event)
        await builder.flush_background_tasks()

        # Verify snapshot was saved
        async with session_factory() as session:
            from sqlalchemy import select as sa_select

            result = await session.execute(sa_select(JobRow.trail_state_snapshot).where(JobRow.id == "job-1"))
            raw = result.scalar_one_or_none()
            assert raw is not None
            data = json.loads(raw)
            assert data["next_seq"] >= 2
