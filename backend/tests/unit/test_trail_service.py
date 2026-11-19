"""Tests for the agent audit trail service — deterministic skeleton + enrichment."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.models.db import Base, JobTelemetrySpanRow, TrailNodeRow
from backend.models.events import EventKind, SessionEvent, new_event
from backend.models.session_event import EventMetadata, ToolMotivation
from backend.persistence.trail_repo import TrailNodeRepository
from backend.services.events.event_bus import EventBus
from backend.services.trail import TrailService
from backend.services.trail.node_builder import (
    _extract_snippet,
)
from backend.services.trail.node_builder import (
    classify_step as _classify_step,
)
from backend.services.trail.prompts import parse_enrichment_response as _parse_enrichment_response

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
def trail_service(session_factory):
    event_bus = EventBus()
    return TrailService(session_factory=session_factory, event_bus=event_bus)


@pytest.fixture
def trail_repo(session_factory):
    return TrailNodeRepository(session_factory)


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


def _job_started_event(job_id: str = "job-1") -> SessionEvent:
    """Create a job_state_changed event that triggers goal node creation."""
    return _make_event(
        EventKind.job_state_changed,
        job_id=job_id,
        payload={"previous_state": "queued", "new_state": "running"},
    )


# ---------------------------------------------------------------------------
# classify_step unit tests
# ---------------------------------------------------------------------------


class TestClassifyStep:
    def test_files_written_returns_modify(self):
        assert _classify_step({"files_written": ["a.py"]}) == "modify"

    def test_sha_divergence_returns_modify(self):
        assert _classify_step({"start_sha": "aaa", "end_sha": "bbb"}) == "modify"

    def test_files_read_returns_explore(self):
        assert _classify_step({"files_read": ["a.py"]}) == "explore"

    def test_empty_returns_shell(self):
        assert _classify_step({}) == "shell"

    def test_same_sha_with_reads_returns_explore(self):
        assert _classify_step({"files_read": ["a.py"], "start_sha": "aaa", "end_sha": "aaa"}) == "explore"

    def test_writes_take_precedence_over_reads(self):
        assert _classify_step({"files_read": ["a.py"], "files_written": ["b.py"]}) == "modify"


# ---------------------------------------------------------------------------
# parse_enrichment_response tests
# ---------------------------------------------------------------------------


class TestParseEnrichmentResponse:
    def test_valid_json(self):
        data = _parse_enrichment_response('{"annotations": [], "semantic_nodes": []}')
        assert data == {"annotations": [], "semantic_nodes": []}

    def test_fenced_json(self):
        data = _parse_enrichment_response('```json\n{"annotations": []}\n```')
        assert data == {"annotations": []}

    def test_invalid_json_returns_none(self):
        assert _parse_enrichment_response("this is not json") is None

    def test_empty_returns_none(self):
        assert _parse_enrichment_response("") is None


# ---------------------------------------------------------------------------
# TrailService event handling (deterministic skeleton)
# ---------------------------------------------------------------------------


class TestTrailServiceGoalNode:
    async def test_job_started_creates_goal_node(self, trail_service, trail_repo):
        event = _job_started_event()
        await trail_service.handle_event(event)

        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 1
        assert nodes[0].kind == "goal"
        assert nodes[0].deterministic_kind == "goal"
        assert nodes[0].enrichment == "complete"
        assert nodes[0].seq == 1
        assert nodes[0].anchor_seq == 1
        assert nodes[0].parent_id is None

    async def test_goal_node_intent_empty_without_job_row(self, trail_service, trail_repo):
        """When the job row doesn't exist, intent falls back to empty."""
        event = _job_started_event()
        await trail_service.handle_event(event)

        nodes = await trail_repo.get_by_job("job-1")
        # Intent is None or empty since we can't fetch the job row in test DB
        assert nodes[0].intent is None or nodes[0].intent == ""


class TestTrailServiceStepNodes:
    async def test_step_completed_modify(self, trail_service, trail_repo):
        # First create the job
        await trail_service.handle_event(_job_started_event())

        event = _make_event(
            EventKind.step_completed,
            payload={
                "step_id": "step-1",
                "files_written": ["auth.py", "config.py"],
                "files_read": ["utils.py"],
                "start_sha": "aaa",
                "end_sha": "bbb",
            },
        )
        await trail_service.handle_event(event)

        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 2  # goal + modify
        step_node = nodes[1]
        assert step_node.kind == "modify"
        assert step_node.deterministic_kind == "modify"
        assert step_node.enrichment == "pending"
        assert step_node.seq == 2
        assert step_node.anchor_seq == 2
        assert step_node.parent_id == nodes[0].id  # parent is goal
        files = json.loads(step_node.files)
        assert "auth.py" in files
        assert "config.py" in files

    async def test_step_completed_explore(self, trail_service, trail_repo):
        await trail_service.handle_event(_job_started_event())

        event = _make_event(
            EventKind.step_completed,
            payload={"step_id": "step-1", "files_read": ["main.py"]},
        )
        await trail_service.handle_event(event)

        nodes = await trail_repo.get_by_job("job-1")
        assert nodes[1].kind == "explore"

    async def test_step_completed_shell(self, trail_service, trail_repo):
        await trail_service.handle_event(_job_started_event())

        event = _make_event(
            EventKind.step_completed,
            payload={"step_id": "step-1"},
        )
        await trail_service.handle_event(event)

        nodes = await trail_repo.get_by_job("job-1")
        assert nodes[1].kind == "shell"

    async def test_step_without_job_start_is_skipped(self, trail_service, trail_repo):
        """Steps for unknown jobs (started before trail service) are silently skipped."""
        event = _make_event(
            EventKind.step_completed,
            job_id="unknown-job",
            payload={"step_id": "step-1"},
        )
        await trail_service.handle_event(event)

        nodes = await trail_repo.get_by_job("unknown-job")
        assert len(nodes) == 0


