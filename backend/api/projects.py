"""Project management endpoints (Story 2.1 / CAP-6).

Thin routes: validate input, delegate to ``ProjectService``, return the
result. ``ProjectRow`` is the sole persistence entity for repo-path
membership (AD-5) — creating/editing a Project is the only way a repo path
is durably registered going forward.
"""

from __future__ import annotations

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.api_schemas import (
    CreateManualTaskLinkRequest,
    CreateProjectRequest,
    ProjectApprovalPage,
    ProjectBoardColumn,
    ProjectBoardPage,
    ProjectBoardResponse,
    ProjectDeliveryPage,
    ProjectListResponse,
    ProjectListSummaryResponse,
    ProjectResponse,
    ProjectSummaryResponse,
    TaskLinkInspectionResponse,
    TaskLinkListResponse,
    TaskLinkResponse,
    UpdateProjectRequest,
)
from backend.models.domain import Project, ProjectSummary, TaskLink
from backend.services.project.board_service import ProjectBoardService
from backend.services.project.project_service import ProjectService
from backend.services.recipe.recipe_service import RecipeService
from backend.services.runtime import RuntimeService
from backend.services.task_execution_service import TaskExecutionService

router = APIRouter(tags=["projects"], route_class=DishkaRoute)


def _to_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        repo_paths=project.repo_paths,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _to_summary_response(summary: ProjectSummary) -> ProjectSummaryResponse:
    return ProjectSummaryResponse(
        id=summary.id,
        name=summary.name,
        repo_paths=summary.repo_paths,
        active_job_count=summary.active_job_count,
        awaiting_input_count=summary.awaiting_input_count,
        failed_count=summary.failed_count,
        last_activity_at=summary.last_activity_at,
    )


def _task_link_to_response(task_link: TaskLink) -> TaskLinkResponse:
    return TaskLinkResponse(
        id=task_link.id,
        project_id=task_link.project_id,
        repo_path=task_link.repo_path,
        story_node_id=task_link.story_node_id,
        depends_on=task_link.depends_on,
        chain_root_id=task_link.chain_root_id,
        state=task_link.state,
        job_id=task_link.job_id,
        tracker_link_id=task_link.tracker_link_id,
        tracker_ticket_ref=task_link.tracker_ticket_ref,
        prompt_override=task_link.prompt_override,
        epic_id=task_link.epic_id,
        output_routes=task_link.output_routes,
        created_at=task_link.created_at,
        updated_at=task_link.updated_at,
    )


@router.post("/settings/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: CreateProjectRequest,
    project_service: FromDishka[ProjectService],
) -> ProjectResponse:
    """Create a new Project, registering each repo path (AD-5)."""
    project = await project_service.create(name=body.name, repo_paths=body.repo_paths)
    return _to_response(project)


@router.get("/settings/projects", response_model=ProjectListResponse)
async def list_projects(
    project_service: FromDishka[ProjectService],
) -> ProjectListResponse:
    """List all registered Projects."""
    projects = await project_service.list()
    return ProjectListResponse(items=[_to_response(p) for p in projects])


# NOTE: Must be registered before `/settings/projects/{project_id}` so the
# literal `summary` path segment isn't captured as a project_id.
@router.get("/settings/projects/summary", response_model=ProjectListSummaryResponse)
async def get_projects_summary(
    project_service: FromDishka[ProjectService],
) -> ProjectListSummaryResponse:
    """Batch Overview summary for every Project (Story 2.2 / CAP-2).

    Single call for all Projects — the Overview screen never does N
    sequential per-Project fetches. Projects with no jobs at all are still
    included, with all-zero counts.
    """
    summaries = await project_service.summary_all()
    return ProjectListSummaryResponse(items=[_to_summary_response(s) for s in summaries])


@router.get("/settings/projects/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    project_service: FromDishka[ProjectService],
) -> ProjectResponse:
    """Get a single Project by ID."""
    project = await project_service.get(project_id)
    return _to_response(project)


