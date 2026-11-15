"""Recipe definition APIs. Saving and publishing never dispatch work."""

from __future__ import annotations

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.api_schemas import (
    CopyRecipeRequest,
    PublishRecipeRequest,
    RecipeDefinition,
    RecipeDraftListResponse,
    RecipeDraftResponse,
    RecipeRevisionListResponse,
    RecipeRevisionResponse,
    RecipeValidationResult,
    SaveRecipeDraftRequest,
)
from backend.services.recipe.definition_service import RecipeDefinitionService

router = APIRouter(prefix="/recipes", tags=["recipes"], route_class=DishkaRoute)


@router.post("", response_model=RecipeDraftResponse, status_code=201)
async def create_recipe(
    body: RecipeDefinition,
    service: FromDishka[RecipeDefinitionService],
    session: FromDishka[AsyncSession],
) -> RecipeDraftResponse:
    draft = await service.create(body)
    await session.commit()
    return draft


@router.get("", response_model=RecipeDraftListResponse)
async def list_recipes(
    service: FromDishka[RecipeDefinitionService],
    after: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    high_water: int | None = Query(None, alias="highWater", ge=0),
) -> RecipeDraftListResponse:
    return await service.list_drafts(after=after, limit=limit, high_water=high_water)


@router.post("/validate", response_model=RecipeValidationResult)
async def validate_recipe(
    body: RecipeDefinition, service: FromDishka[RecipeDefinitionService]
) -> RecipeValidationResult:
    return await service.validate(body)


@router.get("/revisions/{revision_id}", response_model=RecipeRevisionResponse)
async def get_recipe_revision(revision_id: str, service: FromDishka[RecipeDefinitionService]) -> RecipeRevisionResponse:
    return await service.get_revision(revision_id)


@router.post("/revisions/{revision_id}/copy", response_model=RecipeDraftResponse, status_code=201)
async def copy_recipe_revision(
    revision_id: str,
    body: CopyRecipeRequest,
    service: FromDishka[RecipeDefinitionService],
    session: FromDishka[AsyncSession],
) -> RecipeDraftResponse:
    draft = await service.copy_revision(revision_id, body.name)
    await session.commit()
    return draft


@router.get("/{definition_id}", response_model=RecipeDraftResponse)
async def get_recipe(definition_id: str, service: FromDishka[RecipeDefinitionService]) -> RecipeDraftResponse:
    return await service.get(definition_id)


@router.put("/{definition_id}", response_model=RecipeDraftResponse)
async def save_recipe(
    definition_id: str,
    body: SaveRecipeDraftRequest,
    service: FromDishka[RecipeDefinitionService],
    session: FromDishka[AsyncSession],
) -> RecipeDraftResponse:
    draft = await service.save(definition_id, body.definition, expected_version=body.expected_version)
    await session.commit()
    return draft


@router.post("/{definition_id}/publish", response_model=RecipeRevisionResponse, status_code=201)
async def publish_recipe(
    definition_id: str,
    body: PublishRecipeRequest,
    service: FromDishka[RecipeDefinitionService],
    session: FromDishka[AsyncSession],
) -> RecipeRevisionResponse:
    revision = await service.publish(definition_id, expected_version=body.expected_version)
    await session.commit()
    return revision


@router.get("/{definition_id}/revisions", response_model=RecipeRevisionListResponse)
async def list_recipe_revisions(
    definition_id: str,
    service: FromDishka[RecipeDefinitionService],
    after: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    high_water: int | None = Query(None, alias="highWater", ge=0),
) -> RecipeRevisionListResponse:
    return await service.list_revisions(definition_id, after=after, limit=limit, high_water=high_water)
