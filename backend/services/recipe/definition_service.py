"""Draft, validate and publish without admitting any execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from backend.models.domain import CodePlaneError, StateConflictError
from backend.models.schemas.recipes import (
    RecipeBlockRunner,
    RecipeDefinition,
    RecipeDraftListResponse,
    RecipeDraftResponse,
    RecipePinnedBlock,
    RecipeRevisionListResponse,
    RecipeRevisionResponse,
    RecipeValidationIssue,
    RecipeValidationResult,
)
from backend.services.recipe.validation import content_checksum, validate_definition

if TYPE_CHECKING:
    from backend.persistence.recipe_repo import RecipeRepository


class RecipeNotFoundError(CodePlaneError):
    pass


class RecipeValidationError(CodePlaneError):
    def __init__(self, result: RecipeValidationResult) -> None:
        super().__init__("Recipe is invalid. Correct the reported bindings and graph before publishing.")
        self.result = result


def revision_checksum(definition: RecipeDefinition, blocks: dict[str, RecipePinnedBlock]) -> str:
    return content_checksum(
        {
            "definition": definition.model_dump(mode="json", exclude_unset=True),
            "blocks": {key: block.model_dump(mode="json", exclude_unset=True) for key, block in blocks.items()},
        }
    )


class RecipeDefinitionService:
    def __init__(self, repo: RecipeRepository) -> None:
        self._repo = repo

    async def create(self, definition: RecipeDefinition) -> RecipeDraftResponse:
        now = datetime.now(UTC)
        return await self._repo.create(
            RecipeDraftResponse(id=str(uuid4()), version=1, definition=definition, created_at=now, updated_at=now)
        )

    async def get(self, definition_id: str) -> RecipeDraftResponse:
        result = await self._repo.get(definition_id)
        if result is None:
            raise RecipeNotFoundError(f"Recipe '{definition_id}' does not exist.")
        return result

    async def save(
        self, definition_id: str, definition: RecipeDefinition, *, expected_version: int
    ) -> RecipeDraftResponse:
        await self.get(definition_id)
        return await self._repo.save(
            definition_id, definition, expected_version=expected_version, now=datetime.now(UTC)
        )

    async def get_revision(self, revision_id: str) -> RecipeRevisionResponse:
        result = await self._repo.get_revision(revision_id)
        if result is None:
            raise RecipeNotFoundError(f"Published recipe revision '{revision_id}' does not exist.")
        if result.checksum != revision_checksum(result.definition, result.blocks):
            raise StateConflictError(f"Published revision '{revision_id}' failed its content integrity check.")
        return result

    async def copy_revision(self, revision_id: str, name: str) -> RecipeDraftResponse:
        revision = await self.get_revision(revision_id)
        definition = revision.definition.model_copy(deep=True)
        definition.name = name
        return await self.create(definition)

    async def _validate(
        self, definition: RecipeDefinition, *, definition_id: str | None = None
    ) -> tuple[RecipeValidationResult, dict[str, RecipePinnedBlock]]:
        blocks: dict[str, RecipePinnedBlock] = {}
        issues: list[RecipeValidationIssue] = []
        examined_nodes = 0

        async def visit(current: RecipeDefinition, active: frozenset[str], path: str, depth: int) -> None:
            nonlocal examined_nodes
            examined_nodes += len(current.steps)
            if depth > 16 or examined_nodes > 4096:
                issues.append(
                    RecipeValidationIssue(
                        path=path,
                        code="composition_bound",
                        message="Expanded recipes are limited to 16 nested levels and 4096 step instances.",
                    )
                )
                return
            for step in current.steps:
                if not isinstance(step.runner, RecipeBlockRunner):
                    continue
                revision_id = step.runner.revision_id
                reference_path = f"{path}steps.{step.id}.runner.revisionId"
                revision = await self._repo.get_revision(revision_id)
                if revision is None:
                    issues.append(
                        RecipeValidationIssue(
                            path=reference_path,
                            code="missing_revision",
                            message=f"Published revision '{revision_id}' is missing.",
                        )
                    )
                    continue
                if revision.checksum != revision_checksum(revision.definition, revision.blocks):
                    issues.append(
                        RecipeValidationIssue(
                            path=reference_path,
                            code="checksum_mismatch",
                            message="Pinned revision content failed its integrity check.",
                        )
                    )
                    continue
                if revision.definition_id in active:
                    issues.append(
                        RecipeValidationIssue(
                            path=reference_path,
                            code="recursive_composition",
                            message="A recipe cannot contain itself through nested blocks.",
                        )
                    )
                    continue
                block = RecipePinnedBlock(
                    definition_id=revision.definition_id,
                    revision_id=revision.id,
                    revision=revision.revision,
                    checksum=revision.checksum,
                    definition=revision.definition,
                )
                blocks[revision_id] = block
                await visit(
                    revision.definition,
                    active | {revision.definition_id},
                    f"{path}steps.{step.id}.block.",
                    depth + 1,
                )
            result = validate_definition(current, blocks)
            issues.extend(issue.model_copy(update={"path": path + issue.path}) for issue in result.issues)

        await visit(definition, frozenset({definition_id}) if definition_id else frozenset(), "", 0)
        return RecipeValidationResult(valid=not issues, issues=issues), blocks

    async def validate(self, definition: RecipeDefinition) -> RecipeValidationResult:
        result, _ = await self._validate(definition)
        return result

    async def publish(self, definition_id: str, *, expected_version: int) -> RecipeRevisionResponse:
        draft = await self.get(definition_id)
        if draft.version != expected_version:
            raise StateConflictError("Recipe draft changed. Reload before publishing.")
        result, blocks = await self._validate(draft.definition, definition_id=definition_id)
        if not result.valid:
            raise RecipeValidationError(result)
        return await self._repo.publish(
            draft,
            revision_id=str(uuid4()),
            blocks=blocks,
            checksum=revision_checksum(draft.definition, blocks),
            now=datetime.now(UTC),
        )

    @staticmethod
    def _check_page(after: int, limit: int, high_water: int | None) -> None:
        if after < 0 or not 1 <= limit <= 100 or (high_water is not None and high_water < after):
            raise StateConflictError("Invalid recipe page. Use the returned cursor and frozen highWater.")

    async def list_drafts(
        self, *, after: int = 0, limit: int = 30, high_water: int | None = None
    ) -> RecipeDraftListResponse:
        self._check_page(after, limit, high_water)
        return await self._repo.list_drafts(after=after, limit=limit, high_water=high_water)

    async def list_revisions(
        self, definition_id: str, *, after: int = 0, limit: int = 30, high_water: int | None = None
    ) -> RecipeRevisionListResponse:
        await self.get(definition_id)
        self._check_page(after, limit, high_water)
        return await self._repo.list_revisions(definition_id, after=after, limit=limit, high_water=high_water)
