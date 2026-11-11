"""Unit tests for backend.services.chat.chat_service — ChatService.

Also statically guards the AD-12/NFR8 invariant: chat_service.py must
never depend on GitService, structurally, not just behaviorally.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from backend.models.domain import Chat, Project, StateConflictError
from backend.services.chat import chat_service as chat_service_module
from backend.services.chat.chat_service import ChatService


def _mock_repo() -> AsyncMock:
    repo = AsyncMock()

    async def _create(chat: Chat) -> Chat:
        return chat

    repo.create.side_effect = _create
    return repo


class TestRuntimeContract:
    @pytest.mark.asyncio
    async def test_two_turn_chat_uses_discussion_system_context_through_real_pool(self):
        from backend.services.adapters.agent_adapter import CompletionResult
        from backend.services.sidecar.session import SidecarSessionManager

        repo = _mock_repo()
        persisted = []
        repo.get.return_value = Chat(
            id="chat-contract", project_id=None, title="Discussion", created_at=None, last_message_at=None
        )
        repo.add_message.side_effect = lambda message: (persisted.append(message), message)[1]
        repo.list_messages.side_effect = lambda _chat_id: list(persisted)
        adapter = AsyncMock()
        adapter.complete.side_effect = [CompletionResult(text="First answer"), CompletionResult(text="Second answer")]
        manager = SidecarSessionManager(adapter, model="utility-model")
        service = ChatService(repo, completer=manager)
        for content in ["First question", "Follow-up question"]:
            user_message = await service.begin_turn("chat-contract", content=content)
            assert user_message is not None
            result = await service.complete_turn("chat-contract", user_message=user_message)
            assert result.state == "assistant"
        first, second = adapter.complete.call_args_list
        assert first.kwargs["system_message"] == chat_service_module._CHAT_SYSTEM_PROMPT
        assert chat_service_module._CHAT_SYSTEM_PROMPT not in first.args[0]
        assert first.kwargs["excluded_tools"] == ["*"]
        assert second.kwargs["system_message"] == chat_service_module._CHAT_SYSTEM_PROMPT
        assert second.args[0] == (
            "Conversation:\nuser: First question\nassistant: First answer\nuser: Follow-up question\nAssistant:"
        )
        assert [message.role for message in persisted] == ["user", "assistant", "user", "assistant"]

    @pytest.mark.asyncio
    async def test_provider_error_preserves_committed_user_message(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from backend.models.db import Base
        from backend.persistence.chat_repo import ChatRepository
        from backend.services.sidecar.session import SidecarSessionManager

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        adapter = AsyncMock()
        adapter.complete.side_effect = OSError("provider unavailable")
        manager = SidecarSessionManager(adapter, model="utility-model")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                service = ChatService(ChatRepository(session), completer=manager)
                chat = await service.create_chat(title="Saved discussion")
                user_message = await service.begin_turn(chat.id, content="Keep my question")
                assert user_message is not None
                await session.commit()
            async with factory() as session:
                service = ChatService(ChatRepository(session), completer=manager)
                result = await service.complete_turn(chat.id, user_message=user_message)
                assert result.state == "error"
                assert result.assistant_message is None
                assert result.user_message.id == user_message.id
                await session.commit()
            async with factory() as session:
                messages = await ChatRepository(session).list_messages(chat.id)
                assert [(message.role, message.content) for message in messages] == [("user", "Keep my question")]
            assert adapter.complete.call_args.kwargs["system_message"] == chat_service_module._CHAT_SYSTEM_PROMPT
        finally:
            await engine.dispose()


class TestChatServiceCreateChat:
    @pytest.mark.asyncio
    async def test_default_project_id_is_null(self):
        repo = _mock_repo()
        service = ChatService(repo)

        chat = await service.create_chat(title="Thinking something through")

        assert chat.project_id is None
        assert chat.title == "Thinking something through"
        assert chat.status == "open"
        repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_project_id_passed_through_when_provided(self):
        repo = _mock_repo()
        service = ChatService(repo)

        chat = await service.create_chat(title="Project-scoped chat", project_id="proj-123")

        assert chat.project_id == "proj-123"

    @pytest.mark.asyncio
    async def test_project_id_is_overridable_regardless_of_context(self):
        """Even when a caller supplies a project_id, it's just a normal
        argument — nothing in ChatService forces or coerces it."""
        repo = _mock_repo()
        service = ChatService(repo)

        chat_a = await service.create_chat(title="A", project_id="proj-a")
        chat_b = await service.create_chat(title="B", project_id=None)
        chat_c = await service.create_chat(title="C", project_id="proj-c")

        assert chat_a.project_id == "proj-a"
        assert chat_b.project_id is None
        assert chat_c.project_id == "proj-c"

    @pytest.mark.asyncio
    async def test_timestamps_set_on_creation(self):
        repo = _mock_repo()
        service = ChatService(repo)

        chat = await service.create_chat(title="Timed")

        assert chat.created_at is not None
        assert chat.last_message_at is not None
        assert chat.created_at == chat.last_message_at

    @pytest.mark.asyncio
    async def test_id_is_generated_and_unique(self):
        repo = _mock_repo()
        service = ChatService(repo)

        chat_1 = await service.create_chat(title="One")
        chat_2 = await service.create_chat(title="Two")

        assert chat_1.id
        assert chat_2.id
        assert chat_1.id != chat_2.id


class TestChatServiceReads:
    @pytest.mark.asyncio
    async def test_get_chat_delegates_to_repo(self):
        repo = _mock_repo()
        expected = Chat(
            id="c1",
            project_id=None,
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.get.return_value = expected
        service = ChatService(repo)

        result = await service.get_chat("c1")

        assert result is expected
        repo.get.assert_awaited_once_with("c1")

    @pytest.mark.asyncio
    async def test_list_chats_delegates_to_repo(self):
        repo = _mock_repo()
        repo.list_all.return_value = []
        service = ChatService(repo)

        result = await service.list_chats()

        assert result == []
        repo.list_all.assert_awaited_once()


class TestChatServiceMessages:
    @pytest.mark.asyncio
    async def test_add_message_returns_none_for_missing_chat(self):
        repo = _mock_repo()
        repo.get.return_value = None
        service = ChatService(repo)

        result = await service.add_message("missing", role="user", content="hi")

        assert result is None
        repo.add_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_message_persists_via_repo(self):
        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id=None,
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.add_message.side_effect = lambda message: message
        service = ChatService(repo)

        result = await service.add_message("c1", role="user", content="Let's think this through")

        assert result is not None
        assert result.chat_id == "c1"
        assert result.role == "user"
        assert result.content == "Let's think this through"
        repo.add_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_build_transcript_returns_none_for_missing_chat(self):
        repo = _mock_repo()
        repo.get.return_value = None
        service = ChatService(repo)

        result = await service.build_transcript("missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_build_transcript_concatenates_in_order(self):
        from backend.models.domain import ChatMessage

        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id=None,
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.list_messages.return_value = [
            ChatMessage(id="m1", chat_id="c1", role="user", content="first", created_at=None),  # type: ignore[arg-type]
            ChatMessage(id="m2", chat_id="c1", role="assistant", content="second", created_at=None),  # type: ignore[arg-type]
        ]
        service = ChatService(repo)

        transcript = await service.build_transcript("c1")

        assert transcript == "user: first\nassistant: second"

    @pytest.mark.asyncio
    async def test_build_transcript_empty_for_no_messages(self):
        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id=None,
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.list_messages.return_value = []
        service = ChatService(repo)

        transcript = await service.build_transcript("c1")

        assert transcript == ""

    @pytest.mark.asyncio
    async def test_send_turn_persists_user_and_assistant_messages(self):
        repo = _mock_repo()
        chat = Chat(
            id="c1",
            project_id=None,
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
        )
        repo.get.return_value = chat
        persisted = []

        async def add(message):
            persisted.append(message)
            return message

        repo.add_message.side_effect = add
        repo.list_messages.side_effect = lambda _chat_id: persisted
        completer = AsyncMock()
        completer.complete.return_value = "A useful response"

        service = ChatService(repo, completer=completer)
        user_message = await service.begin_turn("c1", content="Help me decide")
        assert user_message is not None
        result = await service.complete_turn("c1", user_message=user_message)

        assert result is not None
        assert result.state == "assistant"
        assert [message.role for message in persisted] == ["user", "assistant"]
        assert result.assistant_message is not None
        assert result.assistant_message.content == "A useful response"

    @pytest.mark.asyncio
    async def test_send_turn_persists_user_and_returns_error_when_completion_fails(self):
        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id=None,
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
        )
        persisted = []

        async def add(message):
            persisted.append(message)
            return message

        repo.add_message.side_effect = add
        repo.list_messages.side_effect = lambda _chat_id: persisted
        completer = AsyncMock()
        completer.complete.side_effect = RuntimeError("offline")

        service = ChatService(repo, completer=completer)
        user_message = await service.begin_turn("c1", content="Hello")
        assert user_message is not None
        result = await service.complete_turn("c1", user_message=user_message)

        assert result is not None
        assert result.state == "error"
        assert result.assistant_message is None
        assert [message.role for message in persisted] == ["user"]


class TestChatServiceLaunchJob:
    @pytest.mark.asyncio
    async def test_launch_job_returns_none_for_missing_chat(self):
        repo = _mock_repo()
        repo.get.return_value = None
        service = ChatService(repo)
        job_service = AsyncMock()

        result = await service.launch_job("missing", job_service, repo="/repos/test")

        assert result is None
        job_service.create_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_launch_job_seeds_prompt_from_transcript(self):
        from backend.models.domain import ChatMessage

        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id=None,
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.list_messages.return_value = [
            ChatMessage(id="m1", chat_id="c1", role="user", content="do the thing", created_at=None),  # type: ignore[arg-type]
        ]
        service = ChatService(repo)
        job_service = AsyncMock()
        fake_job = object()
        job_service.create_job.return_value = fake_job

        result = await service.launch_job("c1", job_service, repo="/repos/test")

        assert result is fake_job
        job_service.create_job.assert_awaited_once()
        (spec,), _ = job_service.create_job.await_args
        assert spec.repo == "/repos/test"
        assert spec.prompt == "user: do the thing"

    @pytest.mark.asyncio
    async def test_launch_job_settles_null_project_id_from_repo(self):
        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id=None,
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.list_messages.return_value = []
        project_repo = AsyncMock()
        project_repo.find_by_repo_path.return_value = Project(
            id="proj-1",
            name="Owned",
            repo_paths=["/repos/test"],
            created_at=None,  # type: ignore[arg-type]
            updated_at=None,  # type: ignore[arg-type]
        )
        service = ChatService(repo, project_repo=project_repo)
        job_service = AsyncMock()
        job_service.create_job.return_value = object()

        await service.launch_job("c1", job_service, repo="/repos/test")

        repo.set_project_id.assert_awaited_once_with("c1", "proj-1")

    @pytest.mark.asyncio
    async def test_launch_job_rejects_repo_owned_by_different_project(self):
        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
        )
        repo.list_messages.return_value = []
        project_repo = AsyncMock()
        project_repo.find_by_repo_path.return_value = Project(
            id="proj-b",
            name="Other",
            repo_paths=["/repos/test"],
            created_at=None,  # type: ignore[arg-type]
            updated_at=None,  # type: ignore[arg-type]
        )
        job_service = AsyncMock()

        with pytest.raises(StateConflictError):
            await ChatService(repo, project_repo=project_repo).launch_job("c1", job_service, repo="/repos/test")

        job_service.create_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_launch_job_does_not_resettle_existing_project_id(self):
        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.list_messages.return_value = []
        service = ChatService(repo)
        job_service = AsyncMock()
        job_service.create_job.return_value = object()

        await service.launch_job("c1", job_service, repo="/repos/test")

        repo.set_project_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_launch_job_twice_creates_two_independent_jobs(self):
        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.list_messages.return_value = []
        service = ChatService(repo)
        job_service = AsyncMock()
        job_service.create_job.side_effect = [object(), object()]

        job_1 = await service.launch_job("c1", job_service, repo="/repos/test")
        job_2 = await service.launch_job("c1", job_service, repo="/repos/test")

        assert job_1 is not job_2
        assert job_service.create_job.await_count == 2

    @pytest.mark.asyncio
    async def test_launch_job_does_not_mutate_chat_status(self):
        """The Chat is repeatable/never consumed: launching a Job never
        changes its status, title, or otherwise transforms it (AC 2)."""
        repo = _mock_repo()
        chat = Chat(
            id="c1",
            project_id="proj-a",
            title="Original title",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.get.return_value = chat
        repo.list_messages.return_value = []
        service = ChatService(repo)
        job_service = AsyncMock()
        job_service.create_job.return_value = object()

        await service.launch_job("c1", job_service, repo="/repos/test")

        assert chat.status == "open"
        assert chat.title == "Original title"


class TestChatServiceAttachToChain:
    @pytest.mark.asyncio
    async def test_attach_returns_none_for_missing_chat(self):
        repo = _mock_repo()
        repo.get.return_value = None
        task_link_repo = AsyncMock()
        service = ChatService(repo, task_link_repo=task_link_repo)

        result = await service.attach_to_chain("missing", "tl-1")

        assert result is None
        task_link_repo.get.assert_not_awaited()
        repo.attach_to_chain.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_attach_rejects_task_link_from_different_project(self):
        from backend.models.domain import TaskLink

        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
        )
        task_link_repo = AsyncMock()
        task_link_repo.get.return_value = TaskLink(
            id="tl-1",
            project_id="proj-b",
            repo_path="/repos/b",
            story_node_id="1-1",
            depends_on=[],
            job_id=None,
            tracker_ticket_ref=None,
            prompt_override=None,
            epic_id=None,
            created_at=None,  # type: ignore[arg-type]
            updated_at=None,  # type: ignore[arg-type]
        )

        with pytest.raises(StateConflictError):
            await ChatService(repo, task_link_repo=task_link_repo).attach_to_chain("c1", "tl-1")

        repo.attach_to_chain.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_attach_raises_for_missing_task_link(self):
        from backend.models.domain import TaskLinkNotFoundError

        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id=None,
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        task_link_repo = AsyncMock()
        task_link_repo.get.return_value = None
        service = ChatService(repo, task_link_repo=task_link_repo)

        with pytest.raises(TaskLinkNotFoundError):
            await service.attach_to_chain("c1", "missing-tl")

        repo.attach_to_chain.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_attach_links_chat_to_task_link(self):
        from backend.models.domain import TaskLink

        repo = _mock_repo()
        chat = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.get.return_value = chat
        task_link = TaskLink(
            id="tl-1",
            project_id="proj-a",
            repo_path="/repos/test",
            story_node_id="1-1",
            depends_on=[],
            job_id=None,
            tracker_ticket_ref=None,
            prompt_override=None,
            epic_id=None,
            created_at=None,  # type: ignore[arg-type]
            updated_at=None,  # type: ignore[arg-type]
        )
        task_link_repo = AsyncMock()
        task_link_repo.get.return_value = task_link
        attached_chat = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
            task_link_id="tl-1",
        )
        repo.attach_to_chain.return_value = attached_chat
        service = ChatService(repo, task_link_repo=task_link_repo)

        result = await service.attach_to_chain("c1", "tl-1")

        assert result is attached_chat
        repo.attach_to_chain.assert_awaited_once_with("c1", "tl-1")
        repo.set_project_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_attach_settles_null_project_id_from_task_link(self):
        from backend.models.domain import TaskLink

        repo = _mock_repo()
        chat = Chat(
            id="c1",
            project_id=None,
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
        )
        repo.get.return_value = chat
        task_link = TaskLink(
            id="tl-1",
            project_id="proj-b",
            repo_path="/repos/test",
            story_node_id="1-1",
            depends_on=[],
            job_id=None,
            tracker_ticket_ref=None,
            prompt_override=None,
            epic_id=None,
            created_at=None,  # type: ignore[arg-type]
            updated_at=None,  # type: ignore[arg-type]
        )
        task_link_repo = AsyncMock()
        task_link_repo.get.return_value = task_link
        repo.attach_to_chain.return_value = chat
        service = ChatService(repo, task_link_repo=task_link_repo)

        await service.attach_to_chain("c1", "tl-1")

        repo.set_project_id.assert_awaited_once_with("c1", "proj-b")


class TestChatServiceDetachFromChain:
    @pytest.mark.asyncio
    async def test_detach_returns_none_for_missing_chat(self):
        repo = _mock_repo()
        repo.detach_from_chain.return_value = None
        service = ChatService(repo)

        result = await service.detach_from_chain("missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_detach_clears_task_link_id(self):
        repo = _mock_repo()
        detached_chat = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
            task_link_id=None,
        )
        repo.detach_from_chain.return_value = detached_chat
        service = ChatService(repo)

        result = await service.detach_from_chain("c1")

        assert result is detached_chat
        assert result.task_link_id is None
        repo.detach_from_chain.assert_awaited_once_with("c1")


class TestChatServiceGetChainStatus:
    @pytest.mark.asyncio
    async def test_returns_none_for_missing_chat(self):
        repo = _mock_repo()
        repo.get.return_value = None
        task_link_repo = AsyncMock()
        service = ChatService(repo, task_link_repo=task_link_repo)

        result = await service.get_chain_status("missing")

        assert result is None
        task_link_repo.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_none_when_nothing_attached(self):
        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
            task_link_id=None,
        )
        task_link_repo = AsyncMock()
        service = ChatService(repo, task_link_repo=task_link_repo)

        result = await service.get_chain_status("c1")

        assert result is None
        task_link_repo.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reflects_task_link_with_no_job_yet(self):
        from backend.models.domain import TaskLink

        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
            task_link_id="tl-1",
        )
        task_link = TaskLink(
            id="tl-1",
            project_id="proj-a",
            repo_path="/repos/test",
            story_node_id="1-1",
            depends_on=[],
            job_id=None,
            tracker_ticket_ref=None,
            prompt_override=None,
            epic_id=None,
            created_at=None,  # type: ignore[arg-type]
            updated_at=None,  # type: ignore[arg-type]
        )
        task_link_repo = AsyncMock()
        task_link_repo.get.return_value = task_link
        job_repo = AsyncMock()
        service = ChatService(repo, task_link_repo=task_link_repo, job_repo=job_repo)

        status = await service.get_chain_status("c1")

        assert status is not None
        assert status.task_link_id == "tl-1"
        assert status.story_node_id == "1-1"
        assert status.repo_path == "/repos/test"
        assert status.job_id is None
        assert status.job_state is None
        job_repo.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reflects_job_state_when_spawned(self):
        from backend.models.domain import JobState, TaskLink

        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
            task_link_id="tl-1",
        )
        task_link = TaskLink(
            id="tl-1",
            project_id="proj-a",
            repo_path="/repos/test",
            story_node_id="1-1",
            depends_on=[],
            job_id="job-1",
            tracker_ticket_ref=None,
            prompt_override=None,
            epic_id=None,
            created_at=None,  # type: ignore[arg-type]
            updated_at=None,  # type: ignore[arg-type]
        )
        task_link_repo = AsyncMock()
        task_link_repo.get.return_value = task_link
        job_repo = AsyncMock()
        fake_job = type("FakeJob", (), {"state": JobState.running})()
        job_repo.get.return_value = fake_job
        service = ChatService(repo, task_link_repo=task_link_repo, job_repo=job_repo)

        status = await service.get_chain_status("c1")

        assert status is not None
        assert status.job_id == "job-1"
        assert status.job_state == JobState.running
        job_repo.get.assert_awaited_once_with("job-1")

    @pytest.mark.asyncio
    async def test_never_calls_job_creation_or_git(self):
        """AC 2: narration reflects state via read-only polling — it never
        calls GitService or the job-creation function itself."""
        from backend.models.domain import TaskLink

        repo = _mock_repo()
        repo.get.return_value = Chat(
            id="c1",
            project_id="proj-a",
            title="Hi",
            created_at=None,  # type: ignore[arg-type]
            last_message_at=None,  # type: ignore[arg-type]
            status="open",
            task_link_id="tl-1",
        )
        task_link = TaskLink(
            id="tl-1",
            project_id="proj-a",
            repo_path="/repos/test",
            story_node_id="1-1",
            depends_on=[],
            job_id=None,
            tracker_ticket_ref=None,
            prompt_override=None,
            epic_id=None,
            created_at=None,  # type: ignore[arg-type]
            updated_at=None,  # type: ignore[arg-type]
        )
        task_link_repo = AsyncMock()
        task_link_repo.get.return_value = task_link
        job_repo = AsyncMock()
        service = ChatService(repo, task_link_repo=task_link_repo, job_repo=job_repo)

        await service.get_chain_status("c1")

        # Only reads happened: no job/worktree creation call of any kind.
        job_repo.create.assert_not_called()
        assert not hasattr(service, "_job_service")


class TestChatIsGitFree:
    """Structural guard for AD-12/NFR8: chat_service.py must have zero
    GitService dependency — not merely unused, but structurally absent.
    """

    def test_chat_service_module_never_imports_git_service(self):
        import ast

        source = inspect.getsource(chat_service_module)
        tree = ast.parse(source)
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
                imported_names.update(alias.name for alias in node.names)

        assert not any("git" in name.lower() for name in imported_names), imported_names
        assert not hasattr(chat_service_module, "GitService")

    def test_chat_service_class_has_no_git_attribute(self):
        repo = _mock_repo()
        service = ChatService(repo)
        for attr_name in dir(service):
            assert "git" not in attr_name.lower()
