"""Tests for domain models and Pydantic API schemas."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.models.api_schemas import (
    CamelModel,
    CreateJobRequest,
    HealthResponse,
    HealthStatus,
    JobResponse,
    JobState,
)
from backend.models.domain import Job, MCPServerConfig, SessionConfig
from backend.models.events import DomainEvent, DomainEventKind


def test_camel_model_serializes_to_camel_case() -> None:
    class Example(CamelModel):
        some_field: str
        another_field_name: int

    obj = Example(some_field="hello", another_field_name=42)
    data = obj.model_dump(by_alias=True)
    assert "someField" in data
    assert "anotherFieldName" in data
    assert "some_field" not in data


def test_create_job_request_defaults() -> None:
    req = CreateJobRequest(repo="/repos/a", prompt="Fix bug")
    assert req.base_ref is None
    assert req.branch is None


def test_job_response_round_trip() -> None:
    now = datetime.now(UTC)
    resp = JobResponse(
        id="job-1",
        repo="/repos/a",
        prompt="Fix it",
        state="running",
        base_ref="main",
        worktree_path="/repos/a",
        branch="fix/it",
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    data = resp.model_dump(by_alias=True)
    assert data["id"] == "job-1"
    assert data["worktreePath"] == "/repos/a"
    assert data["completedAt"] is None


def test_health_response_serialization() -> None:
    resp = HealthResponse(
        status=HealthStatus.healthy,
        version="0.1.0",
        uptime_seconds=123.4,
        active_jobs=1,
        queued_jobs=0,
    )
    data = resp.model_dump(by_alias=True)
    assert data["uptimeSeconds"] == 123.4
    assert data["activeJobs"] == 1


def test_job_state_enum_values() -> None:
    assert JobState.running == "running"
    assert JobState.waiting_for_approval == "waiting_for_approval"
    assert len(JobState) == 6


def test_domain_event_creation() -> None:
    now = datetime.now(UTC)
    event = DomainEvent(
        event_id="evt-1",
        job_id="job-1",
        timestamp=now,
        kind=DomainEventKind.job_created,
        payload={"repo": "/repos/a"},
    )
    assert event.kind == DomainEventKind.job_created
    assert event.kind.value == "JobCreated"


def test_domain_event_kind_values() -> None:
    assert len(DomainEventKind) == 24


def test_job_domain_model() -> None:
    now = datetime.now(UTC)
    job = Job(
        id="job-1",
        repo="/repos/a",
        prompt="Fix it",
        state="running",
        base_ref="main",
        branch="fix/it",
        worktree_path="/repos/a",
        session_id=None,
        created_at=now,
        updated_at=now,
    )
    assert job.completed_at is None
    assert job.session_id is None


def test_session_config_defaults() -> None:
    config = SessionConfig(workspace_path="/repos/a", prompt="Fix it")
    assert config.mcp_servers == {}
    assert config.protected_paths == []


def test_mcp_server_config() -> None:
    cfg = MCPServerConfig(command="npx", args=["-y", "server"], env={"KEY": "val"})
    assert cfg.command == "npx"
    assert cfg.env == {"KEY": "val"}
