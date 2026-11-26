"""Explicit account bindings must persist, stay isolated, and never fall back."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import CPLConfig
from backend.models.db import Base
from backend.models.domain import JobSpec, SessionConfig
from backend.models.events import EventKind, new_event
from backend.persistence.credential_repo import CredentialRepository
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.adapters.inference_utility_adapter import InferenceUtilityAdapter
from backend.services.copilot_adapter._adapter import CopilotAdapter
from backend.services.inference_account import InferenceAccountError, inference_scope, resolve_inference_token
from backend.services.job.job_service import JobService


@pytest.fixture
async def accounts(monkeypatch):
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(
        "backend.persistence.credential_repo.encrypt_secret", lambda value: cipher.encrypt(value.encode()).decode()
    )
    monkeypatch.setattr(
        "backend.persistence.credential_repo.decrypt_secret", lambda value: cipher.decrypt(value.encode()).decode()
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await ProjectRepository(session).create("project", "Project", ["/repo"])
        credentials = CredentialRepository(session)
        for account, provider in [("a", "copilot"), ("b", "copilot"), ("tracker", "github")]:
            await credentials.create(
                credential_id=account,
                provider=provider,
                label=f"Label {account}",
                base_url="https://github.com",
                pat=f"secret-{account}",
            )
        service = JobService(
            job_repo=JobRepository(session),
            config=CPLConfig(repos=["/repo"]),
            git_service=AsyncMock(),
            project_repo=ProjectRepository(session),
            credential_repo=credentials,
        )
        for account in ("a", "b", None):
            await service.create_job(
                JobSpec(
                    repo="/repo",
                    prompt="Task",
                    base_ref="main",
                    title="Task",
                    worktree_name=f"job-{account}",
                    copilot_credential_id=account,
                )
            )
        await session.commit()
    yield factory
    await engine.dispose()


async def test_job_roundtrip_and_rerun_preserve_reference(accounts):
    async with accounts() as session:
        repo = JobRepository(session)
        job = await repo.get("job-a")
        assert job.copilot_credential_id == "a"
        assert job.copilot_credential_label == "Label a"
        service = JobService(
            job_repo=repo,
            config=CPLConfig(repos=["/repo"]),
            git_service=AsyncMock(),
            project_repo=ProjectRepository(session),
            credential_repo=CredentialRepository(session),
        )
        rerun = await service.rerun_job(job.id)
        assert rerun.copilot_credential_id == "a"
        assert rerun.copilot_credential_label == "Label a"
    assert await resolve_inference_token(accounts, job_id="job-a") == "secret-a"


@pytest.mark.parametrize("account,sdk", [("missing", "copilot"), ("tracker", "copilot"), ("a", "claude")])
async def test_creation_rejects_missing_wrong_provider_and_unsupported_sdk(accounts, account, sdk):
    async with accounts() as session:
        service = JobService(
            job_repo=JobRepository(session),
            config=CPLConfig(repos=["/repo"]),
            git_service=AsyncMock(),
            project_repo=ProjectRepository(session),
            credential_repo=CredentialRepository(session),
        )
        with pytest.raises(InferenceAccountError):
            await service.create_job(JobSpec(repo="/repo", prompt="Task", sdk=sdk, copilot_credential_id=account))


async def test_deleted_credential_fails_without_ambient_fallback(accounts):
    async with accounts() as session:
        await CredentialRepository(session).delete("a")
        await session.commit()
    with pytest.raises(InferenceAccountError, match="missing"):
        await resolve_inference_token(accounts, job_id="job-a")
    assert await resolve_inference_token(accounts, job_id="job-None") is None


@pytest.mark.parametrize("send_method", ["send_message", "send_peer_message"])
@pytest.mark.parametrize("account", ["a", None])
async def test_active_session_sends_revalidate_recipient_account(accounts, monkeypatch, send_method, account):
    session = MagicMock(session_id="live-session", send=AsyncMock(), abort=AsyncMock())
    client = MagicMock(start=AsyncMock(), stop=AsyncMock(), create_session=AsyncMock(return_value=session))
    monkeypatch.setattr("backend.services.copilot_adapter._adapter.create_copilot_client", lambda **kwargs: client)
    adapter = CopilotAdapter(session_factory=accounts)
    session_id = await adapter.create_session(
        SessionConfig(
            workspace_path="/repo",
            prompt="Initial task",
            job_id=f"job-{account}",
            copilot_credential_id=account,
        )
    )
    session.send.reset_mock()
    async with accounts() as db:
        await CredentialRepository(db).delete("a")
        await db.commit()
    try:
        # Sidecars and native peer tools may call from another job's account scope.
        with inference_scope(job_id="job-b", credential_id="b"):
            if account:
                with pytest.raises(InferenceAccountError, match="missing"):
                    await getattr(adapter, send_method)(session_id, "Later message")
                session.send.assert_not_awaited()
            else:
                await getattr(adapter, send_method)(session_id, "Later message")
                session.send.assert_awaited_once_with(
                    "Later message", mode="enqueue" if send_method == "send_peer_message" else "immediate"
                )
    finally:
        await adapter.abort_session(session_id)


async def test_binding_cannot_be_overridden(accounts):
    with pytest.raises(InferenceAccountError, match="Cannot change"):
        await resolve_inference_token(accounts, job_id="job-a", credential_id="b")


async def test_async_scopes_are_isolated_and_reset(accounts):
    barrier = asyncio.Barrier(2)

    async def resolve(job_id):
        with inference_scope(job_id=job_id):
            await barrier.wait()
            return await resolve_inference_token(accounts)

    assert await asyncio.gather(resolve("job-a"), resolve("job-b")) == ["secret-a", "secret-b"]
    assert await resolve_inference_token(accounts) is None
    with inference_scope(credential_id="a"):
        assert await resolve_inference_token(accounts, job_id="job-b") == "secret-b"
        assert await resolve_inference_token(accounts) == "secret-a"


async def test_utility_uses_copilot_for_bound_job_even_when_global_sdk_is_claude(accounts):
    copilot = MagicMock(complete=AsyncMock())
    claude = MagicMock(complete=AsyncMock())
    registry = MagicMock()
    registry.get_adapter.side_effect = lambda sdk: copilot if sdk == "copilot" else claude
    utility = InferenceUtilityAdapter(registry, "claude", accounts)
    with inference_scope(job_id="job-a"):
        await utility.complete("bound")
    await utility.complete("ambient")
    copilot.complete.assert_awaited_once()
    claude.complete.assert_awaited_once()


@pytest.mark.parametrize("ending", ["complete", "error", "close"])
async def test_utility_stream_releases_routing_and_aborts_once(accounts, ending):
    event = new_event(session_id="helper", kind=EventKind.message_assistant, payload={"content": "Answer"})

    async def events(session_id):
        yield event
        if ending == "error":
            raise RuntimeError("Stream failed")
        yield event

    adapter = MagicMock(create_session=AsyncMock(return_value="helper"), abort_session=AsyncMock())
    adapter.stream_events.side_effect = events
    registry = MagicMock()
    registry.get_adapter.return_value = adapter
    utility = InferenceUtilityAdapter(registry, "copilot", accounts)
    session_id = await utility.create_session(SessionConfig(workspace_path="/repo", prompt="Task"))
    async with aclosing(utility.stream_events(session_id)) as stream:
        assert await anext(stream) is event
        if ending == "error":
            with pytest.raises(RuntimeError, match="Stream failed"):
                await anext(stream)
        elif ending == "complete":
            assert [item async for item in stream] == [event]
    assert not utility._sessions
    await utility.abort_session(session_id)
    await utility.abort_session(session_id)
    adapter.abort_session.assert_awaited_once_with(session_id)


async def test_cancelled_utility_stream_releases_routing(accounts):
    entered = asyncio.Event()

    async def events(session_id):
        entered.set()
        await asyncio.Event().wait()
        yield new_event(session_id=session_id, kind=EventKind.message_assistant, payload={})

    adapter = MagicMock(create_session=AsyncMock(return_value="helper"), abort_session=AsyncMock())
    adapter.stream_events.side_effect = events
    registry = MagicMock()
    registry.get_adapter.return_value = adapter
    utility = InferenceUtilityAdapter(registry, "copilot", accounts)
    session_id = await utility.create_session(SessionConfig(workspace_path="/repo", prompt="Task"))

    async def consume():
        async for _ in utility.stream_events(session_id):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not utility._sessions
    adapter.abort_session.assert_awaited_once_with(session_id)