class TestTrailServicePhaseNodes:
    async def test_phase_changed_creates_summarize(self, trail_service, trail_repo):
        await trail_service.handle_event(_job_started_event())

        event = _make_event(
            EventKind.execution_phase_changed,
            payload={"phase": "agent_reasoning"},
        )
        await trail_service.handle_event(event)

        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 2
        assert nodes[1].kind == "summarize"
        assert nodes[1].enrichment == "complete"
        assert nodes[1].intent == "Phase: agent_reasoning"
        assert nodes[1].anchor_seq == nodes[1].seq  # self-anchored


class TestTrailServiceTerminalNodes:
    async def test_job_completed_creates_terminal_summarize(self, trail_service, trail_repo):
        await trail_service.handle_event(_job_started_event())

        event = _make_event(EventKind.job_completed, payload={})
        await trail_service.handle_event(event)

        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 2
        terminal = nodes[1]
        assert terminal.kind == "summarize"
        assert terminal.phase == "terminal"
        assert terminal.intent == "Job completed"

    async def test_job_failed_creates_terminal_summarize(self, trail_service, trail_repo):
        await trail_service.handle_event(_job_started_event())

        event = _make_event(EventKind.job_failed, payload={"reason": "timeout"})
        await trail_service.handle_event(event)

        nodes = await trail_repo.get_by_job("job-1")
        terminal = nodes[1]
        assert terminal.intent == "Job failed"

    async def test_job_terminal_cleans_up_state(self, trail_service):
        await trail_service.handle_event(_job_started_event())
        assert "job-1" in trail_service._job_state

        await trail_service.handle_event(_make_event(EventKind.job_completed))
        assert "job-1" not in trail_service._job_state


class TestTrailServiceApprovalNodes:
    async def test_approval_deferred_until_step_completes(self, trail_service, trail_repo):
        """Approval requested while step is active gets deferred."""
        await trail_service.handle_event(_job_started_event())
        # Start a step
        await trail_service.handle_event(
            _make_event(
                EventKind.step_started,
                payload={"step_id": "step-1", "step_number": 1},
            )
        )
        # Approval arrives mid-step
        await trail_service.handle_event(
            _make_event(
                EventKind.approval_requested,
                payload={"description": "Need approval for deploy"},
            )
        )

        # Only goal node so far (approval is deferred)
        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 1

        # Step completes — should flush the deferred approval
        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={"step_id": "step-1", "files_read": ["a.py"]},
            )
        )

        nodes = await trail_repo.get_by_job("job-1")
        # goal + step + request
        assert len(nodes) == 3
        request_node = [n for n in nodes if n.kind == "request"][0]
        assert request_node.intent == "Need approval for deploy"
        assert request_node.enrichment == "complete"


class TestTrailServiceSequencing:
    async def test_seq_is_monotonic(self, trail_service, trail_repo):
        await trail_service.handle_event(_job_started_event())
        for i in range(5):
            await trail_service.handle_event(
                _make_event(
                    EventKind.step_completed,
                    payload={"step_id": f"step-{i}", "files_read": [f"f{i}.py"]},
                )
            )

        nodes = await trail_repo.get_by_job("job-1")
        seqs = [n.seq for n in nodes]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # all unique


# ---------------------------------------------------------------------------
# TrailNodeRepository tests
# ---------------------------------------------------------------------------


