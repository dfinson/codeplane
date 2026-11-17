"""Rationale retains exact evidence, without agent commands or delivery."""

import asyncio

import pytest

from backend.models.domain import StateConflictError
from backend.models.schemas.reviews import PrepareReviewRequest, RecordReviewRequest
from backend.persistence.artifact_repo import ArtifactRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.artifacts.artifact_service import resolve_artifact_path
from backend.services.recipe.command_runner import CommandStepRunner
from backend.services.recipe.review_service import ReviewService
from backend.tests.unit.test_recipe_command_runner import command_context
from backend.tests.unit.test_recipe_runs import sessions as sessions


async def prepared_review(sessions, tmp_path, monkeypatch):
    host, _, run, _, attempt = await command_context(sessions, tmp_path, monkeypatch, "printf 'retained evidence'")
    result, outputs = await CommandStepRunner(host).run(run, await _step(sessions, attempt), attempt)
    async with sessions() as session:
        await RecipeRunRepository(session).finish_attempt(attempt.id, result, outputs)
        await session.commit()
        before = await JobRepository(session).get(run.job_id)
    review = ReviewService(host)
    prepared = await review.prepare(run.job_id, PrepareReviewRequest(expected_run_id=run.id, attempt_ids=[attempt.id]))
    request = RecordReviewRequest(
        request_key="one-explicit-review",
        snapshot_artifact_id=prepared.snapshot_artifact_id,
        snapshot_checksum=prepared.snapshot_checksum,
        rationale="I reviewed these exact command receipts and code. Delivery is a separate action.",
    )
    return host, run, attempt, before, review, prepared, request


async def _step(sessions, attempt):
    async with sessions() as session:
        return await RecipeRunRepository(session).get_step(attempt.step_instance_id)


async def test_review_is_idempotent_append_only_and_does_not_change_job_or_execute(sessions, tmp_path, monkeypatch):
    host, run, attempt, before, review, prepared, request = await prepared_review(sessions, tmp_path, monkeypatch)
    first, replay = await asyncio.gather(review.record(run.job_id, request), review.record(run.job_id, request))
    assert first == replay
    assert first.evidence.attempts[0].attempt.id == attempt.id
    assert first.evidence.attempts[0].attempt.outputs == prepared.evidence.attempts[0].attempt.outputs
    async with sessions() as session:
        after = await JobRepository(session).get(run.job_id)
        assert after.state == before.state and after.resolution == before.resolution and after.version == before.version
    history = await review.history(run.job_id, after=0, high_water=None, limit=20)
    assert [record.event_id for record in history.items] == [first.event_id]
    with pytest.raises(StateConflictError, match="different content"):
        await review.record(run.job_id, request.model_copy(update={"rationale": "Changed rationale"}))
    second = await review.record(run.job_id, request.model_copy(update={"request_key": "separate-review"}))
    frozen = await review.history(run.job_id, after=0, high_water=history.high_water, limit=20)
    assert [record.event_id for record in frozen.items] == [first.event_id]
    assert second.event_id != first.event_id and not host._tasks and not host._agent_sessions


@pytest.mark.parametrize("change", ["code", "original_artifact", "snapshot", "missing_artifact"])
async def test_review_revalidates_exact_code_and_evidence_content(sessions, tmp_path, monkeypatch, change):
    _, run, _, _, review, prepared, request = await prepared_review(sessions, tmp_path, monkeypatch)
    if change == "code":
        (tmp_path / "repo" / "source.txt").write_text("New code after capture\n")
    else:
        artifact_id = (
            prepared.snapshot_artifact_id if change == "snapshot" else prepared.evidence.artifacts[0].original_id
        )
        async with sessions() as session:
            artifact = await ArtifactRepository(session).get(artifact_id)
        path = resolve_artifact_path(artifact, job_id=run.job_id)
        if change == "missing_artifact":
            path.unlink()
        else:
            path.write_bytes(b"overwritten mutable content")
    with pytest.raises(StateConflictError):
        await review.record(run.job_id, request)
    assert (await review.history(run.job_id, after=0, high_water=None, limit=20)).items == []
