"""Canonical observation history registration, intentionally without live attachment."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from backend.models.domain import Job, JobNotFoundError, JobSource, JobState, StateConflictError
from backend.services.action_policy.credential_filter import filter_credentials
from backend.services.ingest.mapping import NATIVE_FORMATS
from backend.validators import validate_integration_binding_id

if TYPE_CHECKING:
    from backend.persistence.integration_repo import IntegrationBinding, IntegrationRepository
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.project_repo import ProjectRepository


class HistoryRegistration:
    def __init__(self, bindings: IntegrationRepository, jobs: JobRepository, projects: ProjectRepository) -> None:
        self._bindings = bindings
        self._jobs = jobs
        self._projects = projects

    async def _existing_job(self, binding: IntegrationBinding, project_id: str, config: dict[str, str]) -> Job:
        if (
            binding.project_id != project_id
            or binding.adapter_id != "native"
            or binding.mode != "observation"
            or binding.config != config
        ):
            raise StateConflictError("Binding identity already belongs to another source")
        if binding.job_id is None:
            raise StateConflictError("Binding has no committed observation history")
        job = await self._jobs.get(binding.job_id)
        if job is None:
            raise JobNotFoundError("Observation history no longer exists")
        if not job.observation_only or job.integration_binding_id != binding.id:
            raise StateConflictError("Binding does not own the observation history")
        return job

    async def register(
        self,
        *,
        binding_id: str,
        project_id: str,
        format_id: str,
        external_session_id: str,
        title: str | None = None,
        repo: str | None = None,
        base_ref: str | None = None,
    ) -> Job:
        validate_integration_binding_id(binding_id)
        if format_id not in NATIVE_FORMATS:
            raise ValueError("Unsupported native history format")
        if not binding_id or not external_session_id or len(binding_id) > 128 or len(external_session_id) > 512:
            raise ValueError("History registration requires binding and external source identities")
        label = " ".join(filter_credentials(title or f"{format_id} observation").text.split())[:80]
        if await self._projects.get(project_id) is None:
            raise ValueError("History registration requires an existing Project")
        config = {"format": format_id, "external_session_id": external_session_id}
        existing = await self._bindings.get(binding_id)
        if existing is not None:
            return await self._existing_job(existing, project_id, config)
        binding, created = await self._bindings.create_observation(binding_id, project_id, "native", config)
        if not created:
            return await self._existing_job(binding, project_id, config)
        now = datetime.now(UTC)
        job = Job(
            id=f"observed-{uuid.uuid4().hex}",
            project_id=project_id,
            repo=repo,
            base_ref=base_ref,
            prompt="",
            state=JobState.running,
            branch=None,
            worktree_path=None,
            session_id=None,
            created_at=now,
            updated_at=now,
            title=label,
            sdk=f"native:{format_id}",
            source=JobSource.integration,
            external_session_id=external_session_id,
            integration_binding_id=binding_id,
            observation_only=True,
            artifact_collection_status="not_applicable",
        )
        await self._jobs.create(job)
        await self._bindings.attach_job(binding_id, job.id)
        return job