@router.patch("/settings/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    body: UpdateProjectRequest,
    project_service: FromDishka[ProjectService],
) -> ProjectResponse:
    """Rename a Project and/or replace its repo membership."""
    project = await project_service.update(
        project_id,
        name=body.name,
        repo_paths=body.repo_paths,
        confirm_repo_removal=body.confirm_repo_removal,
    )
    return _to_response(project)


@router.get("/settings/projects/{project_id}/task-links", response_model=TaskLinkListResponse)
async def list_project_task_links(
    project_id: str,
    recipe_service: FromDishka[RecipeService],
) -> TaskLinkListResponse:
    """List a Project's currently persisted TaskLinks."""
    task_links = await recipe_service.list_task_links(project_id)
    return TaskLinkListResponse(items=[_task_link_to_response(t) for t in task_links])


@router.post(
    "/settings/projects/{project_id}/task-links",
    response_model=TaskLinkResponse,
    status_code=201,
)
async def create_manual_task_link(
    project_id: str,
    body: CreateManualTaskLinkRequest,
    recipe_service: FromDishka[RecipeService],
) -> TaskLinkResponse:
    """Create a fresh TaskLink directly against an existing tracker ticket."""
    task_link = await recipe_service.create_manual_task_link(
        project_id=project_id,
        repo_path=body.repo_path,
        tracker_link_id=body.tracker_link_id,
        tracker_ticket_ref=body.tracker_ticket_ref,
        prompt_override=body.prompt_override,
        output_routes=list(body.output_routes),
    )
    return _task_link_to_response(task_link)


@router.post(
    "/settings/projects/{project_id}/task-links/{task_link_id}/start",
    response_model=TaskLinkResponse,
)
async def start_task_link(
    project_id: str,
    task_link_id: str,
    background_tasks: BackgroundTasks,
    recipe_service: FromDishka[RecipeService],
    runtime_service: FromDishka[RuntimeService],
    session: FromDishka[AsyncSession],
) -> TaskLinkResponse:
    """Atomically claim and start one ready TaskLink."""
    task_link, job = await recipe_service.start_task_link(project_id, task_link_id)
    await session.commit()
    background_tasks.add_task(runtime_service.setup_and_start, job)
    return _task_link_to_response(task_link)


@router.get(
    "/settings/projects/{project_id}/task-links/{task_link_id}",
    response_model=TaskLinkInspectionResponse,
)
async def inspect_task_link(
    project_id: str,
    task_link_id: str,
    execution_service: FromDishka[TaskExecutionService],
) -> TaskLinkInspectionResponse:
    return await execution_service.inspect_task_link(project_id, task_link_id)


@router.get("/settings/projects/{project_id}/board", response_model=ProjectBoardResponse)
async def get_project_board(project_id: str, service: FromDishka[ProjectBoardService]) -> ProjectBoardResponse:
    return await service.get(project_id)


@router.get("/settings/projects/{project_id}/board/columns/{column}", response_model=ProjectBoardPage)
async def get_project_board_column(
    project_id: str,
    column: ProjectBoardColumn,
    service: FromDishka[ProjectBoardService],
    after: str = Query("", max_length=512),
    limit: int = Query(20, ge=1, le=100),
) -> ProjectBoardPage:
    return await service.page(project_id, column, after=after, limit=limit)


@router.get("/settings/projects/{project_id}/board/approvals", response_model=ProjectApprovalPage)
async def get_project_board_approvals(
    project_id: str,
    service: FromDishka[ProjectBoardService],
    after: str = Query("", max_length=512),
    limit: int = Query(30, ge=1, le=100),
) -> ProjectApprovalPage:
    return await service.approvals(project_id, after=after, limit=limit)


@router.get("/settings/projects/{project_id}/board/deliveries", response_model=ProjectDeliveryPage)
async def get_project_board_deliveries(
    project_id: str,
    service: FromDishka[ProjectBoardService],
    after: str = Query("", max_length=512),
    limit: int = Query(20, ge=1, le=100),
) -> ProjectDeliveryPage:
    return await service.deliveries(project_id, after=after, limit=limit)
