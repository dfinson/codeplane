"""Chat service — owns ``ChatRow`` lifecycle.

AD-12 / NFR8: a Chat is a persistent, purely conversational entity with
zero git footprint. This module must never import ``GitService`` or any
git-touching module — that is a structural, not behavioral, guarantee.
Read-only repo context (if ever needed by a later story) must go through
existing workspace read tools, never a git write path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import (
    Chat,
    ChatChainStatus,
    ChatMessage,
    ChatTurnResult,
    JobSpec,
    ProjectNotFoundError,
    StateConflictError,
    TaskLinkNotFoundError,
)

if TYPE_CHECKING:
    from backend.models.domain import Job
    from backend.persistence.chat_repo import ChatRepository
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.project_repo import ProjectRepository
    from backend.persistence.task_link_repo import TaskLinkRepository
    from backend.services.job.job_service import JobService
    from backend.services.sidecar.session import SidecarSessionManager

log = structlog.get_logger()

_CHAT_SYSTEM_PROMPT = """\
You are a conversational coding assistant inside CodePlane. Help the user think
through software ideas and decisions. This is a discussion-only Chat: do not
claim to inspect files, run commands, change code, or create a worktree. Keep
the response concise and useful."""


class ChatService:
    """Manages the lifecycle of persistent, git-free chats."""

    def __init__(
        self,
        repo: ChatRepository,
        task_link_repo: TaskLinkRepository | None = None,
        job_repo: JobRepository | None = None,
        project_repo: ProjectRepository | None = None,
        completer: SidecarSessionManager | None = None,
    ) -> None:
        self._repo = repo
        self._task_link_repo = task_link_repo
        self._job_repo = job_repo
        self._project_repo = project_repo
        self._completer = completer

    async def create_chat(self, *, title: str, project_id: str | None = None) -> Chat:
        """Create a new Chat.

        ``project_id`` defaults to whatever the caller passes — the API
        layer resolves this from UI context (a Project's id if started
        from within one, ``None`` from global nav) — and is always
        user-overridable at creation time.
        """
        if (
            project_id is not None
            and self._project_repo is not None
            and await self._project_repo.get(project_id) is None
        ):
            raise ProjectNotFoundError(f"Project '{project_id}' does not exist.")
        now = datetime.now(UTC)
        chat = Chat(
            id=str(uuid.uuid4()),
            project_id=project_id,
            title=title,
            created_at=now,
            last_message_at=now,
            status="open",
        )
        return await self._repo.create(chat)

    async def get_chat(self, chat_id: str) -> Chat | None:
        """Get a single chat by ID."""
        return await self._repo.get(chat_id)

    async def list_chats(self, project_id: str | None = None) -> list[Chat]:
        """List all chats."""
        if project_id is None:
            return await self._repo.list_all()
        if self._project_repo is not None and await self._project_repo.get(project_id) is None:
            raise ProjectNotFoundError(f"Project '{project_id}' does not exist.")
        return await self._repo.list_by_project(project_id)

    async def add_message(self, chat_id: str, *, role: str, content: str) -> ChatMessage | None:
        """Append a message to a Chat's transcript.

        Returns ``None`` if the chat does not exist. Bumps
        ``ChatRow.last_message_at`` as a side effect of persistence.
        """
        chat = await self._repo.get(chat_id)
        if chat is None:
            return None
        message = ChatMessage(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            role=role,
            content=content,
            created_at=datetime.now(UTC),
        )
        return await self._repo.add_message(message)

    async def list_messages(self, chat_id: str) -> list[ChatMessage]:
        if await self._repo.get(chat_id) is None:
            return []
        return await self._repo.list_messages(chat_id)

    async def begin_turn(self, chat_id: str, *, content: str) -> ChatMessage | None:
        """Persist the user half of a turn before any provider I/O."""
        return await self.add_message(chat_id, role="user", content=content)

    async def complete_turn(
        self,
        chat_id: str,
        *,
        user_message: ChatMessage,
    ) -> ChatTurnResult:
        """Request and persist the assistant half after the user commit."""
        transcript = await self.build_transcript(chat_id)
        prompt = f"Conversation:\n{transcript or ''}\nAssistant:"
        try:
            if self._completer is None:
                raise RuntimeError("Chat completion is unavailable")
            assistant_content = (await self._completer.complete(prompt, system_prompt=_CHAT_SYSTEM_PROMPT)).strip()
            if not assistant_content:
                raise RuntimeError("The assistant returned an empty response")
        except Exception:
            log.warning("chat_completion_failed", chat_id=chat_id, exc_info=True)
            return ChatTurnResult(
                user_message=user_message,
                assistant_message=None,
                state="error",
                error="The assistant could not respond. Your message was saved; try again.",
            )

        assistant_message = await self.add_message(chat_id, role="assistant", content=assistant_content)
        assert assistant_message is not None
        return ChatTurnResult(
            user_message=user_message,
            assistant_message=assistant_message,
            state="assistant",
        )

    async def build_transcript(self, chat_id: str) -> str | None:
        """Concatenate a Chat's messages, role-prefixed, in transcript order.

        Returns ``None`` if the chat does not exist. An empty transcript
        (a chat with no messages yet) is returned as ``""``, not an error —
        launching a Job from a fresh Chat is still valid.
        """
        chat = await self._repo.get(chat_id)
        if chat is None:
            return None
        messages = await self._repo.list_messages(chat_id)
        return "\n".join(f"{m.role}: {m.content}" for m in messages)

    async def launch_job(
        self,
        chat_id: str,
        job_service: JobService,
        *,
        repo: str,
        base_ref: str | None = None,
        branch: str | None = None,
        model: str | None = None,
        sdk: str | None = None,
    ) -> Job | None:
        """Launch a new Job seeded from this Chat's transcript (AD-12/CAP-12, Story 5.2).

        Delegates job/worktree/branch creation entirely to ``JobService``
        (the same job-creation function AD-10 uses for ``spawn_task``) —
        this module never provisions git state itself. Returns ``None`` if
        the chat does not exist.

        The Chat itself is never consumed or transformed: it remains
        ``"open"`` and can launch further, independent Jobs later. If its
        ``project_id`` is still null, it is settled from ``repo`` at this
        call (no ``ProjectRow``/Project registry exists yet in this
        codebase, so ``project_id`` remains a plain nullable string).
        """
        chat = await self._repo.get(chat_id)
        if chat is None:
            return None

        transcript = await self.build_transcript(chat_id)
        assert transcript is not None  # chat existence already checked above

        owner = None
        if self._project_repo is not None:
            owner = await self._project_repo.find_by_repo_path(str(Path(repo).expanduser().resolve()))
            if owner is None:
                raise StateConflictError("The selected repository does not belong to a Project.")
            if chat.project_id is not None and owner.id != chat.project_id:
                raise StateConflictError("The selected repository belongs to a different Project.")

        spec = JobSpec(
            repo=repo,
            prompt=transcript,
            base_ref=base_ref,
            branch=branch,
            model=model,
            sdk=sdk,
        )
        job = await job_service.create_job(spec)

        if chat.project_id is None and owner is not None:
            await self._repo.set_project_id(chat_id, owner.id)

        return job

    async def attach_to_chain(self, chat_id: str, task_link_id: str) -> Chat | None:
        """Attach a Chat to a Task Recipe chain via its entry ``TaskLink`` (Story 5.3).

        Links the Chat to ``task_link_id``. If ``chat.project_id`` is still
        null, it is settled from the TaskLink's own Project at this moment
        (whichever of launch-job/attach-chain happens first settles it).
        Returns ``None`` if the chat does not exist; raises
        ``TaskLinkNotFoundError`` if the TaskLink does not exist. This
        method never touches ``GitService`` or creates a Job — attaching is
        a pure linking operation.
        """
        assert self._task_link_repo is not None  # required collaborator for this operation
        chat = await self._repo.get(chat_id)
        if chat is None:
            return None

        task_link = await self._task_link_repo.get(task_link_id)
        if task_link is None:
            raise TaskLinkNotFoundError(f"TaskLink '{task_link_id}' does not exist.")
        if chat.project_id is not None and chat.project_id != task_link.project_id:
            raise StateConflictError("The TaskLink belongs to a different Project.")

        if chat.project_id is None:
            await self._repo.set_project_id(chat_id, task_link.project_id)

        return await self._repo.attach_to_chain(chat_id, task_link_id)

    async def detach_from_chain(self, chat_id: str) -> Chat | None:
        """Detach a Chat from its Task Recipe chain (Story 5.3).

        The chain and its TaskLinks continue to exist and run exactly as
        before; only the Chat's link to it is cleared. Returns ``None`` if
        the chat does not exist.
        """
        return await self._repo.detach_from_chain(chat_id)

    async def get_chain_status(self, chat_id: str) -> ChatChainStatus | None:
        """Read-only narration snapshot of a Chat's attached chain (Story 5.3, AC 2).

        Reflects the attached TaskLink's (and, if spawned, its Job's) state
        purely by reading existing repositories — it never calls
        ``GitService`` or any job-creation function itself. Returns ``None``
        if the chat does not exist or has nothing attached.
        """
        assert self._task_link_repo is not None  # required collaborator for this operation
        chat = await self._repo.get(chat_id)
        if chat is None or chat.task_link_id is None:
            return None

        task_link = await self._task_link_repo.get(chat.task_link_id)
        if task_link is None:
            return None

        job_state: str | None = None
        if task_link.job_id is not None and self._job_repo is not None:
            job = await self._job_repo.get(task_link.job_id)
            if job is not None:
                job_state = job.state

        return ChatChainStatus(
            task_link_id=task_link.id,
            story_node_id=task_link.story_node_id,
            repo_path=task_link.repo_path,
            job_id=task_link.job_id,
            job_state=job_state,
        )
