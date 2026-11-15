"""Application-scoped, recoverable TaskLink execution.

Only the Job/TaskLink attachment transaction accepts a start. Git discovery
and naming do not reserve a task, and workspace setup belongs to the runtime
after commit. ApprovalService calls never run inside our locks or sessions.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, TypedDict
from weakref import WeakValueDictionary

import structlog

from backend.models.domain import (
    Approval,
    ApprovalResolution,
    Job,
    JobSpec,
    JobState,
    ProjectNotFoundError,
    StateConflictError,
    TaskLink,
    TaskLinkNotFoundError,
    TaskLinkState,
)
from backend.persistence.approval_repo import ApprovalRepository
from backend.persistence.chat_repo import ChatRepository
from backend.persistence.database import serialized_write
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.persistence.task_link_repo import TaskLinkRepository
from backend.persistence.tracker_link_repo import TrackerLinkRepository
from backend.services.job.job_service import JobService
from backend.services.tracker_resolution import TrackerResolutionError
from backend.services.tracker_write_service import TrackerWriteAction, TrackerWriteRequest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CPLConfig
    from backend.services.git.git_service import GitService
    from backend.services.job.approval_service import ApprovalService
    from backend.services.tracker_write_service import TrackerWriteService

log = structlog.get_logger()

_SUCCESSFUL_RESOLUTIONS = frozenset({"merged", "pr_created"})
_RETRYABLE_STATES = frozenset({TaskLinkState.ready, TaskLinkState.waiting, TaskLinkState.failed})


class SpawnAction(TypedDict):
    """Normalized action; legacy actions have version 0 and unknown attempt."""

    schemaVersion: int
    kind: Literal["spawn_task"]
    taskLinkId: str
    predecessorJobId: str | None
    sessionCount: int | None


def parse_spawn_action(proposed_action: str | None) -> SpawnAction | None:
    """Parse v1 JSON or legacy ``spawn_task:<id>`` without inventing history.

    Returns a SpawnAction dict with exactly schemaVersion, kind, taskLinkId,
    predecessorJobId and sessionCount. Legacy version is 0 and its last two
    fields are None; callers use the Approval's job_id for that association.
    Malformed, unsupported-version and unrelated actions return None.
    """
    if not isinstance(proposed_action, str):
        return None
    if proposed_action.startswith("spawn_task:"):
        legacy_task_id = proposed_action.removeprefix("spawn_task:")
        if not legacy_task_id or legacy_task_id != legacy_task_id.strip():
            return None
        return {
            "schemaVersion": 0,
            "kind": "spawn_task",
            "taskLinkId": legacy_task_id,
            "predecessorJobId": None,
            "sessionCount": None,
        }
    try:
        value = json.loads(proposed_action)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        return None
    if value.get("kind") != "spawn_task":
        return None
    task_id, predecessor = value.get("taskLinkId"), value.get("predecessorJobId")
    count = value.get("sessionCount")
    if not isinstance(task_id, str) or not task_id.strip() or task_id != task_id.strip():
        return None
    if not isinstance(predecessor, str) or not predecessor.strip() or predecessor != predecessor.strip():
        return None
    if type(count) is not int or count < 1:
        return None
    return {
        "schemaVersion": 1,
        "kind": "spawn_task",
        "taskLinkId": task_id,
        "predecessorJobId": predecessor,
        "sessionCount": count,
    }


def _key(link: TaskLink) -> str | None:
    return None if link.story_node_id is None else f"{link.repo_path}::{link.story_node_id}"


def _successful(job: Job | None) -> bool:
    return job is not None and job.state == JobState.completed and str(job.resolution or "") in _SUCCESSFUL_RESOLUTIONS


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass
class _Snapshot:
    task: TaskLink
    links: list[TaskLink]
    jobs: dict[str, Job]
    approvals: list[Approval]
    gated: bool

    def dependencies(self) -> list[Job] | None:
        by_key = {key: link for link in self.links if (key := _key(link)) is not None}
        result: list[Job] = []
        for key in self.task.depends_on:
            target = by_key.get(key)
            job = self.jobs.get(target.job_id) if target is not None and target.job_id else None
            if not _successful(job):
                return None
            assert job is not None
            result.append(job)
        return result

    def decisions(self, *, legacy_root_parent_id: str | None = None) -> list[Approval]:
        """Validate graph attempts or an explicitly associated legacy root gate."""
        dependencies = self.dependencies()
        if dependencies is None:
            return []
        parents = {job.id: job for job in dependencies}
        result: list[Approval] = []
        for approval in self.approvals:
            action = parse_spawn_action(approval.proposed_action)
            if action is None or action["taskLinkId"] != self.task.id:
                continue
            parent = parents.get(approval.job_id)
            if (
                parent is None
                and not self.task.depends_on
                and action["schemaVersion"] == 0
                and approval.job_id == legacy_root_parent_id
            ):
                # Legacy root gates used the approval's Job as an evidence owner,
                # not as a dependency. Do not invent a graph edge or attempt.
                owner = self.jobs.get(approval.job_id)
                if owner is not None and owner.project_id == self.task.project_id:
                    parent = owner
            if parent is None:
                continue
            if action["schemaVersion"] == 1 and (
                action["predecessorJobId"] != parent.id or action["sessionCount"] != parent.session_count
            ):
                continue
            result.append(approval)
        return sorted(result, key=lambda a: (_utc(a.requested_at), a.id))


class TaskExecutionService:
    """One instance per application, shared by REST, MCP and subscribers.

    Public methods own their transactions. Call them without a caller-owned
    writer transaction. ``recover_on_startup`` runs once before admission;
    it only prepares records, leaving agent startup to RuntimeService.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
        *,
        git_service: GitService | None = None,
        approval_service: ApprovalService | None = None,
        tracker_write_service: TrackerWriteService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._git = git_service
        self._approvals = approval_service
        self._tracker = tracker_write_service
        self._task_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        # A synchronous reservation coalesces gate provisioning without holding
        # a lock across ApprovalService's independently committed transaction.
        self._gates_inflight: dict[str, asyncio.Event] = {}

    def _lock(self, task_id: str) -> asyncio.Lock:
        lock = self._task_locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            self._task_locks[task_id] = lock
        return lock

    async def _snapshot(
        self,
        session: AsyncSession,
        project_id: str,
        task_id: str,
        *,
        approved_parent_job_id: str | None = None,
    ) -> _Snapshot:
        if await ProjectRepository(session).get(project_id) is None:
            raise ProjectNotFoundError(f"Project '{project_id}' does not exist.")
        links = await TaskLinkRepository(session).list_by_project(project_id)
        task = next((link for link in links if link.id == task_id), None)
        if task is None:
            raise TaskLinkNotFoundError(f"TaskLink '{task_id}' does not exist in Project '{project_id}'.")
        jobs: dict[str, Job] = {}
        approvals: dict[str, Approval] = {}
        job_repo, approval_repo = JobRepository(session), ApprovalRepository(session)
        for link in links:
            if link.job_id is None:
                continue
            job = await job_repo.get(link.job_id)
            if job is not None and job.project_id == project_id:
                jobs[job.id] = job
                for approval in await approval_repo.list_for_job(job.id):
                    approvals[approval.id] = approval
        if approved_parent_job_id is not None and approved_parent_job_id not in jobs:
            owner = await job_repo.get(approved_parent_job_id)
            if owner is not None and owner.project_id == project_id:
                jobs[owner.id] = owner
                for approval in await approval_repo.list_for_job(owner.id):
                    approvals[approval.id] = approval
        # Preserve direct-start protection for legacy pending actions, including
        # manually assigned roots that are not graph dependency targets.
        for approval in await approval_repo.list_pending():
            action = parse_spawn_action(approval.proposed_action)
            if action is not None and action["taskLinkId"] == task_id:
                approvals[approval.id] = approval
        gated = bool(task.depends_on) and (
            await ChatRepository(session).get_attached_open_chat_for_chain(task.chain_root_id or task.id) is not None
        )
        return _Snapshot(task, links, jobs, list(approvals.values()), gated)

    @staticmethod
    def _decision(
        snapshot: _Snapshot,
        *,
        mode: Literal["direct", "automatic", "approved"],
        parent_job_id: str | None,
        recovery: bool,
    ) -> tuple[Literal["start", "gate", "skip"], Approval | None]:
        task = snapshot.task
        if task.job_id is not None or task.state not in _RETRYABLE_STATES or snapshot.dependencies() is None:
            return "skip", None
        matching = [
            a
            for a in snapshot.approvals
            if (action := parse_spawn_action(a.proposed_action)) is not None and action["taskLinkId"] == task.id
        ]
        if mode == "direct":
            if any(a.resolution is None for a in matching):
                raise StateConflictError(f"TaskLink '{task.id}' has a pending chain approval.")
            if snapshot.gated:
                raise StateConflictError(f"TaskLink '{task.id}' belongs to a gated chain and requires approval.")
            return "start", None
        decisions = snapshot.decisions(legacy_root_parent_id=parent_job_id if mode == "approved" else None)
        if any(a.resolution is None for a in decisions):
            return "skip", None
        latest = decisions[-1] if decisions else None
        if latest is not None and latest.resolution == ApprovalResolution.approved:
            if mode == "approved" and latest.job_id != parent_job_id:
                return "skip", None
            return "start", latest
        if mode == "approved":
            return "skip", None
        if latest is not None and latest.resolution == ApprovalResolution.rejected:
            # Startup is replay, not an operator request. A later real completion
            # may request again, but an older sibling's delayed event may not.
            if recovery:
                return "skip", None
            parent = snapshot.jobs.get(parent_job_id or "")
            if parent is None:
                return "skip", None
            for approval in decisions:
                action = parse_spawn_action(approval.proposed_action)
                assert action is not None
                if approval.job_id == parent.id and (
                    action["sessionCount"] is None or action["sessionCount"] == parent.session_count
                ):
                    return "skip", None
            if _utc(parent.completed_at or parent.updated_at) <= _utc(latest.requested_at):
                return "skip", None
            return "gate", None
        return ("gate", None) if snapshot.gated else ("start", None)

    async def _run(
        self,
        project_id: str,
        task_id: str,
        *,
        mode: Literal["direct", "automatic", "approved"],
        parent_job_id: str | None,
        recovery: bool = False,
    ) -> tuple[TaskLink, Job] | None:
        gate: tuple[TaskLink, Job] | None = None
        async with self._lock(task_id):
            if task_id in self._gates_inflight:
                if mode == "direct":
                    raise StateConflictError(f"TaskLink '{task_id}' has a chain approval being persisted.")
                return None
            async with self._session_factory() as session:
                before = await self._snapshot(
                    session,
                    project_id,
                    task_id,
                    approved_parent_job_id=parent_job_id if mode == "approved" else None,
                )
                decision, approval = self._decision(
                    before,
                    mode=mode,
                    parent_job_id=parent_job_id,
                    recovery=recovery,
                )
                if decision == "skip":
                    if mode == "direct":
                        raise StateConflictError(f"TaskLink '{task_id}' is not ready or already has a Job.")
                    return None
                if decision == "gate":
                    if self._approvals is None:
                        raise StateConflictError("ApprovalService is required for a gated task chain.")
                    parent = before.jobs.get(parent_job_id or "")
                    if parent is None or not _successful(parent):
                        return None
                    gate = before.task, parent
                else:
                    from backend.services.recipe.recipe_service import RecipeService

                    actual_parent = approval.job_id if approval is not None else parent_job_id
                    spec = JobSpec(
                        repo=before.task.repo_path,
                        project_id=project_id,
                        prompt=RecipeService._derive_prompt(before.task),
                        parent_job_id=actual_parent,
                    )
                    prepared = await JobService.from_session(
                        session,
                        self._config,
                        git_service=self._git,
                    ).prepare_job(spec)
            if gate is not None:
                self._gates_inflight[task_id] = asyncio.Event()
            else:
                # The read session above is closed before this writer is acquired.
                async with serialized_write(self._session_factory) as session:
                    current = await self._snapshot(
                        session,
                        project_id,
                        task_id,
                        approved_parent_job_id=parent_job_id if mode == "approved" else None,
                    )
                    final, final_approval = self._decision(
                        current,
                        mode=mode,
                        parent_job_id=parent_job_id,
                        recovery=recovery,
                    )
                    if final != "start":
                        if mode == "direct":
                            raise StateConflictError(f"TaskLink '{task_id}' changed while preparing its Job.")
                        return None
                    if (
                        current.task.repo_path != before.task.repo_path
                        or current.task.depends_on != before.task.depends_on
                        or current.task.chain_root_id != before.task.chain_root_id
                        or RecipeService._derive_prompt(current.task) != spec.prompt
                        or (final_approval.id if final_approval else None) != (approval.id if approval else None)
                    ):
                        raise StateConflictError(f"TaskLink '{task_id}' changed while preparing its Job; retry.")
                    repo = TaskLinkRepository(session)
                    if current.task.state != TaskLinkState.ready:
                        ready = await repo.compare_and_set_state(
                            task_id,
                            TaskLinkState.ready,
                            expected_state=current.task.state,
                            expected_job_id=None,
                        )
                        if ready is None:
                            raise StateConflictError(f"TaskLink '{task_id}' is no longer ready.")
                    if await repo.claim_ready(task_id) is None:
                        raise StateConflictError(f"TaskLink '{task_id}' was already claimed.")
                    job = await JobService.from_session(
                        session,
                        self._config,
                        git_service=self._git,
                    ).persist_prepared_job(prepared, align_generated_branch=True)
                    attached = await repo.attach_claimed_job(
                        task_id,
                        job.id,
                        state=TaskLinkState.failed if job.state == JobState.failed else TaskLinkState.running,
                    )
                    if attached is None:
                        raise StateConflictError(f"TaskLink '{task_id}' lost its uncommitted start.")
                return attached, job
        # No task lock, database session, or writer transaction survives here.
        assert gate is not None and self._approvals is not None
        task, parent = gate
        try:
            await self._approvals.create_request(
                job_id=parent.id,
                description=(
                    f"Chain is gated by an attached Chat: dependent task "
                    f"'{task.story_node_id or task.tracker_ticket_ref}' is ready to "
                    "spawn. Approve to start its job."
                ),
                proposed_action=json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": "spawn_task",
                        "taskLinkId": task.id,
                        "predecessorJobId": parent.id,
                        "sessionCount": parent.session_count,
                    },
                    separators=(",", ":"),
                ),
                requires_explicit_approval=True,
            )
        finally:
            self._gates_inflight.pop(task_id).set()
        return None

    async def start_task_link(self, project_id: str, task_link_id: str) -> tuple[TaskLink, Job]:
        result = await self._run(project_id, task_link_id, mode="direct", parent_job_id=None)
        assert result is not None
        return result

    async def spawn_approved_task_link(self, task_link_id: str, *, parent_job_id: str | None) -> Job | None:
        # A decision may arrive after Approval's commit but before create_request
        # returns. Wait for provisioning, not for approval, without owning a lock.
        provisioning = self._gates_inflight.get(task_link_id)
        if provisioning is not None:
            await provisioning.wait()
        async with self._session_factory() as session:
            task = await TaskLinkRepository(session).get(task_link_id)
        if task is None or task.job_id is not None:
            return None
        result = await self._run(task.project_id, task.id, mode="approved", parent_job_id=parent_job_id)
        return result[1] if result is not None else None

    async def _refresh(
        self,
        session: AsyncSession,
        links: list[TaskLink],
        *,
        reset_legacy: bool = False,
    ) -> list[TaskLink]:
        repo, jobs = TaskLinkRepository(session), JobRepository(session)
        by_key = {key: link for link in links if (key := _key(link)) is not None}
        refreshed: list[TaskLink] = []
        for link in links:
            if link.job_id is not None:
                job = await jobs.get(link.job_id)
                if job is None or job.state in {JobState.failed, JobState.canceled}:
                    desired = TaskLinkState.failed
                elif job.state == JobState.completed:
                    desired = TaskLinkState.completed if _successful(job) else TaskLinkState.failed
                else:
                    desired = TaskLinkState.running
            elif link.state == TaskLinkState.starting and not reset_legacy:
                refreshed.append(link)
                continue
            else:
                satisfied = True
                for key in link.depends_on:
                    dep = by_key.get(key)
                    job = await jobs.get(dep.job_id) if dep is not None and dep.job_id else None
                    if not _successful(job):
                        satisfied = False
                        break
                desired = TaskLinkState.ready if satisfied else TaskLinkState.waiting
            if desired != link.state:
                updated = await repo.compare_and_set_state(
                    link.id,
                    desired,
                    expected_state=link.state,
                    expected_job_id=link.job_id,
                )
                link = updated or await repo.get(link.id) or link
            refreshed.append(link)
        return refreshed

    async def list_task_links(self, project_id: str) -> list[TaskLink]:
        async with serialized_write(self._session_factory) as session:
            if await ProjectRepository(session).get(project_id) is None:
                raise ProjectNotFoundError(f"Project '{project_id}' does not exist.")
            return await self._refresh(session, await TaskLinkRepository(session).list_by_project(project_id))

    async def _enqueue_tracker(self, session: AsyncSession, task: TaskLink, job: Job) -> bool:
        if self._tracker is None or "tracker_write" not in (task.output_routes or []):
            return False
        if not task.tracker_ticket_ref:
            log.info("tracker_write_skipped", task_link_id=task.id, reason="missing_ticket_ref")
            return False
        if not task.tracker_link_id:
            log.warning("tracker_write_skipped", task_link_id=task.id, reason="missing_tracker_link")
            return False
        tracker = await TrackerLinkRepository(session).get(task.tracker_link_id)
        if tracker is None or tracker["project_id"] != task.project_id:
            log.warning("tracker_write_skipped", task_link_id=task.id, reason="invalid_tracker_link")
            return False
        request = TrackerWriteRequest(
            tracker_link_id=task.tracker_link_id,
            ticket_ref=task.tracker_ticket_ref,
            action=TrackerWriteAction.comment,
            value=f"Task '{task.story_node_id or task.id}' completed.",
        )
        await self._tracker.enqueue_in_session(
            session,
            job.id,
            request,
            source_key=json.dumps(
                ["task_output", task.id, job.id, job.session_count, "tracker_write"],
                separators=(",", ":"),
            ),
            task_link_id=task.id,
        )
        return True

    async def handle_job_completed(self, job_id: str, *, resolution: str | None) -> list[Job]:
        return await self._completed(job_id, resolution=resolution, recovery=False)

    async def _completed(self, job_id: str, *, resolution: str | None, recovery: bool) -> list[Job]:
        enqueued = False
        async with serialized_write(self._session_factory) as session:
            repo = TaskLinkRepository(session)
            task = await repo.get_by_job_id(job_id)
            job = await JobRepository(session).get(job_id)
            # A delayed completion event must not project over a resumed Job.
            if task is None or job is None or job.state != JobState.completed:
                return []
            success = _successful(job)
            await repo.compare_and_set_state(
                task.id,
                TaskLinkState.completed if success else TaskLinkState.failed,
                expected_state=task.state,
                expected_job_id=job_id,
            )
            if not success or resolution not in _SUCCESSFUL_RESOLUTIONS:
                return []
            if not recovery:
                try:
                    enqueued = await self._enqueue_tracker(session, task, job)
                except TrackerResolutionError:
                    log.warning("tracker_write_skipped", task_link_id=task.id, reason="invalid_tracker_target")
            key = _key(task)
            candidates: list[TaskLink] = []
            if key is not None:
                project_links = await self._refresh(session, await repo.list_by_project(task.project_id))
                candidates = [
                    candidate for candidate in project_links if candidate.job_id is None and key in candidate.depends_on
                ]
        if enqueued and self._tracker is not None:
            self._tracker.wake()
        spawned: list[Job] = []
        for candidate in candidates:
            try:
                result = await self._run(
                    task.project_id,
                    candidate.id,
                    mode="automatic",
                    parent_job_id=job_id,
                    recovery=recovery,
                )
                if result is not None:
                    spawned.append(result[1])
            except Exception:
                log.exception("task_link_spawn_failed", task_link_id=candidate.id, predecessor_job_id=job_id)
        return spawned

    async def handle_job_failed(self, job_id: str) -> None:
        async with serialized_write(self._session_factory) as session:
            repo = TaskLinkRepository(session)
            task = await repo.get_by_job_id(job_id)
            job = await JobRepository(session).get(job_id)
            if task is not None and job is not None and job.state in {JobState.failed, JobState.canceled}:
                await repo.compare_and_set_state(
                    task.id,
                    TaskLinkState.failed,
                    expected_state=task.state,
                    expected_job_id=job_id,
                )

    async def recover_on_startup(self) -> None:
        """Repair legacy claims and missed successors before runtime admission.

        Never call this as a periodic sweep, never start agents here, and never
        backfill tracker notifications for historical task completions.
        """
        async with serialized_write(self._session_factory) as session:
            repo = TaskLinkRepository(session)
            legacy = await repo.list_legacy_unlinked_starting()
            projects = await ProjectRepository(session).list()
            for project in projects:
                await self._refresh(session, await repo.list_by_project(project.id), reset_legacy=True)
            linked = await repo.list_linked()
            completed = []
            for task in linked:
                job = await JobRepository(session).get(task.job_id) if task.job_id else None
                if _successful(job):
                    assert job is not None
                    completed.append(job)
            legacy_roots = [
                (task, owner_job_id)
                for task, owner_job_id in await repo.list_approved_legacy_starts()
                if not task.depends_on
            ]
        if legacy:
            log.info("legacy_task_starts_reconciled", count=len(legacy))
        for job in completed:
            await self._completed(job.id, resolution=str(job.resolution), recovery=True)
        for task, owner_job_id in legacy_roots:
            try:
                await self._run(
                    task.project_id,
                    task.id,
                    mode="approved",
                    parent_job_id=owner_job_id,
                    recovery=True,
                )
            except Exception:
                log.exception("legacy_root_spawn_failed", task_link_id=task.id, parent_job_id=owner_job_id)
