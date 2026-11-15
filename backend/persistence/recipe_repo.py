"""Recipe definition transactions. Execution is deliberately absent here."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter
from sqlalchemy import func, select, update

from backend.models.db import RecipeDefinitionRow, RecipeRevisionRow
from backend.models.domain import StateConflictError
from backend.models.schemas.recipes import (
    RecipeDefinition,
    RecipeDraftListResponse,
    RecipeDraftResponse,
    RecipePinnedBlock,
    RecipeRevisionListResponse,
    RecipeRevisionResponse,
)
from backend.persistence.repository import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime

_BLOCKS = TypeAdapter(dict[str, RecipePinnedBlock])


class RecipeRepository(BaseRepository):
    @staticmethod
    def _draft(row: RecipeDefinitionRow) -> RecipeDraftResponse:
        return RecipeDraftResponse(
            id=row.id,
            version=row.version,
            definition=RecipeDefinition.model_validate_json(row.definition_json),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _revision(row: RecipeRevisionRow) -> RecipeRevisionResponse:
        return RecipeRevisionResponse(
            id=row.id,
            definition_id=row.definition_id,
            revision=row.revision,
            definition=RecipeDefinition.model_validate_json(row.definition_json),
            blocks=_BLOCKS.validate_json(row.blocks_json),
            checksum=row.checksum,
            published_at=row.published_at,
        )

    async def create(self, draft: RecipeDraftResponse) -> RecipeDraftResponse:
        self._session.add(
            RecipeDefinitionRow(
                id=draft.id,
                definition_json=draft.definition.model_dump_json(),
                version=1,
                latest_revision=0,
                created_at=draft.created_at,
                updated_at=draft.updated_at,
            )
        )
        await self._session.flush()
        return draft

    async def get(self, definition_id: str) -> RecipeDraftResponse | None:
        row = await self._session.scalar(
            select(RecipeDefinitionRow)
            .where(RecipeDefinitionRow.id == definition_id)
            .execution_options(populate_existing=True)
        )
        return self._draft(row) if row is not None else None

    async def save(
        self, definition_id: str, definition: RecipeDefinition, *, expected_version: int, now: datetime
    ) -> RecipeDraftResponse:
        updated = await self._session.scalar(
            update(RecipeDefinitionRow)
            .where(RecipeDefinitionRow.id == definition_id, RecipeDefinitionRow.version == expected_version)
            .values(definition_json=definition.model_dump_json(), version=expected_version + 1, updated_at=now)
            .returning(RecipeDefinitionRow)
            .execution_options(populate_existing=True)
        )
        if updated is None:
            raise StateConflictError("Recipe draft changed. Reload before saving.")
        return self._draft(updated)

    async def publish(
        self,
        draft: RecipeDraftResponse,
        *,
        revision_id: str,
        blocks: dict[str, RecipePinnedBlock],
        checksum: str,
        now: datetime,
    ) -> RecipeRevisionResponse:
        number = await self._session.scalar(
            update(RecipeDefinitionRow)
            .where(RecipeDefinitionRow.id == draft.id, RecipeDefinitionRow.version == draft.version)
            .values(
                latest_revision=RecipeDefinitionRow.latest_revision + 1,
                version=RecipeDefinitionRow.version + 1,
                updated_at=now,
            )
            .returning(RecipeDefinitionRow.latest_revision)
        )
        if number is None:
            raise StateConflictError("Recipe draft changed. Reload and validate before publishing.")
        row = RecipeRevisionRow(
            id=revision_id,
            definition_id=draft.id,
            revision=number,
            definition_json=draft.definition.model_dump_json(),
            blocks_json=_BLOCKS.dump_json(blocks).decode(),
            checksum=checksum,
            published_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return self._revision(row)

    async def get_revision(self, revision_id: str) -> RecipeRevisionResponse | None:
        row = await self._session.scalar(select(RecipeRevisionRow).where(RecipeRevisionRow.id == revision_id))
        return self._revision(row) if row is not None else None

    async def list_drafts(self, *, after: int, limit: int, high_water: int | None) -> RecipeDraftListResponse:
        maximum = high_water
        if maximum is None:
            maximum = await self._session.scalar(select(func.max(RecipeDefinitionRow.sequence))) or 0
        rows = list(
            await self._session.scalars(
                select(RecipeDefinitionRow)
                .where(RecipeDefinitionRow.sequence > after, RecipeDefinitionRow.sequence <= maximum)
                .order_by(RecipeDefinitionRow.sequence)
                .limit(limit + 1)
            )
        )
        return RecipeDraftListResponse(
            items=[self._draft(row) for row in rows[:limit]],
            next_cursor=rows[limit - 1].sequence if len(rows) > limit else None,
            high_water=maximum,
        )

    async def list_revisions(
        self, definition_id: str, *, after: int, limit: int, high_water: int | None
    ) -> RecipeRevisionListResponse:
        maximum = high_water
        if maximum is None:
            maximum = (
                await self._session.scalar(
                    select(func.max(RecipeRevisionRow.sequence)).where(RecipeRevisionRow.definition_id == definition_id)
                )
                or 0
            )
        rows = list(
            await self._session.scalars(
                select(RecipeRevisionRow)
                .where(
                    RecipeRevisionRow.definition_id == definition_id,
                    RecipeRevisionRow.sequence > after,
                    RecipeRevisionRow.sequence <= maximum,
                )
                .order_by(RecipeRevisionRow.sequence)
                .limit(limit + 1)
            )
        )
        return RecipeRevisionListResponse(
            items=[self._revision(row) for row in rows[:limit]],
            next_cursor=rows[limit - 1].sequence if len(rows) > limit else None,
            high_water=maximum,
        )
