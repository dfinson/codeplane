"""Seal selected evidence and append rationale without execution or delivery."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from sqlalchemy.exc import DBAPIError

from backend.models.domain import JobNotFoundError, StateConflictError
from backend.models.events import EventKind, EventMetadata, new_event
from backend.models.schemas.recipes import RecipeArtifactReceipt, RecipeCodeReference, RecipeCommandResult
from backend.models.schemas.reviews import (
    PrepareReviewRequest,
    RecordReviewRequest,
    ReviewArtifactSnapshot,
    ReviewAttemptSnapshot,
    ReviewEventSnapshot,
    ReviewEvidenceSnapshot,
    ReviewPreparation,
    ReviewRecord,
)
from backend.persistence.artifact_repo import ArtifactRepository
from backend.persistence.database import serialized_write
from backend.persistence.event_repo import EventRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.persistence.review_repo import ReviewRepository
from backend.services.artifacts.artifact_service import capture_immutable_file, resolve_artifact_path
from backend.services.recipe.command_runner import _settle
from backend.services.recipe.validation import content_checksum

if TYPE_CHECKING:
    from backend.models.schemas.reviews import ReviewHistoryPage
    from backend.services.runtime.service import RuntimeService

log = structlog.get_logger()
_SNAPSHOT_LIMIT = 1024 * 1024
_ARTIFACT_TOTAL_LIMIT = 256 * 1024 * 1024


async def _digest_file(path: Path, expected_size: int) -> str:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                size += len(chunk)
                if size > expected_size:
                    raise StateConflictError("Retained evidence size changed.")
                digest.update(chunk)
                await asyncio.sleep(0)
    except OSError as exc:
        raise StateConflictError(f"Retained evidence is unavailable: {path.name}") from exc
    if size != expected_size:
        raise StateConflictError("Retained evidence size changed.")
    return digest.hexdigest()


class ReviewService:
    def __init__(self, runtime: RuntimeService) -> None:
        self._runtime = runtime

    @staticmethod
    def _same_request(previous: ReviewRecord, request: RecordReviewRequest) -> ReviewRecord:
        if (
            previous.request_key != request.request_key
            or previous.rationale != request.rationale
            or previous.snapshot_artifact_id != request.snapshot_artifact_id
            or previous.snapshot_checksum != request.snapshot_checksum
        ):
            raise StateConflictError("This review request key already identifies different content.")
        return previous

    async def prepare(self, job_id: str, request: PrepareReviewRequest) -> ReviewPreparation:
        host = self._runtime
        if len(set(request.attempt_ids)) != len(request.attempt_ids) or len(set(request.event_ids)) != len(
            request.event_ids
        ):
            raise StateConflictError("Select each evidence identity once.")
        async with host._execution_control.hold(job_id):
            async with host._session_factory() as session:
                job = await JobRepository(session).get(job_id)
                if job is None:
                    raise JobNotFoundError(f"Job {job_id} does not exist.")
                runs = RecipeRunRepository(session)
                run = await runs.get_for_job(job_id)
                if (run.id if run else None) != request.expected_run_id:
                    raise StateConflictError("The selected recipe run changed. Refresh review evidence.")
                if not job.worktree_path or host._git_service is None:
                    raise StateConflictError("This Job has no retained workspace for an exact current-code review.")
                high_water = await ReviewRepository(session).event_high_water(job_id)
                attempts = []
                source_receipts: dict[str, RecipeArtifactReceipt] = {}
                for attempt_id in request.attempt_ids:
                    attempt = await runs.get_attempt(attempt_id)
                    step = await runs.get_step(attempt.step_instance_id) if attempt else None
                    if attempt is None or step is None or run is None or step.run_id != run.id:
                        raise StateConflictError("Selected evidence does not belong to this Job's selected run.")
                    if attempt.result is None:
                        raise StateConflictError("Select a completed attempt result, not an unfinished execution.")
                    for output in attempt.outputs:
                        if output.checksum != content_checksum(output.value):
                            raise StateConflictError(f"Output receipt {output.id} failed its content checksum.")
                    attempts.append(
                        ReviewAttemptSnapshot(
                            step=step,
                            attempt=attempt,
                            checksum=content_checksum(attempt.model_dump(mode="json")),
                        )
                    )
                    if isinstance(attempt.result.evidence, RecipeCommandResult):
                        for receipt in (
                            attempt.result.evidence.stdout_artifact,
                            attempt.result.evidence.stderr_artifact,
                        ):
                            if receipt:
                                source_receipts[receipt.artifact_id] = receipt
                events = []
                for event_id in request.event_ids:
                    event = await ReviewRepository(session).selected_event(job_id, event_id, high_water)
                    if event is None:
                        raise StateConflictError("The selected event is outside this Job's retained read range.")
                    data = event.model_dump(mode="json")
                    events.append(
                        ReviewEventSnapshot(
                            event_id=event.id,
                            kind=str(event.kind),
                            checksum=content_checksum(data),
                            content=data,
                        )
                    )
                originals = {}
                if sum(receipt.size_bytes for receipt in source_receipts.values()) > _ARTIFACT_TOTAL_LIMIT:
                    raise StateConflictError("Select fewer artifacts; one review capture is limited to 256 MiB.")
                for receipt in source_receipts.values():
                    artifact = await ArtifactRepository(session).get(receipt.artifact_id)
                    if artifact is None:
                        raise StateConflictError(f"Evidence artifact {receipt.artifact_id} is missing.")
                    path = resolve_artifact_path(artifact, job_id=job_id)
                    if (
                        artifact.size_bytes != receipt.size_bytes
                        or await _digest_file(path, receipt.size_bytes) != receipt.checksum
                    ):
                        raise StateConflictError(f"Evidence artifact {receipt.artifact_id} changed.")
                    originals[receipt.artifact_id] = path
            git = host._git_service
            assert git is not None
            head = await git.rev_parse("HEAD", cwd=job.worktree_path)
            code = await git.capture_worktree_commit(
                cwd=job.worktree_path,
                snapshot_ref=f"refs/codeplane/recipe-inputs/review-{uuid4().hex}",
            )
            tree = await git.rev_parse(f"{code}^{{tree}}", cwd=job.worktree_path)
            captured = []
            with ExitStack() as cleanup:
                records = []
                for receipt in source_receipts.values():
                    retained, digest = await capture_immutable_file(
                        job_id,
                        f"review-{receipt.artifact_id}.evidence",
                        originals[receipt.artifact_id],
                        expected_size=receipt.size_bytes,
                    )
                    cleanup.callback(Path(retained.disk_path).unlink, missing_ok=True)
                    records.append(retained)
                    if digest != receipt.checksum or retained.size_bytes != receipt.size_bytes:
                        raise StateConflictError("Evidence changed while it was being retained.")
                    captured.append(
                        ReviewArtifactSnapshot(
                            original_id=receipt.artifact_id,
                            retained=RecipeArtifactReceipt(
                                artifact_id=retained.id,
                                checksum=digest,
                                size_bytes=retained.size_bytes,
                                complete=receipt.complete,
                            ),
                        )
                    )
                snapshot = ReviewEvidenceSnapshot(
                    job_id=job_id,
                    job_version=job.version,
                    run_id=run.id if run else None,
                    run_version=run.version if run else None,
                    recipe_revision_id=run.revision_id if run else None,
                    recipe_snapshot_checksum=run.snapshot_checksum if run else None,
                    code=RecipeCodeReference(workspace=job.worktree_path, commit=code),
                    code_head=head,
                    code_tree=tree,
                    event_high_water=high_water,
                    attempts=attempts,
                    events=events,
                    artifacts=captured,
                    captured_at=datetime.now(UTC),
                )
                payload = snapshot.model_dump_json().encode()
                if len(payload) > _SNAPSHOT_LIMIT:
                    raise StateConflictError("Selected review metadata exceeds 1 MiB. Select fewer evidence records.")
                with tempfile.TemporaryDirectory(prefix="codeplane-review-") as directory:
                    source = Path(directory) / "snapshot.json"
                    source.write_bytes(payload)
                    retained, checksum = await capture_immutable_file(
                        job_id,
                        "review-evidence.json",
                        source,
                        mime_type="application/json",
                        expected_size=len(payload),
                    )
                    cleanup.callback(Path(retained.disk_path).unlink, missing_ok=True)
                    records.append(retained)

                async def save_captures() -> None:
                    async with serialized_write(host._session_factory) as session:
                        for artifact in records:
                            await ArtifactRepository(session).create(artifact)

                await _settle(asyncio.create_task(save_captures()))
                cleanup.pop_all()
            return ReviewPreparation(snapshot_artifact_id=retained.id, snapshot_checksum=checksum, evidence=snapshot)

    async def _snapshot(self, job_id: str, request: RecordReviewRequest) -> ReviewEvidenceSnapshot:
        async with self._runtime._session_factory() as session:
            artifact = await ArtifactRepository(session).get(request.snapshot_artifact_id)
            if artifact is None or artifact.name != "review-evidence.json" or artifact.size_bytes > _SNAPSHOT_LIMIT:
                raise StateConflictError("The selected review snapshot is unavailable.")
            path = resolve_artifact_path(artifact, job_id=job_id)
            if await _digest_file(path, artifact.size_bytes) != request.snapshot_checksum:
                raise StateConflictError("The selected review snapshot changed.")
            with path.open("rb") as source:
                data = source.read(_SNAPSHOT_LIMIT + 1)
            if len(data) > _SNAPSHOT_LIMIT:
                raise StateConflictError("The selected review snapshot exceeds its retained bound.")
        if hashlib.sha256(data).hexdigest() != request.snapshot_checksum:
            raise StateConflictError("The selected review snapshot changed during read.")
        snapshot = ReviewEvidenceSnapshot.model_validate_json(data)
        if snapshot.job_id != job_id:
            raise StateConflictError("The selected review snapshot belongs to another Job.")
        return snapshot

    async def record(self, job_id: str, request: RecordReviewRequest) -> ReviewRecord:
        if not request.rationale.strip():
            raise StateConflictError("Review rationale must not be blank.")
        host = self._runtime
        identity = content_checksum({"job_id": job_id, "request_key": request.request_key})
        event_id = f"review-{identity}"
        async with host._execution_control.hold(job_id):
            async with host._session_factory() as session:
                previous = await ReviewRepository(session).get(job_id, event_id)
            if previous is not None:
                return self._same_request(previous, request)
            snapshot = await self._snapshot(job_id, request)
            git = host._git_service
            if git is None:
                raise StateConflictError("Current code cannot be revalidated.")
            async with host._session_factory() as session:
                job = await JobRepository(session).get(job_id)
                if job is None or job.worktree_path is None or job.worktree_path != snapshot.code.workspace:
                    raise StateConflictError("The selected Job workspace is no longer available.")
            code = await git.capture_worktree_commit(
                cwd=job.worktree_path,
                snapshot_ref=f"refs/codeplane/recipe-inputs/review-check-{uuid4().hex}",
            )
            if (
                await git.rev_parse("HEAD", cwd=job.worktree_path) != snapshot.code_head
                or await git.rev_parse(f"{code}^{{tree}}", cwd=job.worktree_path) != snapshot.code_tree
            ):
                raise StateConflictError("The code changed after evidence capture. Refresh before recording rationale.")
            async with host._session_factory() as session:
                for item in snapshot.artifacts:
                    for artifact_id in (item.original_id, item.retained.artifact_id):
                        artifact = await ArtifactRepository(session).get(artifact_id)
                        if artifact is None:
                            raise StateConflictError("Selected evidence was removed after capture.")
                        path = resolve_artifact_path(artifact, job_id=job_id)
                        if await _digest_file(path, item.retained.size_bytes) != item.retained.checksum:
                            raise StateConflictError("Selected artifact content changed after capture.")
            async with serialized_write(host._session_factory) as session:
                previous = await ReviewRepository(session).get(job_id, event_id)
                if previous is not None:
                    return self._same_request(previous, request)
                current = await JobRepository(session).get(job_id)
                run = await RecipeRunRepository(session).get_for_job(job_id)
                if (
                    current is None
                    or current.version != snapshot.job_version
                    or (run.id if run else None) != snapshot.run_id
                    or (run.version if run else None) != snapshot.run_version
                    or (run.revision_id if run else None) != snapshot.recipe_revision_id
                    or (run.snapshot_checksum if run else None) != snapshot.recipe_snapshot_checksum
                ):
                    raise StateConflictError("The Job or run changed after evidence capture.")
                for selected_attempt in snapshot.attempts:
                    attempt = await RecipeRunRepository(session).get_attempt(selected_attempt.attempt.id)
                    step = await RecipeRunRepository(session).get_step(selected_attempt.step.id)
                    if (
                        attempt is None
                        or step is None
                        or step.version != selected_attempt.step.version
                        or content_checksum(attempt.model_dump(mode="json")) != selected_attempt.checksum
                    ):
                        raise StateConflictError("Selected attempt evidence changed after capture.")
                for selected_event in snapshot.events:
                    event = await ReviewRepository(session).selected_event(
                        job_id, selected_event.event_id, snapshot.event_high_water
                    )
                    if event is None or content_checksum(event.model_dump(mode="json")) != selected_event.checksum:
                        raise StateConflictError("Selected event content changed after capture.")
                record = ReviewRecord(
                    event_id=event_id,
                    job_id=job_id,
                    request_key=request.request_key,
                    rationale=request.rationale,
                    snapshot_artifact_id=request.snapshot_artifact_id,
                    snapshot_checksum=request.snapshot_checksum,
                    evidence=snapshot,
                    recorded_at=datetime.now(UTC),
                )
                event = new_event(
                    job_id,
                    EventKind.review_recorded,
                    record.model_dump(mode="json"),
                    event_id=event_id,
                    metadata=EventMetadata(
                        source_framework="codeplane",
                        namespace=("control", "review"),
                        recipe_run_id=snapshot.run_id,
                    ),
                )
                await EventRepository(session).append(event)
        try:
            await host._event_bus.publish(event)
        except (DBAPIError, OSError, RuntimeError):
            log.error("review_publication_failed_after_commit", job_id=job_id, event_id=event_id, exc_info=True)
        return record

    async def history(self, job_id: str, *, after: int, high_water: int | None, limit: int) -> ReviewHistoryPage:
        async with self._runtime._session_factory() as session:
            if await JobRepository(session).get(job_id) is None:
                raise JobNotFoundError(f"Job {job_id} does not exist.")
            return await ReviewRepository(session).history(job_id, after=after, high_water=high_water, limit=limit)