class TestTrailNodeRepository:
    async def test_create_and_get(self, trail_repo):
        node = TrailNodeRow(
            id="node-1",
            job_id="job-1",
            seq=1,
            anchor_seq=1,
            kind="goal",
            deterministic_kind="goal",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            intent="Fix a bug",
        )
        await trail_repo.create(node)

        fetched = await trail_repo.get("node-1")
        assert fetched is not None
        assert fetched.kind == "goal"
        assert fetched.intent == "Fix a bug"

    async def test_get_by_job_display_order(self, trail_repo):
        """Nodes should be returned in (anchor_seq, seq) order."""
        base = datetime.now(UTC)
        # Create nodes with different anchor_seqs to test display ordering
        for seq, anchor in [(1, 1), (3, 1), (2, 2)]:
            await trail_repo.create(
                TrailNodeRow(
                    id=f"node-{seq}",
                    job_id="job-1",
                    seq=seq,
                    anchor_seq=anchor,
                    kind="explore",
                    deterministic_kind="explore",
                    timestamp=base,
                    enrichment="pending",
                )
            )

        nodes = await trail_repo.get_by_job("job-1")
        seqs = [(n.anchor_seq, n.seq) for n in nodes]
        assert seqs == sorted(seqs)

    async def test_kind_filtering(self, trail_repo):
        base = datetime.now(UTC)
        for i, kind in enumerate(["goal", "explore", "modify", "explore"]):
            await trail_repo.create(
                TrailNodeRow(
                    id=f"node-{i}",
                    job_id="job-1",
                    seq=i + 1,
                    anchor_seq=i + 1,
                    kind=kind,
                    timestamp=base,
                    enrichment="complete",
                )
            )

        nodes = await trail_repo.get_by_job("job-1", kinds=["explore"])
        assert len(nodes) == 2
        assert all(n.kind == "explore" for n in nodes)

    async def test_after_seq_pagination(self, trail_repo):
        base = datetime.now(UTC)
        for i in range(5):
            await trail_repo.create(
                TrailNodeRow(
                    id=f"node-{i}",
                    job_id="job-1",
                    seq=i + 1,
                    anchor_seq=i + 1,
                    kind="explore",
                    timestamp=base,
                    enrichment="pending",
                )
            )

        nodes = await trail_repo.get_by_job("job-1", after_seq=3)
        assert len(nodes) == 2
        assert nodes[0].seq == 4
        assert nodes[1].seq == 5

    async def test_pending_enrichment(self, trail_repo):
        base = datetime.now(UTC)
        for i, status in enumerate(["pending", "complete", "failed", "pending"]):
            await trail_repo.create(
                TrailNodeRow(
                    id=f"node-{i}",
                    job_id="job-1",
                    seq=i + 1,
                    anchor_seq=i + 1,
                    kind="explore",
                    timestamp=base,
                    enrichment=status,
                )
            )

        pending = await trail_repo.get_pending_enrichment(limit=100)
        assert len(pending) == 2
        assert all(node.enrichment == "pending" for node in pending)

    async def test_update_enrichment(self, trail_repo):
        base = datetime.now(UTC)
        await trail_repo.create(
            TrailNodeRow(
                id="node-1",
                job_id="job-1",
                seq=1,
                anchor_seq=1,
                kind="shell",
                deterministic_kind="shell",
                timestamp=base,
                enrichment="pending",
            )
        )

        await trail_repo.update_enrichment(
            "node-1",
            kind="verify",
            intent="Ran test suite",
            rationale="Checking auth fixes work",
            outcome="All tests pass",
            tags=["testing"],
        )

        node = await trail_repo.get("node-1")
        assert node.kind == "verify"
        assert node.enrichment == "complete"
        assert node.intent == "Ran test suite"
        assert json.loads(node.tags) == ["testing"]

    async def test_max_seq(self, trail_repo):
        base = datetime.now(UTC)
        for i in range(3):
            await trail_repo.create(
                TrailNodeRow(
                    id=f"node-{i}",
                    job_id="job-1",
                    seq=i + 1,
                    anchor_seq=i + 1,
                    kind="explore",
                    timestamp=base,
                    enrichment="pending",
                )
            )

        assert await trail_repo.max_seq("job-1") == 3
        assert await trail_repo.max_seq("nonexistent") == 0

    async def test_count_by_job(self, trail_repo):
        base = datetime.now(UTC)
        for i, status in enumerate(["complete", "pending", "complete"]):
            await trail_repo.create(
                TrailNodeRow(
                    id=f"node-{i}",
                    job_id="job-1",
                    seq=i + 1,
                    anchor_seq=i + 1,
                    kind="explore",
                    timestamp=base,
                    enrichment=status,
                )
            )

        total, enriched = await trail_repo.count_by_job("job-1")
        assert total == 3
        assert enriched == 2


# ---------------------------------------------------------------------------
# TrailService query helpers
# ---------------------------------------------------------------------------


class TestTrailServiceQueries:
    async def test_get_trail_flat(self, trail_service, trail_repo):
        await trail_service.handle_event(_job_started_event())
        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={"step_id": "step-1", "files_read": ["a.py"]},
            )
        )

        result = await trail_service.get_trail("job-1", flat=True)
        assert result["job_id"] == "job-1"
        assert result["total_nodes"] == 2
        assert len(result["nodes"]) == 2
        # Flat mode: no children nesting
        assert all(n.get("children", []) == [] for n in result["nodes"])

    async def test_get_trail_nested(self, trail_service, trail_repo):
        await trail_service.handle_event(_job_started_event())
        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={"step_id": "step-1", "files_read": ["a.py"]},
            )
        )

        result = await trail_service.get_trail("job-1", flat=False)
        assert len(result["nodes"]) == 1  # Only goal at root
        assert result["nodes"][0]["kind"] == "goal"
        assert len(result["nodes"][0]["children"]) == 1  # explore is a child

    async def test_get_summary(self, trail_service, trail_repo):
        await trail_service.handle_event(_job_started_event())
        for i in range(3):
            await trail_service.handle_event(
                _make_event(
                    EventKind.step_completed,
                    payload={"step_id": f"step-{i}", "files_read": [f"f{i}.py"]},
                )
            )
        await trail_service.handle_event(_make_event(EventKind.job_completed))

        summary = await trail_service.get_summary("job-1")
        assert summary["job_id"] == "job-1"
        assert summary["files_explored"] == 3  # 3 unique files
        assert isinstance(summary["goals"], list)


# ---------------------------------------------------------------------------
# Full lifecycle integration test
# ---------------------------------------------------------------------------


