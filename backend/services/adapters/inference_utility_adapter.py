"""Route explicitly bound job helpers through Copilot, regardless of the global SDK."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.services.adapters.agent_adapter import AgentAdapterInterface, CompletionResult, UtilityCompletionError
from backend.services.inference_account import InferenceAccountError, resolve_inference_token

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.domain import SessionConfig
    from backend.models.events import SessionEvent
    from backend.services.adapters.adapter_registry import AdapterRegistry


class InferenceUtilityAdapter(AgentAdapterInterface):
    def __init__(
        self,
        registry: AdapterRegistry,
        default_sdk: str,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        utility_sdk: str | None = None,
    ) -> None:
        self._registry = registry
        self._default_sdk = default_sdk
        self._utility_sdk = utility_sdk or default_sdk
        self._session_factory = session_factory
        self._sessions: dict[str, AgentAdapterInterface] = {}

    async def _adapter(self, config: SessionConfig | None = None) -> AgentAdapterInterface:
        token = await resolve_inference_token(
            self._session_factory,
            job_id=config.job_id if config else None,
            credential_id=config.copilot_credential_id if config else None,
        )
        return self._registry.get_adapter("copilot" if token else self._default_sdk)

    async def create_session(self, config: SessionConfig) -> str:
        adapter = await self._adapter(config)
        session_id = await adapter.create_session(config)
        self._sessions[session_id] = adapter
        return session_id

    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        try:
            async for event in self._sessions[session_id].stream_events(session_id):
                yield event
        finally:
            await self.abort_session(session_id)

    async def send_message(self, session_id: str, message: str) -> None:
        await self._sessions[session_id].send_message(session_id, message)

    async def abort_session(self, session_id: str) -> None:
        adapter = self._sessions.pop(session_id, None)
        if adapter:
            await adapter.abort_session(session_id)

    async def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_message: str | None = None,
        excluded_tools: list[str] | None = None,
    ) -> CompletionResult:
        from backend.services.adapters.completion_context import current_completion_context

        try:
            token = await resolve_inference_token(self._session_factory, job_id=current_completion_context().job_id)
        except InferenceAccountError as exc:
            raise UtilityCompletionError(str(exc), reason="inference_account_unavailable") from None
        provider = self._registry.get_completion_provider("copilot" if token else self._utility_sdk)
        return await provider.complete(
            prompt, model=model, system_message=system_message, excluded_tools=excluded_tools
        )

    def set_policy_router(self, *args: Any, **kwargs: Any) -> None:
        for sdk in {self._default_sdk, "copilot"}:
            self._registry.get_adapter(sdk).set_policy_router(*args, **kwargs)  # type: ignore[attr-defined]
