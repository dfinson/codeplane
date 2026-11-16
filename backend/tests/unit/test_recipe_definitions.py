"""Recipe publication is pure validation plus immutable versioned storage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from pydantic import ValidationError
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from alembic import command
from backend.models.db import ApprovalRow, Base, JobRow, RecipeRevisionRow, RecipeRunRow
from backend.models.domain import StateConflictError
from backend.models.schemas.recipes import (
    RecipeAgentRunner,
    RecipeBlockRunner,
    RecipeCommandArgument,
    RecipeCommandRunner,
    RecipeDefinition,
    RecipeDependency,
    RecipeExport,
    RecipeHumanRunner,
    RecipeInput,
    RecipeLiteralBinding,
    RecipeOutput,
    RecipeOutputBinding,
    RecipeParameter,
    RecipeParameterBinding,
    RecipeRetryPolicy,
    RecipeRoute,
    RecipeStep,
    RecipeWorkspacePolicy,
)
from backend.persistence.recipe_repo import RecipeRepository
from backend.services.recipe.definition_service import (
    RecipeDefinitionService,
    RecipeValidationError,
)
from backend.services.recipe.validation import content_checksum, validate_definition

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


def agent(step_id: str, *, next_steps: list[str] | None = None) -> RecipeStep:
    return RecipeStep(
        id=step_id,
        title=step_id.title(),
        instructions="Read the selected source and return the declared result.",
        runner=RecipeAgentRunner(),
        capabilities=["read"],
        outputs={"result": RecipeOutput(type="string")},
        routes={
            "succeeded": RecipeRoute(next_steps=next_steps) if next_steps else RecipeRoute(terminal="succeeded"),
            "failed": RecipeRoute(terminal="failed"),
        },
    )


def simple_recipe() -> RecipeDefinition:
    return RecipeDefinition(name="Context", entry_steps=["context"], steps=[agent("context")])


def received(step_id: str, *, optional: bool = False) -> RecipeInput:
    return RecipeInput(
        type="string",
        binding=RecipeOutputBinding(step_id=step_id, output="result"),
        optional=optional,
        degraded_reason="Continue without this optional context, recording the missing receipt." if optional else None,
    )


def issue_codes(definition: RecipeDefinition) -> set[str]:
    return {issue.code for issue in validate_definition(definition).issues}


@pytest.fixture
async def store() -> AsyncIterator[tuple[RecipeDefinitionService, AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield RecipeDefinitionService(RecipeRepository(session)), session
    await engine.dispose()


def test_parallel_readers_join_and_human_gate_are_valid_without_scout() -> None:
    code = agent("code", next_steps=["synthesize"])
    tests = agent("tests", next_steps=["synthesize"])
    synthesize = agent("synthesize", next_steps=["approve"])
    synthesize.depends_on = [RecipeDependency(step_id="code"), RecipeDependency(step_id="tests")]
    synthesize.inputs = {"code": received("code"), "tests": received("tests")}
    gate = RecipeStep(
        id="approve",
        title="Approve implementation",
        runner=RecipeHumanRunner(proposed_action="Implement the received synthesis."),
        workspace=RecipeWorkspacePolicy(mode="none"),
        inputs={"proposal": received("synthesize")},
        depends_on=[RecipeDependency(step_id="synthesize")],
        routes={"approved": RecipeRoute(next_steps=["implement"]), "rejected": RecipeRoute(terminal="failed")},
    )
    implement = agent("implement")
    implement.capabilities = ["read", "write"]
    implement.workspace.mode = "mutable"
    implement.depends_on = [RecipeDependency(step_id="approve", outcomes=["approved"])]
    definition = RecipeDefinition(
        name="Parallel investigation", entry_steps=["code", "tests"], steps=[code, tests, synthesize, gate, implement]
    )
    result = validate_definition(definition)
    assert result.valid, result.issues
    assert all(step.id != "scout" for step in definition.steps)


def test_missing_mistyped_and_unordered_bindings_are_actionable() -> None:
    definition = simple_recipe()
    definition.parameters["count"] = RecipeParameter(type="integer")
    definition.steps[0].inputs = {
        "missing": RecipeInput(type="string"),
        "wrong": RecipeInput(type="string", binding=RecipeParameterBinding(parameter="count")),
        "unknown": received("missing"),
        "boolean": RecipeInput(type="integer", binding=RecipeLiteralBinding(value=True)),
    }
    result = validate_definition(definition)
    assert not result.valid
    assert {"missing_binding", "type_mismatch", "unknown_output", "unordered_input"} <= {
        issue.code for issue in result.issues
    }
    assert all(issue.path and issue.message for issue in result.issues)


def test_required_output_needs_success_not_just_dependency_completion() -> None:
    producer = agent("context", next_steps=["implement"])
    consumer = agent("implement")
    consumer.inputs["brief"] = received("context")
    consumer.depends_on = [RecipeDependency(step_id="context", outcomes=["succeeded", "failed"])]
    definition = RecipeDefinition(name="Unsafe handoff", entry_steps=["context"], steps=[producer, consumer])
    assert "unprotected_input" in issue_codes(definition)
    consumer.inputs["brief"] = received("context", optional=True)
    assert validate_definition(definition).valid
    consumer.inputs["brief"].degraded_reason = None
    assert "missing_degraded_reason" in issue_codes(definition)


def test_cycles_and_unreachable_nodes_cannot_publish() -> None:
    definition = simple_recipe()
    definition.steps[0].routes["succeeded"] = RecipeRoute(next_steps=["context"])
    definition.steps.append(agent("orphan"))
    assert {"cycle", "unreachable", "blocked_entry"} <= issue_codes(definition)


def test_parallel_writer_policy_requires_isolation_and_exact_reconciliation() -> None:
    left, right = agent("left", next_steps=["reconcile"]), agent("right", next_steps=["reconcile"])
    for step in (left, right):
        step.capabilities = ["read", "write"]
        step.workspace.mode = "mutable"
        step.outputs["code"] = RecipeOutput(type="code_ref")
    join = agent("reconcile")
    join.depends_on = [RecipeDependency(step_id="left"), RecipeDependency(step_id="right")]
    definition = RecipeDefinition(name="Writers", entry_steps=["left", "right"], steps=[left, right, join])
    assert "parallel_writers" in issue_codes(definition)
    for step in (left, right):
        step.workspace = RecipeWorkspacePolicy(mode="isolated", key=step.id)
    assert "missing_reconciliation" in issue_codes(definition)
    join.runner = RecipeCommandRunner(argv=["git", "merge", "--no-edit", "left", "right"], reconciles=["left", "right"])
    join.capabilities = ["read", "shell", "write"]
    join.workspace.mode = "mutable"
    join.inputs = {
        step.id: RecipeInput(type="code_ref", binding=RecipeOutputBinding(step_id=step.id, output="code"))
        for step in (left, right)
    }
    assert validate_definition(definition).valid


def test_exclusive_decision_branches_can_share_a_writer_workspace_but_cannot_join():
    gate = RecipeStep(
        id="decision",
        title="Decision",
        runner=RecipeHumanRunner(proposed_action="Choose the next action"),
        workspace=RecipeWorkspacePolicy(mode="none"),
        routes={"approved": RecipeRoute(next_steps=["implement"]), "rejected": RecipeRoute(next_steps=["revise"])},
    )
    implement, revise = agent("implement"), agent("revise")
    for step, outcome in ((implement, "approved"), (revise, "rejected")):
        step.capabilities = ["read", "write"]
        step.workspace.mode = "mutable"
        step.depends_on = [RecipeDependency(step_id="decision", outcomes=[outcome])]
    definition = RecipeDefinition(name="Exclusive", entry_steps=["decision"], steps=[gate, implement, revise])
    assert validate_definition(definition).valid
    join = agent("join")
    join.depends_on = [RecipeDependency(step_id="implement"), RecipeDependency(step_id="revise")]
    implement.routes["succeeded"] = RecipeRoute(next_steps=["join"])
    revise.routes["succeeded"] = RecipeRoute(next_steps=["join"])
    definition.steps.append(join)
    assert "impossible_join" in issue_codes(definition)


def test_retry_exhaustion_does_not_make_an_unused_failure_route_reachable():
    work, unreachable = agent("work"), agent("unused")
    work.routes["failed"] = RecipeRoute(next_steps=["unused"])
    work.retry = RecipeRetryPolicy(max_attempts=2, retry_on=["failed"], exhaustion=RecipeRoute(terminal="failed"))
    definition = RecipeDefinition(name="Repair", entry_steps=["work"], steps=[work, unreachable])
    assert "unreachable" in issue_codes(definition)


def test_attempt_bounds_and_runner_outcomes_are_not_prose() -> None:
    with pytest.raises(ValidationError):
        RecipeRetryPolicy(max_attempts=0)
    with pytest.raises(ValidationError):
        RecipeRetryPolicy(max_attempts=21)
    definition = simple_recipe()
    definition.steps[0].retry = RecipeRetryPolicy(max_attempts=3)
    assert "missing_retry_condition" in issue_codes(definition)
    definition.steps[0].retry.retry_on = ["failed"]
    assert validate_definition(definition).valid
    definition.steps[0].routes["looks_good"] = RecipeRoute(terminal="succeeded")
    assert "unknown_outcome" in issue_codes(definition)


def test_command_arguments_and_workspace_bind_exact_typed_inputs() -> None:
    definition = simple_recipe()
    step = definition.steps[0]
    step.inputs["code"] = RecipeInput(
        type="code_ref",
        binding=RecipeLiteralBinding(value={"workspace": "/repo", "commit": "a" * 40}),
    )
    step.runner = RecipeCommandRunner(argv=["git", "show", RecipeCommandArgument(input="code", field="commit")])
    step.capabilities = ["read", "shell"]
    step.workspace.source_input = "code"
    assert validate_definition(definition).valid
    step.workspace.source_input = "missing"
    step.runner.argv[2] = RecipeCommandArgument(input="code")
    assert {"invalid_workspace_source", "invalid_command_input"} <= issue_codes(definition)


async def test_draft_save_has_no_execution_and_invalid_publish_is_rejected(store) -> None:
    service, session = store
    draft = await service.create(RecipeDefinition(name="Unfinished"))
    saved = await service.save(draft.id, simple_recipe(), expected_version=draft.version)
    assert saved.version == 2
    assert await session.scalar(select(func.count()).select_from(JobRow)) == 0
    assert await session.scalar(select(func.count()).select_from(RecipeRunRow)) == 0
    assert await session.scalar(select(func.count()).select_from(RecipeRevisionRow)) == 0
    invalid = await service.create(RecipeDefinition(name="Invalid"))
    with pytest.raises(RecipeValidationError) as error:
        await service.publish(invalid.id, expected_version=1)
    assert error.value.result.issues[0].code == "missing_entry"
    assert await session.scalar(select(func.count()).select_from(RecipeRevisionRow)) == 0


async def test_published_revision_and_independent_copy_survive_later_edits(store) -> None:
    service, _ = store
    draft = await service.create(simple_recipe())
    first = await service.publish(draft.id, expected_version=1)
    changed = simple_recipe()
    changed.steps[0].instructions = "New instructions only for a new revision."
    saved = await service.save(draft.id, changed, expected_version=2)
    second = await service.publish(draft.id, expected_version=saved.version)
    assert second.revision == 2 and first.checksum != second.checksum
    assert (await service.get_revision(first.id)).definition.steps[0].instructions != changed.steps[0].instructions
    copy = await service.copy_revision(first.id, "Independent")
    assert copy.id != draft.id and copy.version == 1
    assert not (await service.list_revisions(copy.id)).items
    assert copy.definition.steps[0].id == first.definition.steps[0].id


async def test_stale_save_and_publish_cannot_overwrite_a_new_revision(store) -> None:
    service, _ = store
    draft = await service.create(simple_recipe())
    await service.publish(draft.id, expected_version=1)
    with pytest.raises(StateConflictError):
        await service.save(draft.id, RecipeDefinition(name="Stale"), expected_version=1)
    with pytest.raises(StateConflictError):
        await service.publish(draft.id, expected_version=1)
    assert len((await service.list_revisions(draft.id)).items) == 1


async def test_new_optional_schema_fields_do_not_rewrite_older_revision_receipts(store) -> None:
    service, session = store
    draft = await service.create(simple_recipe())
    revision = await service.publish(draft.id, expected_version=1)
    row = await session.scalar(select(RecipeRevisionRow).where(RecipeRevisionRow.id == revision.id))
    original_definition = json.loads(row.definition_json)
    original_definition["steps"][0]["workspace"].pop("source_input")
    original_checksum = content_checksum({"definition": original_definition, "blocks": {}})
    row.definition_json = json.dumps(original_definition)
    row.checksum = original_checksum
    await session.commit()
    retained = await service.get_revision(revision.id)
    assert retained.checksum == original_checksum
    assert retained.definition.model_dump(mode="json", exclude_unset=True) == original_definition
    copied = await service.copy_revision(revision.id, "Independent current-schema copy")
    assert copied.id != revision.definition_id
    assert (await service.get_revision(revision.id)).checksum == original_checksum


async def test_transitive_block_revisions_are_pinned_and_recursion_is_rejected(store) -> None:
    service, _ = store
    leaf = simple_recipe()
    leaf.kind = "block"
    leaf.outputs = {
        "result": RecipeExport(type="string", binding=RecipeOutputBinding(step_id="context", output="result"))
    }
    leaf_draft = await service.create(leaf)
    leaf_revision = await service.publish(leaf_draft.id, expected_version=1)

    def block_definition(name: str, revision_id: str) -> RecipeDefinition:
        step = agent("nested")
        step.runner = RecipeBlockRunner(revision_id=revision_id)
        return RecipeDefinition(
            name=name,
            kind="block",
            entry_steps=["nested"],
            steps=[step],
            outputs={
                "result": RecipeExport(type="string", binding=RecipeOutputBinding(step_id="nested", output="result"))
            },
        )

    middle = await service.create(block_definition("Middle", leaf_revision.id))
    middle_revision = await service.publish(middle.id, expected_version=1)
    outer_definition = block_definition("Outer", middle_revision.id)
    outer_definition.kind = "recipe"
    outer = await service.create(outer_definition)
    outer_revision = await service.publish(outer.id, expected_version=1)
    assert set(outer_revision.blocks) == {leaf_revision.id, middle_revision.id}

    changed = leaf.model_copy(deep=True)
    changed.steps[0].instructions = "Changed leaf."
    await service.save(leaf_draft.id, changed, expected_version=2)
    new_leaf = await service.publish(leaf_draft.id, expected_version=3)
    retained = await service.get_revision(outer_revision.id)
    assert new_leaf.id not in retained.blocks
    assert retained.blocks[leaf_revision.id].definition.steps[0].instructions != changed.steps[0].instructions

    recursive = block_definition("Recursive", middle_revision.id)
    await service.save(leaf_draft.id, recursive, expected_version=4)
    with pytest.raises(RecipeValidationError) as error:
        await service.publish(leaf_draft.id, expected_version=5)
    assert "recursive_composition" in {issue.code for issue in error.value.result.issues}


async def test_history_pagination_keeps_its_initial_storage_high_water(store) -> None:
    service, _ = store
    draft = await service.create(simple_recipe())
    for version in (1, 2, 3):
        await service.publish(draft.id, expected_version=version)
    first = await service.list_revisions(draft.id, limit=1)
    newest = await service.publish(draft.id, expected_version=4)
    second = await service.list_revisions(draft.id, after=first.next_cursor, limit=100, high_water=first.high_water)
    assert [item.revision for item in first.items + second.items] == [1, 2, 3]
    assert newest.id not in {item.id for item in second.items}
    assert second.high_water == first.high_water and second.next_cursor is None


def test_migration_matches_recipe_models_and_preserves_legacy_rows(tmp_path: Path) -> None:
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'migration.db'}")
    command.upgrade(config, "0075")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        now = datetime.now(UTC)
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            old_metadata = MetaData()
            old_projects = Table("projects", old_metadata, autoload_with=connection)
            old_jobs = Table("jobs", old_metadata, autoload_with=connection)
            old_approvals = Table("approvals", old_metadata, autoload_with=connection)
            connection.execute(
                old_projects.insert().values(
                    id="project", name="Existing", repo_paths='["/repo"]', created_at=now, updated_at=now
                )
            )
            connection.execute(
                old_jobs.insert().values(
                    id="legacy",
                    project_id="project",
                    repo="/repo",
                    prompt="Existing job",
                    state="completed",
                    base_ref="main",
                    created_at=now,
                    updated_at=now,
                    permission_mode="full_auto",
                    session_count=1,
                    preset="supervised",
                    sdk="copilot",
                    source="managed",
                    version=1,
                    mode="standard",
                    tail_offset=0,
                    artifact_collection_status="pending",
                    artifact_collection_session_count=0,
                )
            )
            connection.execute(
                old_approvals.insert().values(
                    id="approval", job_id="legacy", description="Pending tracker action", requested_at=now
                )
            )
        command.upgrade(config, "0078")
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_object": lambda obj, name, kind, reflected, compare_to: (
                        kind != "table" or name.startswith("recipe_")
                    )
                },
            )
            assert compare_metadata(context, Base.metadata) == []
            assert connection.scalar(select(JobRow.prompt).where(JobRow.id == "legacy")) == "Existing job"
            assert connection.scalar(select(ApprovalRow.resolution).where(ApprovalRow.id == "approval")) is None
            assert connection.scalar(select(func.count()).select_from(RecipeRunRow)) == 0
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        command.downgrade(config, "0075")
        with engine.connect() as connection:
            assert not any(name.startswith("recipe_") for name in inspect(connection).get_table_names())
            assert connection.scalar(select(JobRow.prompt).where(JobRow.id == "legacy")) == "Existing job"
    finally:
        engine.dispose()
