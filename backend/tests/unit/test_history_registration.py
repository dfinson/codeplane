from unittest.mock import MagicMock

import pytest

from backend.config import CPLConfig
from backend.models.api_schemas import JobResponse
from backend.models.domain import StateConflictError
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.event_bus import EventBus
from backend.services.ingest.history_registration import HistoryRegistration
from backend.services.runtime import RuntimeService
from backend.tests.unit.test_approval_service import session_factory as session_factory


async def test_history_registration_is_idempotent_workspace_free_and_non_executable(session_factory) -> None:
    async with session_factory() as session:
        service = HistoryRegistration(
            IntegrationRepository(session),
            JobRepository(session),
            ProjectRepository(session),
        )
        job = await service.register(
            binding_id="binding",
            project_id="proj-1",
            format_id="copilot",
            external_session_id="external",
        )
        await session.commit()
        repeated = await service.register(
            binding_id="binding",
            project_id="proj-1",
            format_id="copilot",
            external_session_id="external",
        )
        assert repeated.id == job.id and repeated.id != "external"
        assert job.repo is None and job.base_ref is None and job.worktree_path is None
        assert job.observation_only and job.source == "integration"
        response = JobResponse.from_domain(job).model_dump(by_alias=True)
        assert response["repo"] is None and response["baseRef"] is None
        assert response["observationOnly"] is True and response["integrationBindingId"] == "binding"
        assert job.id not in await JobRepository(session).list_artifact_collection_candidates()
    sidecars = MagicMock()
    runtime = RuntimeService(
        session_factory=session_factory,
        event_bus=EventBus(),
        adapter_registry=MagicMock(),
        config=CPLConfig(),
        sidecar_sessions=sidecars,
    )
    with pytest.raises(StateConflictError):
        await runtime.start_or_enqueue(job)
    sidecars.open.assert_not_called()
    assert not runtime._tasks and not runtime._heartbeat_tasks and not runtime._external_generations
    with pytest.raises(StateConflictError):
        await runtime.register_external_session(job.id, "/invented", "main")
    with pytest.raises(StateConflictError):
        await runtime.send_operator_message(job.id, "Do not execute")
    assert (await runtime.get_current_execution_context(job.id)).execution_token is None


async def test_binding_retry_cannot_retarget_another_source(session_factory) -> None:
    async with session_factory() as session:
        service = HistoryRegistration(
            IntegrationRepository(session),
            JobRepository(session),
            ProjectRepository(session),
        )
        await service.register(
            binding_id="binding", project_id="proj-1", format_id="claude", external_session_id="first"
        )
        await session.commit()
        with pytest.raises(StateConflictError):
            await service.register(
                binding_id="binding",
                project_id="proj-1",
                format_id="claude",
                external_session_id="different",
            )