class TestTrailFullLifecycle:
    async def test_complete_job_lifecycle(self, trail_service, trail_repo):
        """Simulate a full job: start → phase → steps → complete."""
        job_id = "job-lifecycle"

        # Job starts
        await trail_service.handle_event(_job_started_event(job_id=job_id))

        # Phase change
        await trail_service.handle_event(
            _make_event(
                EventKind.execution_phase_changed,
                job_id=job_id,
                payload={"phase": "agent_reasoning"},
            )
        )

        # Step 1: explore
        await trail_service.handle_event(
            _make_event(
                EventKind.step_started,
                job_id=job_id,
                payload={"step_id": "step-1", "step_number": 1},
            )
        )
        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                job_id=job_id,
                payload={"step_id": "step-1", "files_read": ["auth.py", "config.py"]},
            )
        )

        # Step 2: modify
        await trail_service.handle_event(
            _make_event(
                EventKind.step_started,
                job_id=job_id,
                payload={"step_id": "step-2", "step_number": 2},
            )
        )
        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                job_id=job_id,
                payload={
                    "step_id": "step-2",
                    "files_written": ["auth.py"],
                    "start_sha": "aaa",
                    "end_sha": "bbb",
                },
            )
        )

        # Step 3: shell (no file tools)
        await trail_service.handle_event(
            _make_event(
                EventKind.step_started,
                job_id=job_id,
                payload={"step_id": "step-3", "step_number": 3},
            )
        )
        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                job_id=job_id,
                payload={"step_id": "step-3"},
            )
        )

        # Job completes
        await trail_service.handle_event(
            _make_event(
                EventKind.job_completed,
                job_id=job_id,
            )
        )

        # Verify the full trail
        nodes = await trail_repo.get_by_job(job_id)
        kinds = [n.kind for n in nodes]
        assert kinds == ["goal", "summarize", "explore", "modify", "shell", "summarize"]

        # Verify enrichment states
        assert nodes[0].enrichment == "complete"  # goal
        assert nodes[1].enrichment == "complete"  # summarize (phase)
        assert nodes[2].enrichment == "pending"  # explore (needs enrichment)
        assert nodes[3].enrichment == "pending"  # modify (needs enrichment)
        assert nodes[4].enrichment == "pending"  # shell (needs enrichment)
        assert nodes[5].enrichment == "complete"  # summarize (terminal)

        # Verify tree structure
        goal_id = nodes[0].id
        for n in nodes[1:]:
            assert n.parent_id == goal_id

        # Verify state is cleaned up
        assert job_id not in trail_service._job_state

        # Verify counts
        total, enriched = await trail_repo.count_by_job(job_id)
        assert total == 6
        assert enriched == 3  # goal + 2 summarize


# ---------------------------------------------------------------------------
# _extract_snippet unit tests
# ---------------------------------------------------------------------------


class TestExtractSnippet:
    def test_replacement_old_new(self):
        args = json.dumps({"old_str": "foo()", "new_str": "bar()"})
        result = _extract_snippet(args, "edit_file")
        assert "- foo()" in result
        assert "+ bar()" in result

    def test_create_file_text(self):
        args = json.dumps({"file_text": "import os\nprint('hi')\n"})
        result = _extract_snippet(args, "create_file")
        assert "+ import os" in result
        assert "+ print('hi')" in result

    def test_insert_new_text(self):
        args = json.dumps({"new_text": "added line"})
        result = _extract_snippet(args, "insert_text")
        assert "+ added line" in result

    def test_empty_args(self):
        assert _extract_snippet(None, "write_file") == ""
        assert _extract_snippet("", "write_file") == ""

    def test_invalid_json(self):
        assert _extract_snippet("not json", "write_file") == ""

    def test_no_recognized_keys(self):
        args = json.dumps({"unrelated": "data"})
        assert _extract_snippet(args, "write_file") == ""

    def test_camel_case_keys(self):
        args = json.dumps({"oldString": "a", "newString": "b"})
        result = _extract_snippet(args, "edit")
        assert "- a" in result
        assert "+ b" in result


# ---------------------------------------------------------------------------
# Write sub-node creation (§13.1)
# ---------------------------------------------------------------------------


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
    """Insert a file_write telemetry span directly into the DB for testing."""
    datetime.now(UTC).isoformat()
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


