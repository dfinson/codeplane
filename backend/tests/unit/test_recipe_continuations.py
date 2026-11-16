"""Same-Job continuation admission preserves all earlier execution identities."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, delete, func, select

from alembic import command
from backend.models.db import ApprovalRow, RecipeRunRow
from backend.models.domain import StateConflictError
from backend.models.schemas.recipes import RecipeCodeReference, RecipeDefinition, RecipeParameter
from backend.persistence.database import serialized_write
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.recipe.validation import content_checksum
from backend.tests.unit.test_recipe_definitions import agent
from backend.tests.unit.test_recipe_runs import agent_result, create_run, ready_attempt
from backend.tests.unit.test_recipe_runs import sessions as sessions

if TYPE_CHECKING:
    from pathlib import Path


async def completed_run(sessions):
    definition = RecipeDefinition(name="Continue", entry_steps=["work"], steps=[agent("work")])
    run = await create_run(sessions, definition)
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        attempt = await ready_attempt(session, run, "work")
        await repo.finish_attempt(attempt.id, agent_result(), {"result": "Retained original result"})
        ended = await repo.resolve_run(run.id, "succeeded")
        await session.commit()
        return ended, attempt


def continuation_arguments(previous, key="explicit-resume"):
    return {
        "start_request_key": key,
        "continuation_of_id": previous.id,
        "instruction": "Continue with the requested correction.",
        "code_ref": RecipeCodeReference(workspace="/repo/worktree", commit="a" * 40),
    }


async def test_new_run_preserves_job_revision_and_does_not_copy_attempts_or_receipts(sessions):
    previous, original_attempt = await completed_run(sessions)
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        following = await repo.create(
            previous.job_id, previous.snapshot, previous.inputs, **continuation_arguments(previous)
        )
        await session.commit()
        assert following.job_id == previous.job_id
        assert following.revision_id == previous.revision_id
        assert following.run_number == 2 and following.continuation_of_id == previous.id
        assert following.instruction == "Continue with the requested correction."
        assert following.code_ref.commit == "a" * 40
        assert (await repo.get_for_job(previous.job_id)).id == following.id
        original = await repo.get_attempt(original_attempt.id)
        assert original.result.outcome == "succeeded" and original.outputs[0].value == "Retained original result"
        children = await repo.list_steps(following.id)
        assert children and all(child.id != original_attempt.step_instance_id for child in children)
        assert all([not await repo.list_attempts(child.id) for child in children])
        assert (await repo.get(previous.id)).resolved_at == previous.resolved_at
        assert await session.scalar(select(func.count()).select_from(ApprovalRow)) == 0


async def test_concurrent_double_resume_reuses_one_admitted_run(sessions):
    previous, _ = await completed_run(sessions)

    async def resume():
        async with serialized_write(sessions) as session:
            return await RecipeRunRepository(session).create(
                previous.job_id, previous.snapshot, previous.inputs, **continuation_arguments(previous)
            )

    left, right = await asyncio.gather(resume(), resume())
    assert left.id == right.id
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        assert await session.scalar(select(func.count()).select_from(RecipeRunRow)) == 2
        with pytest.raises(StateConflictError, match="unresolved"):
            await repo.create(
                previous.job_id, previous.snapshot, previous.inputs, **continuation_arguments(previous, "other-resume")
            )
        with pytest.raises(StateConflictError, match="different inputs"):
            await repo.create(
                previous.job_id,
                previous.snapshot,
                previous.inputs,
                **{**continuation_arguments(previous), "instruction": "A different request"},
            )


async def test_post_insert_collision_rechecks_the_competing_request_payload(sessions):
    definition = RecipeDefinition(
        name="Competing starts",
        entry_steps=["work"],
        steps=[agent("work")],
        parameters={"request": RecipeParameter(type="string", required=False, default="seed")},
    )
    seeded = await create_run(sessions, definition)
    async with sessions() as session:
        await session.execute(delete(RecipeRunRow).where(RecipeRunRow.id == seeded.id))
        await session.commit()

    class CompetingRepository(RecipeRunRepository):
        async def get_for_job(self, job_id):
            before = await super().get_for_job(job_id)
            assert before is None
            async with sessions() as competitor:
                await RecipeRunRepository(competitor).create(
                    job_id, seeded.snapshot, {"request": "winner"}, start_request_key="same-request"
                )
                await competitor.commit()
            return before

    async with sessions() as session:
        with pytest.raises(StateConflictError, match="different inputs"):
            await CompetingRepository(session).create(
                seeded.job_id, seeded.snapshot, {"request": "loser"}, start_request_key="same-request"
            )
        await session.rollback()
    async with sessions() as session:
        winner = await RecipeRunRepository(session).get_for_job(seeded.job_id)
        assert winner.inputs == {"request": "winner"}
        assert await session.scalar(select(func.count()).select_from(RecipeRunRow)) == 1


async def test_canceled_uncertain_run_still_blocks_another_run(sessions):
    work = agent("work")
    work.capabilities = ["read", "write"]
    work.workspace.mode = "mutable"
    run = await create_run(sessions, RecipeDefinition(name="Uncertain", entry_steps=["work"], steps=[work]))
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        attempt = await ready_attempt(session, run, "work")
        await repo.finish_attempt(attempt.id, agent_result("failed", uncertain=True), {})
        await repo.request_cancel(run.id)
        await session.commit()
        with pytest.raises(StateConflictError, match="remains unresolved"):
            await repo.resolve_run(run.id, "canceled")
        with pytest.raises(StateConflictError, match="unresolved"):
            await repo.create(run.job_id, run.snapshot, run.inputs, **continuation_arguments(run))
        assert (await repo.get_for_job(run.job_id)).id == run.id


async def test_run_history_uses_frozen_sequence_and_exact_ids(sessions):
    previous, _ = await completed_run(sessions)
    async with sessions() as session:
        repo = RecipeRunRepository(session)
        second = await repo.create(previous.job_id, previous.snapshot, {}, **continuation_arguments(previous))
        second = await repo.resolve_run(second.id, "failed", reason="Preparation failed before dispatch")
        await session.commit()
        first_page = await repo.list_runs(previous.job_id, limit=1)
        third = await repo.create(
            previous.job_id, previous.snapshot, {}, **continuation_arguments(second, "next-resume")
        )
        await session.commit()
        next_page = await repo.list_runs(
            previous.job_id, after=first_page.next_cursor, high_water=first_page.high_water
        )
        assert [item.run_number for item in first_page.items + next_page.items] == [1, 2]
        assert (await repo.get_for_job(previous.job_id)).id == third.id
        assert (await repo.get(second.id)).reason == "Preparation failed before dispatch"


def test_0078_retains_old_run_rows_and_refuses_destructive_continuation_downgrade(tmp_path: Path):
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'continuations.db'}")
    command.upgrade(config, "0077")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    now = datetime.now(UTC)
    try:
        with engine.begin() as connection:
            metadata = MetaData()
            tables = {
                name: Table(name, metadata, autoload_with=connection)
                for name in (
                    "projects",
                    "jobs",
                    "approvals",
                    "recipe_definitions",
                    "recipe_revisions",
                    "recipe_runs",
                    "recipe_step_instances",
                    "recipe_attempts",
                )
            }
            connection.execute(
                tables["projects"]
                .insert()
                .values(
                    id="project",
                    name="Project",
                    repo_paths='["/repo"]',
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                tables["jobs"]
                .insert()
                .values(
                    id="job",
                    project_id="project",
                    repo="/repo",
                    base_ref="main",
                    prompt="Original",
                    state="review",
                    permission_mode="full_auto",
                    session_count=1,
                    preset="supervised",
                    sdk="copilot",
                    source="managed",
                    mode="standard",
                    version=1,
                    tail_offset=0,
                    artifact_collection_status="pending",
                    artifact_collection_session_count=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                tables["approvals"]
                .insert()
                .values(
                    id="approval",
                    job_id="job",
                    description="Retained approval",
                    requested_at=now,
                )
            )
            definition = {"name": "Original", "steps": []}
            connection.execute(
                tables["recipe_definitions"]
                .insert()
                .values(
                    id="definition",
                    definition_json=json.dumps(definition),
                    version=1,
                    latest_revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                tables["recipe_revisions"]
                .insert()
                .values(
                    id="revision",
                    definition_id="definition",
                    revision=1,
                    definition_json=json.dumps(definition),
                    blocks_json="{}",
                    checksum=content_checksum(definition),
                    published_at=now,
                )
            )
            connection.execute(
                tables["recipe_runs"]
                .insert()
                .values(
                    id="original-run",
                    job_id="job",
                    revision_id="revision",
                    snapshot_json='{"retained":"unchanged"}',
                    snapshot_checksum="b" * 64,
                    inputs_json='{"request":"Original"}',
                    state="succeeded",
                    version=1,
                    created_at=now,
                    updated_at=now,
                    completed_at=now,
                )
            )
        command.upgrade(config, "0078")
        with engine.begin() as connection:
            original = connection.exec_driver_sql(
                "SELECT id, run_number, start_request_key, snapshot_json, instruction, code_ref_json, resolved_at "
                "FROM recipe_runs WHERE id='original-run'"
            ).one()
            assert original[:6] == ("original-run", 1, "retained:original-run", '{"retained":"unchanged"}', None, None)
            assert original.resolved_at is not None
            connection.exec_driver_sql(
                "INSERT INTO recipe_runs (id,job_id,run_number,start_request_key,continuation_of_id,instruction,"
                "revision_id,snapshot_json,snapshot_checksum,inputs_json,state,version,created_at,updated_at) "
                "SELECT 'new-run',job_id,2,'resume-request',id,'Explicit continuation',revision_id,snapshot_json,"
                "snapshot_checksum,inputs_json,'ready',1,created_at,updated_at FROM recipe_runs WHERE id='original-run'"
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        with pytest.raises(RuntimeError, match="continuation history exists"):
            command.downgrade(config, "0077")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM recipe_runs").scalar_one() == 2
            assert (
                connection.exec_driver_sql("SELECT description FROM approvals WHERE id='approval'").scalar_one()
                == "Retained approval"
            )
    finally:
        engine.dispose()
