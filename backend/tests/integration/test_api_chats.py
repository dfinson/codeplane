"""Integration tests for the chats API endpoints.

Exercises:
  POST /api/chats
  GET  /api/chats
  GET  /api/chats/{id}
  POST /api/chats/{id}/messages
  POST /api/chats/{id}/launch-job
  POST /api/chats/{id}/attach-chain
  POST /api/chats/{id}/detach-chain
  GET  /api/chats/{id}/chain-status
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from backend.models.db import ChatMessageRow, ProjectRow, TaskLinkRow

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class TestCreateChat:
    """POST /api/chats"""

    @pytest.mark.asyncio
    async def test_create_without_project_id_defaults_to_null(self, client: AsyncClient, app: FastAPI) -> None:
        resp = await client.post("/api/chats", json={"title": "Thinking something through"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Thinking something through"
        assert data["projectId"] is None
        assert data["status"] == "open"
        assert data["id"]
        assert data["createdAt"]
        assert data["lastMessageAt"]

    @pytest.mark.asyncio
    async def test_create_with_project_id(self, client: AsyncClient, app: FastAPI) -> None:
        project = await client.post(
            "/api/settings/projects",
            json={"name": "Chat Project", "repoPaths": ["/test/chat-project"]},
        )
        resp = await client.post(
            "/api/chats",
            json={"title": "Project-scoped chat", "projectId": project.json()["id"]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["projectId"] == project.json()["id"]

    @pytest.mark.asyncio
    async def test_create_rejects_unknown_project(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/chats",
            json={"title": "Project-scoped chat", "projectId": "missing"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_requires_title(self, client: AsyncClient, app: FastAPI) -> None:
        resp = await client.post("/api/chats", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_rejects_empty_title(self, client: AsyncClient, app: FastAPI) -> None:
        resp = await client.post("/api/chats", json={"title": ""})
        assert resp.status_code == 422


class TestListChats:
    """GET /api/chats"""

    @pytest.mark.asyncio
    async def test_list_empty(self, client: AsyncClient, app: FastAPI) -> None:
        resp = await client.get("/api/chats")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    @pytest.mark.asyncio
    async def test_list_returns_created_chats(self, client: AsyncClient, app: FastAPI) -> None:
        await client.post("/api/chats", json={"title": "First"})
        await client.post("/api/chats", json={"title": "Second"})

        resp = await client.get("/api/chats")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        titles = {item["title"] for item in items}
        assert titles == {"First", "Second"}

    @pytest.mark.asyncio
    async def test_list_can_be_scoped_to_project(self, client: AsyncClient) -> None:
        first = await client.post(
            "/api/settings/projects",
            json={"name": "First", "repoPaths": ["/test/chat-a"]},
        )
        second = await client.post(
            "/api/settings/projects",
            json={"name": "Second", "repoPaths": ["/test/chat-b"]},
        )
        await client.post("/api/chats", json={"title": "A", "projectId": first.json()["id"]})
        await client.post("/api/chats", json={"title": "B", "projectId": second.json()["id"]})

        response = await client.get(f"/api/chats?project_id={first.json()['id']}")

        assert [item["title"] for item in response.json()["items"]] == ["A"]


class TestGetChat:
    """GET /api/chats/{id}"""

    @pytest.mark.asyncio
    async def test_get_existing_chat(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "Findable"})
        chat_id = create_resp.json()["id"]

        resp = await client.get(f"/api/chats/{chat_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == chat_id
        assert resp.json()["title"] == "Findable"

    @pytest.mark.asyncio
    async def test_get_missing_chat_returns_404(self, client: AsyncClient, app: FastAPI) -> None:
        resp = await client.get("/api/chats/does-not-exist")
        assert resp.status_code == 404


class TestAddChatMessage:
    """POST /api/chats/{id}/messages"""

    @pytest.mark.asyncio
    async def test_add_message_returns_201(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "Thinking"})
        chat_id = create_resp.json()["id"]

        resp = await client.post(
            f"/api/chats/{chat_id}/messages",
            json={"role": "user", "content": "Let's figure out the login bug"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["chatId"] == chat_id
        assert data["role"] == "user"
        assert data["content"] == "Let's figure out the login bug"
        assert data["id"]
        assert data["createdAt"]

    @pytest.mark.asyncio
    async def test_add_message_to_missing_chat_returns_404(self, client: AsyncClient, app: FastAPI) -> None:
        resp = await client.post(
            "/api/chats/does-not-exist/messages",
            json={"role": "user", "content": "hi"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_add_message_requires_content(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "Thinking"})
        chat_id = create_resp.json()["id"]

        resp = await client.post(f"/api/chats/{chat_id}/messages", json={"role": "user"})
        assert resp.status_code == 422


class TestSendChatTurn:
    @pytest.mark.asyncio
    async def test_user_message_is_committed_before_provider_completion(
        self,
        client: AsyncClient,
        mock_utility_session: AsyncMock,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        chat_id = (await client.post("/api/chats", json={"title": "Thinking"})).json()["id"]

        async def complete(_prompt: str, *, system_prompt: str) -> str:
            assert _prompt == "Conversation:\nuser: Persist this first\nAssistant:"
            assert "discussion-only Chat" in system_prompt
            async with session_factory() as observer:
                rows = (
                    (await observer.execute(select(ChatMessageRow).where(ChatMessageRow.chat_id == chat_id)))
                    .scalars()
                    .all()
                )
            assert [(row.role, row.content) for row in rows] == [("user", "Persist this first")]
            return "Now reply"

        mock_utility_session.complete.side_effect = complete

        response = await client.post(
            f"/api/chats/{chat_id}/turns",
            json={"content": "Persist this first"},
        )

        assert response.status_code == 200
        assert response.json()["assistantMessage"]["content"] == "Now reply"

    @pytest.mark.asyncio
    async def test_turn_returns_and_persists_assistant_response(
        self,
        client: AsyncClient,
        mock_utility_session: AsyncMock,
    ) -> None:
        mock_utility_session.complete.return_value = "Let's compare the tradeoffs."
        chat_id = (await client.post("/api/chats", json={"title": "Thinking"})).json()["id"]

        response = await client.post(
            f"/api/chats/{chat_id}/turns",
            json={"role": "user", "content": "Should we cache this?"},
        )

        assert response.status_code == 200
        assert response.json()["state"] == "assistant"
        assert response.json()["userMessage"]["role"] == "user"
        assert response.json()["assistantMessage"]["role"] == "assistant"
        transcript = (await client.get(f"/api/chats/{chat_id}/messages")).json()
        assert [message["role"] for message in transcript] == ["user", "assistant"]

    @pytest.mark.asyncio
    async def test_turn_returns_explicit_error_while_preserving_user_message(
        self,
        client: AsyncClient,
        mock_utility_session: AsyncMock,
    ) -> None:
        mock_utility_session.complete.side_effect = RuntimeError("offline")
        chat_id = (await client.post("/api/chats", json={"title": "Thinking"})).json()["id"]

        response = await client.post(
            f"/api/chats/{chat_id}/turns",
            json={"content": "Hello"},
        )

        assert response.status_code == 200
        assert response.json()["state"] == "error"
        assert response.json()["assistantMessage"] is None
        transcript = (await client.get(f"/api/chats/{chat_id}/messages")).json()
        assert [message["role"] for message in transcript] == ["user"]


class TestLaunchJobFromChat:
    """POST /api/chats/{id}/launch-job"""

    project_id: str

    @pytest.fixture(autouse=True)
    async def _seed_launch_project(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/settings/projects",
            json={"name": "Launch Project", "repoPaths": ["/test/repo"]},
        )
        assert response.status_code == 201
        self.project_id = response.json()["id"]

    @pytest.fixture(autouse=True)
    def _patch_git_for_launch(self, monkeypatch: pytest.MonkeyPatch, mock_git_service: AsyncMock) -> None:
        """Same pattern as test_api_jobs.py: launch-job provisions a real
        worktree/branch via GitService, so the module-level GitService
        class must resolve to the mock, not hit real git."""
        monkeypatch.setattr(
            "backend.services.git.git_service.GitService",
            lambda config: mock_git_service,
        )

    @pytest.mark.asyncio
    async def test_launch_job_creates_job_seeded_from_transcript(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "Thinking it through"})
        chat_id = create_resp.json()["id"]
        await client.post(
            f"/api/chats/{chat_id}/messages",
            json={"role": "user", "content": "Please fix the login bug"},
        )

        resp = await client.post(
            f"/api/chats/{chat_id}/launch-job",
            json={"repo": "/test/repo"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"]
        assert data["state"] in ("preparing", "queued", "running")

    @pytest.mark.asyncio
    async def test_launch_job_leaves_chat_open_and_unchanged(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "Thinking it through"})
        chat_id = create_resp.json()["id"]

        await client.post(f"/api/chats/{chat_id}/launch-job", json={"repo": "/test/repo"})

        get_resp = await client.get(f"/api/chats/{chat_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["status"] == "open"
        assert data["title"] == "Thinking it through"

    @pytest.mark.asyncio
    async def test_launch_job_twice_creates_two_independent_jobs(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "Thinking it through"})
        chat_id = create_resp.json()["id"]

        resp_1 = await client.post(f"/api/chats/{chat_id}/launch-job", json={"repo": "/test/repo"})
        resp_2 = await client.post(f"/api/chats/{chat_id}/launch-job", json={"repo": "/test/repo"})

        assert resp_1.status_code == 201
        assert resp_2.status_code == 201
        assert resp_1.json()["id"] != resp_2.json()["id"]

    @pytest.mark.asyncio
    async def test_launch_job_settles_null_project_id(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "Global-nav chat"})
        chat_id = create_resp.json()["id"]
        assert create_resp.json()["projectId"] is None

        await client.post(f"/api/chats/{chat_id}/launch-job", json={"repo": "/test/repo"})

        get_resp = await client.get(f"/api/chats/{chat_id}")
        assert get_resp.json()["projectId"] == self.project_id

    @pytest.mark.asyncio
    async def test_launch_job_from_missing_chat_returns_404(self, client: AsyncClient, app: FastAPI) -> None:
        resp = await client.post(
            "/api/chats/does-not-exist/launch-job",
            json={"repo": "/test/repo"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_launch_job_requires_repo(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "No repo"})
        chat_id = create_resp.json()["id"]

        resp = await client.post(f"/api/chats/{chat_id}/launch-job", json={})
        assert resp.status_code == 422


async def _seed_project_and_task_link(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_id: str = "proj-1",
    task_link_id: str = "tl-1",
    story_node_id: str | None = "1-1",
    repo_path: str = "/test/repo",
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            ProjectRow(
                id=project_id,
                name="Chain Project",
                repo_paths="[]",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TaskLinkRow(
                id=task_link_id,
                project_id=project_id,
                repo_path=repo_path,
                story_node_id=story_node_id,
                depends_on="[]",
                job_id=None,
                tracker_ticket_ref=None,
                prompt_override=None,
                epic_id=None,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


class TestAttachChatToChain:
    """POST /api/chats/{id}/attach-chain"""

    @pytest.mark.asyncio
    async def test_attach_links_chat_to_task_link(
        self, client: AsyncClient, app: FastAPI, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project_and_task_link(session_factory)
        create_resp = await client.post("/api/chats", json={"title": "Supervising a chain"})
        chat_id = create_resp.json()["id"]

        resp = await client.post(f"/api/chats/{chat_id}/attach-chain", json={"taskLinkId": "tl-1"})
        assert resp.status_code == 200
        assert resp.json()["taskLinkId"] == "tl-1"

    @pytest.mark.asyncio
    async def test_attach_settles_null_project_id_from_chain(
        self, client: AsyncClient, app: FastAPI, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project_and_task_link(session_factory, project_id="proj-2", task_link_id="tl-2")
        create_resp = await client.post("/api/chats", json={"title": "Unscoped chat"})
        chat_id = create_resp.json()["id"]
        assert create_resp.json()["projectId"] is None

        resp = await client.post(f"/api/chats/{chat_id}/attach-chain", json={"taskLinkId": "tl-2"})
        assert resp.status_code == 200
        assert resp.json()["projectId"] == "proj-2"

    @pytest.mark.asyncio
    async def test_attach_to_missing_task_link_returns_404(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "Chat"})
        chat_id = create_resp.json()["id"]

        resp = await client.post(f"/api/chats/{chat_id}/attach-chain", json={"taskLinkId": "does-not-exist"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_attach_to_missing_chat_returns_404(
        self, client: AsyncClient, app: FastAPI, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project_and_task_link(session_factory, project_id="proj-3", task_link_id="tl-3")

        resp = await client.post("/api/chats/does-not-exist/attach-chain", json={"taskLinkId": "tl-3"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_attach_requires_task_link_id(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "Chat"})
        chat_id = create_resp.json()["id"]

        resp = await client.post(f"/api/chats/{chat_id}/attach-chain", json={})
        assert resp.status_code == 422


class TestDetachChatFromChain:
    """POST /api/chats/{id}/detach-chain"""

    @pytest.mark.asyncio
    async def test_detach_clears_attachment_and_leaves_chat_open(
        self, client: AsyncClient, app: FastAPI, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project_and_task_link(session_factory, project_id="proj-4", task_link_id="tl-4")
        create_resp = await client.post("/api/chats", json={"title": "Watching a chain"})
        chat_id = create_resp.json()["id"]
        await client.post(f"/api/chats/{chat_id}/attach-chain", json={"taskLinkId": "tl-4"})

        resp = await client.post(f"/api/chats/{chat_id}/detach-chain")
        assert resp.status_code == 200
        data = resp.json()
        assert data["taskLinkId"] is None
        assert data["status"] == "open"

    @pytest.mark.asyncio
    async def test_detach_leaves_chain_running_as_before(
        self, client: AsyncClient, app: FastAPI, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """AC 3: the chain continues to exist and run exactly as before."""
        await _seed_project_and_task_link(session_factory, project_id="proj-5", task_link_id="tl-5")
        create_resp = await client.post("/api/chats", json={"title": "Watching"})
        chat_id = create_resp.json()["id"]
        await client.post(f"/api/chats/{chat_id}/attach-chain", json={"taskLinkId": "tl-5"})

        await client.post(f"/api/chats/{chat_id}/detach-chain")

        task_links_resp = await client.get("/api/settings/projects/proj-5/task-links")
        assert task_links_resp.status_code == 200
        items = task_links_resp.json()["items"]
        assert any(t["id"] == "tl-5" for t in items)

    @pytest.mark.asyncio
    async def test_detach_missing_chat_returns_404(self, client: AsyncClient, app: FastAPI) -> None:
        resp = await client.post("/api/chats/does-not-exist/detach-chain")
        assert resp.status_code == 404


class TestChatChainStatus:
    """GET /api/chats/{id}/chain-status"""

    @pytest.mark.asyncio
    async def test_status_reflects_attached_task_link(
        self, client: AsyncClient, app: FastAPI, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_project_and_task_link(
            session_factory, project_id="proj-6", task_link_id="tl-6", story_node_id="2-1", repo_path="/test/repo-6"
        )
        create_resp = await client.post("/api/chats", json={"title": "Narrating"})
        chat_id = create_resp.json()["id"]
        await client.post(f"/api/chats/{chat_id}/attach-chain", json={"taskLinkId": "tl-6"})

        resp = await client.get(f"/api/chats/{chat_id}/chain-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["taskLinkId"] == "tl-6"
        assert data["storyNodeId"] == "2-1"
        assert data["repoPath"] == "/test/repo-6"
        assert data["jobId"] is None
        assert data["jobState"] is None

    @pytest.mark.asyncio
    async def test_status_404_when_nothing_attached(self, client: AsyncClient, app: FastAPI) -> None:
        create_resp = await client.post("/api/chats", json={"title": "No chain yet"})
        chat_id = create_resp.json()["id"]

        resp = await client.get(f"/api/chats/{chat_id}/chain-status")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_status_404_for_missing_chat(self, client: AsyncClient, app: FastAPI) -> None:
        resp = await client.get("/api/chats/does-not-exist/chain-status")
        assert resp.status_code == 404