class TestWriteSubNodes:
    async def test_modify_step_creates_write_sub_nodes(
        self,
        trail_service,
        trail_repo,
        session_factory,
    ):
        """A modify step with file_write spans should produce write sub-nodes."""
        await trail_service.handle_event(_job_started_event())

        # Insert file_write spans BEFORE step_completed fires
        await _insert_file_write_span(
            session_factory,
            job_id="job-1",
            turn_id="turn-1",
            name="write_file",
            tool_target="src/a.py",
            tool_args_json=json.dumps({"file_text": "new content"}),
            motivation_summary="Adding new module",
        )
        await _insert_file_write_span(
            session_factory,
            job_id="job-1",
            turn_id="turn-1",
            name="edit_file",
            tool_target="src/b.py",
            tool_args_json=json.dumps({"old_str": "old", "new_str": "new"}),
            is_retry=True,
            error_kind="syntax_error",
        )

        # Fire step_completed with files_written
        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={
                    "step_id": "step-1",
                    "turn_id": "turn-1",
                    "files_written": ["src/a.py", "src/b.py"],
                    "start_sha": "aaa",
                    "end_sha": "bbb",
                },
            )
        )

        nodes = await trail_repo.get_by_job("job-1")
        # goal + modify + 2 write sub-nodes
        assert len(nodes) == 4

        modify_node = [n for n in nodes if n.kind == "modify"][0]
        write_nodes = [n for n in nodes if n.kind == "write"]
        assert len(write_nodes) == 2

        # Write nodes are children of modify
        for wn in write_nodes:
            assert wn.parent_id == modify_node.id
            assert wn.deterministic_kind == "write"
            assert wn.enrichment == "complete"
            assert wn.step_id == "step-1"
            assert wn.turn_id == "turn-1"

        # Check per-file data
        w1 = next(wn for wn in write_nodes if json.loads(wn.files) == ["src/a.py"])
        assert w1.tool_name == "write_file"
        assert w1.snippet is not None
        assert "+ new content" in w1.snippet
        assert w1.write_summary == "Adding new module"

        w2 = next(wn for wn in write_nodes if json.loads(wn.files) == ["src/b.py"])
        assert w2.tool_name == "edit_file"
        assert w2.is_retry is True
        assert w2.error_kind == "syntax_error"

    async def test_explore_step_no_write_nodes(
        self,
        trail_service,
        trail_repo,
        session_factory,
    ):
        """Explore steps should not produce write sub-nodes."""
        await trail_service.handle_event(_job_started_event())

        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={
                    "step_id": "step-1",
                    "turn_id": "turn-1",
                    "files_read": ["src/a.py"],
                },
            )
        )

        nodes = await trail_repo.get_by_job("job-1")
        write_nodes = [n for n in nodes if n.kind == "write"]
        assert len(write_nodes) == 0

    async def test_modify_step_no_spans_no_write_nodes(
        self,
        trail_service,
        trail_repo,
        session_factory,
    ):
        """A modify step with no file_write spans produces no write sub-nodes."""
        await trail_service.handle_event(_job_started_event())

        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={
                    "step_id": "step-1",
                    "turn_id": "turn-1",
                    "files_written": ["src/a.py"],
                    "start_sha": "aaa",
                    "end_sha": "bbb",
                },
            )
        )

        nodes = await trail_repo.get_by_job("job-1")
        # goal + modify, no write sub-nodes
        assert len(nodes) == 2
        assert nodes[1].kind == "modify"

    async def test_write_nodes_anchor_seq_matches_parent(
        self,
        trail_service,
        trail_repo,
        session_factory,
    ):
        """Write sub-nodes share anchor_seq with their parent modify node."""
        await trail_service.handle_event(_job_started_event())

        await _insert_file_write_span(
            session_factory,
            job_id="job-1",
            turn_id="turn-1",
            tool_target="src/a.py",
        )

        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={
                    "step_id": "step-1",
                    "turn_id": "turn-1",
                    "files_written": ["src/a.py"],
                },
            )
        )

        nodes = await trail_repo.get_by_job("job-1")
        modify_node = [n for n in nodes if n.kind == "modify"][0]
        write_nodes = [n for n in nodes if n.kind == "write"]

        assert len(write_nodes) == 1
        assert write_nodes[0].anchor_seq == modify_node.anchor_seq

    async def test_modify_without_turn_id_no_write_nodes(
        self,
        trail_service,
        trail_repo,
        session_factory,
    ):
        """A modify step without turn_id skips write sub-node creation."""
        await trail_service.handle_event(_job_started_event())

        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={
                    "step_id": "step-1",
                    # no turn_id
                    "files_written": ["src/a.py"],
                    "start_sha": "aaa",
                    "end_sha": "bbb",
                },
            )
        )

        nodes = await trail_repo.get_by_job("job-1")
        assert len(nodes) == 2  # goal + modify only
        write_nodes = [n for n in nodes if n.kind == "write"]
        assert len(write_nodes) == 0

    async def test_seq_is_monotonic_with_write_nodes(
        self,
        trail_service,
        trail_repo,
        session_factory,
    ):
        """Sequence numbers remain monotonic when write sub-nodes are interleaved."""
        await trail_service.handle_event(_job_started_event())

        await _insert_file_write_span(
            session_factory,
            job_id="job-1",
            turn_id="turn-1",
            tool_target="src/a.py",
        )
        await _insert_file_write_span(
            session_factory,
            job_id="job-1",
            turn_id="turn-1",
            tool_target="src/b.py",
        )

        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={
                    "step_id": "step-1",
                    "turn_id": "turn-1",
                    "files_written": ["src/a.py", "src/b.py"],
                },
            )
        )

        # Another step
        await trail_service.handle_event(
            _make_event(
                EventKind.step_completed,
                payload={
                    "step_id": "step-2",
                    "turn_id": "turn-2",
                    "files_read": ["src/c.py"],
                },
            )
        )

        nodes = await trail_repo.get_by_job("job-1")
        seqs = [n.seq for n in nodes]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # all unique


# ---------------------------------------------------------------------------
# §13.5: TrailJobState snapshot roundtrip
# ---------------------------------------------------------------------------


