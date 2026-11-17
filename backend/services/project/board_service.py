"""Project Board projections and complete, paged persisted attention reads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.models.api_schemas import ProjectBoardResponse, ProjectResponse
from backend.models.domain import ProjectNotFoundError
from backend.persistence.project_board_repo import BOARD_COLUMNS

if TYPE_CHECKING:
    from backend.models.api_schemas import (
        ProjectApprovalPage,
        ProjectBoardColumn,
        ProjectBoardPage,
        ProjectDeliveryPage,
    )
    from backend.models.domain import Project
    from backend.persistence.project_board_repo import ProjectBoardRepository
    from backend.persistence.project_repo import ProjectRepository


class ProjectBoardService:
    def __init__(self, projects: ProjectRepository, board: ProjectBoardRepository) -> None:
        self._projects = projects
        self._board = board

    async def _project(self, project_id: str) -> Project:
        project = await self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Project '{project_id}' does not exist.")
        return project

    async def get(self, project_id: str) -> ProjectBoardResponse:
        project = await self._project(project_id)
        return ProjectBoardResponse(
            project=ProjectResponse.model_validate(project, from_attributes=True),
            totals=await self._board.totals(project_id),
            columns={column: await self._board.page(project_id, column) for column in BOARD_COLUMNS},
        )

    async def page(self, project_id: str, column: ProjectBoardColumn, *, after: str, limit: int) -> ProjectBoardPage:
        await self._project(project_id)
        return await self._board.page(project_id, column, after=after, limit=limit)

    async def approvals(self, project_id: str, *, after: str, limit: int) -> ProjectApprovalPage:
        await self._project(project_id)
        return await self._board.approvals(project_id, after=after, limit=limit)

    async def deliveries(self, project_id: str, *, after: str, limit: int) -> ProjectDeliveryPage:
        await self._project(project_id)
        return await self._board.deliveries(project_id, after=after, limit=limit)
