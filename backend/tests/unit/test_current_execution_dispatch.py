"""Coordinated dispatch is fenced to an exact live execution."""

from unittest.mock import AsyncMock, MagicMock

from backend.config import CPLConfig
from backend.services.events.event_bus import EventBus
from backend.services.runtime import RuntimeService
from backend.tests.unit.test_approval_service import session_factory  # noqa: F401


class TestRuntimeContract:
    async def test_missing_execution_never_resumes(self, session_factory):
        runtime = RuntimeService(
            session_factory=session_factory,
            event_bus=EventBus(),
            adapter_registry=MagicMock(),
            config=CPLConfig(),
        )
        runtime._resume_orphaned = AsyncMock()
        dispatch = getattr(runtime, "dispatch_current_execution", None)
        assert dispatch is not None, "A distinct strict dispatch contract is required"
        result = await dispatch("job-1", "stale-token", "fixture")
        assert result.status == "conflict"
        assert result.reason == "no_live_execution"
        runtime._resume_orphaned.assert_not_awaited()