class TestTrailJobStateSnapshot:
    """Test serialization/deserialization of TrailJobState."""

    def test_roundtrip_empty(self):
        from backend.services.trail.models import TrailJobState

        state = TrailJobState()
        data = state.to_snapshot()
        restored = TrailJobState.from_snapshot(data)
        assert restored.next_seq == 1
        assert restored.plan_steps == []
        assert restored.activities == []

    def test_roundtrip_with_plan_and_activities(self):
        from backend.services.trail.models import (
            Activity,
            ActivityStep,
            PlanStep,
            TrailJobState,
        )

        state = TrailJobState()
        state.next_seq = 42
        state.active_goal_id = "g1"
        state.current_phase = "coding"
        state.job_prompt = "Fix the bug"
        state.recent_messages = ["[operator] focus on backend"]
        state.tool_call_count = 7
        state.plan_established = True
        state.plan_steps = [
            PlanStep(plan_step_id="ps-1", label="Investigate", status="completed", order=0),
            PlanStep(plan_step_id="ps-2", label="Fix", status="active", order=1),
        ]
        state.active_idx = 1
        state.activities = [
            Activity(activity_id="act-1", label="Investigating", status="done"),
            Activity(activity_id="act-2", label="Fixing", status="active"),
        ]
        state.activity_steps = [
            ActivityStep(turn_id="t1", title="Read code", activity_id="act-1"),
            ActivityStep(turn_id="t2", title="Edit file", activity_id="act-2"),
        ]
        state.sidecar_consecutive_failures = 2

        data = state.to_snapshot()
        restored = TrailJobState.from_snapshot(data)

        assert restored.next_seq == 42
        assert restored.active_goal_id == "g1"
        assert restored.current_phase == "coding"
        assert restored.job_prompt == "Fix the bug"
        assert restored.recent_messages == ["[operator] focus on backend"]
        assert restored.tool_call_count == 7
        assert restored.plan_established is True
        assert len(restored.plan_steps) == 2
        assert restored.plan_steps[1].label == "Fix"
        assert restored.active_idx == 1
        assert len(restored.activities) == 2
        assert restored.activities[1].label == "Fixing"
        assert len(restored.activity_steps) == 2
        assert restored.sidecar_consecutive_failures == 2


# ---------------------------------------------------------------------------
# §13.7: Activity boundary signals
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# §13.2: Trail repo write node queries
# ---------------------------------------------------------------------------


class TestTrailRepoWriteNodeQueries:
    @pytest.mark.asyncio
    async def test_unsummarized_write_nodes(self, session_factory, trail_repo):
        """Write nodes with no write_summary are returned."""
        node = TrailNodeRow(
            id="w1",
            job_id="j1",
            seq=1,
            anchor_seq=1,
            kind="write",
            deterministic_kind="write",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            parent_id="p1",
        )
        await trail_repo.create(node)
        result = await trail_repo.get_unsummarized_write_nodes(limit=10)
        assert len(result) == 1
        assert result[0].id == "w1"

    @pytest.mark.asyncio
    async def test_unsummarized_write_nodes_excludes_summarized(self, session_factory, trail_repo):
        """Write nodes with write_summary are excluded."""
        node = TrailNodeRow(
            id="w2",
            job_id="j1",
            seq=1,
            anchor_seq=1,
            kind="write",
            deterministic_kind="write",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            parent_id="p1",
            write_summary="Fixed bug",
        )
        await trail_repo.create(node)
        result = await trail_repo.get_unsummarized_write_nodes(limit=10)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_unenriched_edit_write_nodes(self, session_factory, trail_repo):
        """Write nodes with summary but no edit_motivations are returned."""
        node = TrailNodeRow(
            id="w3",
            job_id="j1",
            seq=1,
            anchor_seq=1,
            kind="write",
            deterministic_kind="write",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            parent_id="p1",
            write_summary="Fixed bug",
        )
        await trail_repo.create(node)
        result = await trail_repo.get_unenriched_edit_write_nodes(limit=10)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_set_write_summary(self, session_factory, trail_repo):
        """set_write_summary persists correctly."""
        node = TrailNodeRow(
            id="w4",
            job_id="j1",
            seq=1,
            anchor_seq=1,
            kind="write",
            deterministic_kind="write",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            parent_id="p1",
        )
        await trail_repo.create(node)
        await trail_repo.set_write_summary("w4", "Added feature")
        updated = await trail_repo.get("w4")
        assert updated is not None
        assert updated.write_summary == "Added feature"

    @pytest.mark.asyncio
    async def test_set_edit_motivations(self, session_factory, trail_repo):
        """set_edit_motivations persists JSON correctly."""
        node = TrailNodeRow(
            id="w5",
            job_id="j1",
            seq=1,
            anchor_seq=1,
            kind="write",
            deterministic_kind="write",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            parent_id="p1",
            write_summary="Fix",
        )
        await trail_repo.create(node)
        await trail_repo.set_edit_motivations("w5", '[{"edit_key":"k1","summary":"why"}]')
        updated = await trail_repo.get("w5")
        assert updated is not None
        edits = json.loads(updated.edit_motivations)
        assert len(edits) == 1
        assert edits[0]["edit_key"] == "k1"


# ---------------------------------------------------------------------------
# §13.3: update_tool_metadata + get_snapshot_turns
# ---------------------------------------------------------------------------


class TestToolMetadata:
    """Tests for §13.3 per-tool metadata on trail nodes."""

    @pytest.mark.asyncio
    async def test_update_tool_metadata_matches_write_node(self, trail_repo):
        """update_tool_metadata populates tool_display/intent/success on matching write node."""
        node = TrailNodeRow(
            id="wt1",
            job_id="j1",
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

        updated = await trail_repo.update_tool_metadata(
            "j1",
            "turn-1",
            "str_replace_editor",
            tool_display="Edit config.py",
            tool_intent="Update the config setting",
            tool_success=True,
        )
        assert updated is True

        result = await trail_repo.get("wt1")
        assert result is not None
        assert result.tool_display == "Edit config.py"
        assert result.tool_intent == "Update the config setting"
        assert result.tool_success is True

    @pytest.mark.asyncio
    async def test_update_tool_metadata_no_match(self, trail_repo):
        """update_tool_metadata returns False when no matching write node exists."""
        updated = await trail_repo.update_tool_metadata(
            "j1",
            "turn-99",
            "nonexistent_tool",
            tool_display="Display",
        )
        assert updated is False

    @pytest.mark.asyncio
    async def test_update_tool_metadata_skips_already_populated(self, trail_repo):
        """update_tool_metadata only targets nodes with tool_display IS NULL."""
        node = TrailNodeRow(
            id="wt2",
            job_id="j1",
            seq=1,
            anchor_seq=1,
            kind="write",
            deterministic_kind="write",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            parent_id="p1",
            turn_id="turn-1",
            tool_name="write_file",
            tool_display="Already set",
        )
        await trail_repo.create(node)

        updated = await trail_repo.update_tool_metadata(
            "j1",
            "turn-1",
            "write_file",
            tool_display="New display",
        )
        assert updated is False

        result = await trail_repo.get("wt2")
        assert result is not None
        assert result.tool_display == "Already set"

    @pytest.mark.asyncio
    async def test_get_snapshot_turns_all_types(self, trail_repo):
        """get_snapshot_turns returns assistant, operator, and tool_call turns in order."""
        now = datetime.now(UTC)
        nodes = [
            TrailNodeRow(
                id="s1",
                job_id="j1",
                seq=1,
                anchor_seq=1,
                kind="modify",
                deterministic_kind="modify",
                timestamp=now,
                enrichment="complete",
                agent_message="I'll update the config file.",
            ),
            TrailNodeRow(
                id="s2",
                job_id="j1",
                seq=2,
                anchor_seq=2,
                kind="request",
                deterministic_kind="request",
                timestamp=now,
                enrichment="complete",
                agent_message="Please clarify the requirement.",
            ),
            TrailNodeRow(
                id="s3",
                job_id="j1",
                seq=3,
                anchor_seq=1,
                kind="write",
                deterministic_kind="write",
                timestamp=now,
                enrichment="complete",
                parent_id="s1",
                turn_id="turn-1",
                tool_name="edit_file",
                tool_display="Edit config.py line 42",
                tool_success=True,
            ),
            # Write node WITHOUT tool_display — should NOT appear
            TrailNodeRow(
                id="s4",
                job_id="j1",
                seq=4,
                anchor_seq=1,
                kind="write",
                deterministic_kind="write",
                timestamp=now,
                enrichment="complete",
                parent_id="s1",
                turn_id="turn-1",
                tool_name="read_file",
            ),
            # Step node without agent_message — should NOT appear
            TrailNodeRow(
                id="s5",
                job_id="j1",
                seq=5,
                anchor_seq=5,
                kind="explore",
                deterministic_kind="explore",
                timestamp=now,
                enrichment="complete",
            ),
        ]
        await trail_repo.create_many(nodes)

        turns = await trail_repo.get_snapshot_turns("j1")
        assert len(turns) == 3
        assert turns[0].id == "s1"  # assistant (modify with agent_message, anchor=1)
        assert turns[1].id == "s3"  # tool_call (write with tool_display, anchor=1)
        assert turns[2].id == "s2"  # operator (request, anchor=2)

    @pytest.mark.asyncio
    async def test_get_snapshot_turns_empty_job(self, trail_repo):
        """get_snapshot_turns returns empty list for non-existent job."""
        turns = await trail_repo.get_snapshot_turns("nonexistent")
        assert turns == []


class TestNodeBuilderToolCall:
    """Tests for NodeBuilder handling tool_call transcript events (§13.3)."""

    @pytest.mark.asyncio
    async def test_tool_call_updates_write_sub_node(self, session_factory, trail_service, trail_repo):
        """tool_call transcript event updates matching write sub-node with tool metadata."""
        # Set up: start a job and create a write sub-node
        await trail_service.handle_event(
            _make_event(
                EventKind.job_state_changed,
                payload={"new_state": "running"},
            )
        )

        # Create a write sub-node directly (simulating step_completed flow)
        write_node = TrailNodeRow(
            id="wn-tc1",
            job_id="job-1",
            seq=100,
            anchor_seq=100,
            kind="write",
            deterministic_kind="write",
            timestamp=datetime.now(UTC),
            enrichment="complete",
            parent_id="goal-1",
            turn_id="turn-42",
            tool_name="str_replace_editor",
        )
        await trail_repo.create(write_node)

        # Fire tool_call transcript event
        await trail_service.handle_event(
            _make_event(
                EventKind.tool_call_completed,
                payload={
                    "content": "str_replace_editor",
                    "tool_name": "str_replace_editor",
                    "turn_id": "turn-42",
                    "success": True,
                },
                metadata=EventMetadata(
                    tool_display="Edit app.py:10",
                    motivation=ToolMotivation(intent="Fix the import statement"),
                ),
            )
        )

        # Verify write sub-node was updated
        updated = await trail_repo.get("wn-tc1")
        assert updated is not None
        assert updated.tool_display == "Edit app.py:10"
        assert updated.tool_intent == "Fix the import statement"
        assert updated.tool_success is True

    @pytest.mark.asyncio
    async def test_tool_call_skips_report_intent(self, session_factory, trail_service, trail_repo):
        """tool_call with tool_name='report_intent' is silently skipped."""
        await trail_service.handle_event(
            _make_event(
                EventKind.job_state_changed,
                payload={"new_state": "running"},
            )
        )

        # Fire report_intent — should be ignored
        await trail_service.handle_event(
            _make_event(
                EventKind.tool_call_completed,
                payload={
                    "role": "tool_call",
                    "content": "report_intent",
                    "tool_name": "report_intent",
                    "turn_id": "turn-1",
                    "tool_display": None,
                },
            )
        )

        nodes = await trail_repo.get_by_job("job-1")
        # Only the goal node from job_state_changed, no extra nodes
        tool_nodes = [n for n in nodes if n.tool_display is not None]
        assert len(tool_nodes) == 0

    @pytest.mark.asyncio
    async def test_assistant_transcript_tracks_messages(self, session_factory, trail_service):
        """assistant transcript events update the recent_messages buffer."""
        await trail_service.handle_event(
            _make_event(
                EventKind.job_state_changed,
                payload={"new_state": "running"},
            )
        )

        await trail_service.handle_event(
            _make_event(
                EventKind.message_assistant,
                payload={
                    "role": "assistant",
                    "content": "I will now refactor the module.",
                },
            )
        )

        # Check that the builder's state was updated
        builder = trail_service._node_builder
        state = builder._job_state.get("job-1")
        assert state is not None
        assert any("[assistant]" in m for m in state.recent_messages)


class TestTerminalDrainContract:
    @pytest.mark.parametrize("terminal_kind", [EventKind.job_completed, EventKind.job_review])
    async def test_terminal_publication_drains_reentrant_plan_events(
        self, trail_service, trail_repo, session_factory, monkeypatch, terminal_kind
    ):
        import asyncio

        from backend.models.domain import Job, JobState
        from backend.persistence.job_repo import JobRepository
        from backend.persistence.project_repo import ProjectRepository
        from backend.services.trail.models import PlanStep

        now = datetime.now(UTC)
        async with session_factory() as session:
            await ProjectRepository(session).create("trail-project", "Trail fixture", [r"C:\fixture"])
            await JobRepository(session).create(
                Job(
                    id="job-1",
                    project_id="trail-project",
                    repo=r"C:\fixture",
                    prompt="Fixture plan",
                    state=JobState.running,
                    base_ref="main",
                    branch="work",
                    worktree_path=None,
                    session_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        bus = trail_service._event_bus
        received = []

        async def record(event):
            received.append(event.kind)

        bus.subscribe(record, required=True)
        bus.subscribe(trail_service.handle_event)
        await bus.publish(_job_started_event())
        state = trail_service._job_state["job-1"]
        state.plan_steps = [PlanStep("plan-1", "Fixture step")]
        state.active_idx = 0
        state.plan_established = True
        planner = trail_service._plan_manager
        original_classify = planner.classify_turn
        entered = asyncio.Event()
        release = asyncio.Event()

        async def classify_after_terminal(job_id, payload):
            entered.set()
            await release.wait()
            return await original_classify(job_id, payload)

        monkeypatch.setattr(planner, "classify_turn", classify_after_terminal)
        await bus.publish(_make_event(EventKind.step_completed, payload={"step_id": "step-1", "tool_count": 1}))
        await asyncio.wait_for(entered.wait(), timeout=5)
        builder = trail_service._node_builder
        terminal = asyncio.create_task(bus.publish(_make_event(terminal_kind)))
        completed = False
        try:
            lock = builder._job_locks["job-1"]
            for _ in range(100):
                if lock.locked():
                    break
                await asyncio.sleep(0)
            assert lock.locked(), "Terminal handler must own the lock before classification resumes"
            release.set()
            try:
                await asyncio.wait_for(asyncio.shield(terminal), timeout=5)
                completed = True
            except TimeoutError:
                pass
        finally:
            pending = [terminal, *builder._background_tasks]
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        assert completed, "Ignored plan events must not re-enter the terminal handler's held job lock"
        assert EventKind.plan_step_updated in received
        assert EventKind.progress_headline in received
        terminal_nodes = [node for node in await trail_repo.get_by_job("job-1") if node.phase == "terminal"]
        assert len(terminal_nodes) == 1
        assert "job-1" not in trail_service._job_state
        snapshot = await builder._load_snapshot("job-1")
        assert snapshot is not None
        assert snapshot.plan_steps[0].tool_count == 1

    @pytest.mark.parametrize(
        "kind", [EventKind.plan_step_updated, EventKind.progress_headline, EventKind.step_entries_reassigned]
    )
    async def test_ignored_kind_never_waits_for_or_changes_job_lock(self, trail_service, kind):
        import asyncio

        builder = trail_service._node_builder
        lock = asyncio.Lock()
        builder._job_locks["job-1"] = lock
        completed = False
        async with lock:
            try:
                await asyncio.wait_for(trail_service.handle_event(_make_event(kind)), timeout=1)
                completed = True
            except TimeoutError:
                pass
            assert lock.locked()
        assert completed, "An event with no trail handler must not wait on a per-job mutation lock"

    @pytest.mark.parametrize(
        ("kind", "intent"),
        [
            (EventKind.job_review, "Job ready for review"),
            (EventKind.job_completed, "Job completed"),
            (EventKind.job_failed, "Job failed"),
            (EventKind.job_canceled, "Job canceled"),
        ],
    )
    async def test_terminal_label_preserves_outcome(self, trail_service, trail_repo, kind, intent):
        await trail_service.handle_event(_job_started_event())
        await trail_service.handle_event(_make_event(kind))
        terminal_nodes = [node for node in await trail_repo.get_by_job("job-1") if node.phase == "terminal"]
        assert len(terminal_nodes) == 1
        assert terminal_nodes[0].intent == intent
