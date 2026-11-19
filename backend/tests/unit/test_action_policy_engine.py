"""Application-owned policy reload and preserved approval-batcher behavior."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from backend.models.events import EventKind, SessionEvent, new_event
from backend.services.action_policy.batcher import ApprovalBatcher
from backend.services.action_policy.classifier import Preset
from backend.services.events.event_bus import EventBus

# ---------------------------------------------------------------------------
# ApprovalBatcher.set_batch_window
# ---------------------------------------------------------------------------


class TestBatcherSetWindow:
    def test_set_batch_window_updates_value(self) -> None:
        bus = EventBus()
        batcher = ApprovalBatcher(event_bus=bus, batch_window_seconds=5.0)
        assert batcher._batch_window == 5.0
        batcher.set_batch_window(10.0)
        assert batcher._batch_window == 10.0

    def test_set_batch_window_affects_new_batches(self) -> None:
        bus = EventBus()
        batcher = ApprovalBatcher(event_bus=bus, batch_window_seconds=5.0)
        batcher.set_batch_window(2.0)
        assert batcher._batch_window == 2.0


# ---------------------------------------------------------------------------
# _on_policy_settings_changed
# ---------------------------------------------------------------------------


class TestOnPolicySettingsChanged:
    """Tests for RuntimeService._on_policy_settings_changed (governance rewire)."""

    async def test_ignores_non_policy_events(self) -> None:
        svc = _make_runtime_service()
        event = new_event(session_id="j1", kind=EventKind.job_created, payload={})
        await svc._on_policy_settings_changed(event)
        svc._session_factory.assert_not_called()

    async def test_no_op_when_no_running_jobs(self) -> None:
        svc = _make_runtime_service()
        event = _make_policy_event()
        # Empty _policy_routers → returns before any DB access or rebuild.
        await svc._on_policy_settings_changed(event)
        svc._session_factory.assert_not_called()

    async def test_reloads_policy_for_running_jobs(self) -> None:
        svc = _make_runtime_service()

        mock_router = MagicMock()
        svc._policy_routers["job-1"] = mock_router

        mock_batcher = MagicMock()
        svc._policy_batchers["job-1"] = mock_batcher

        mock_adapter = MagicMock()
        svc._adapter_registry._adapters = {"claude": mock_adapter}

        mock_repo = _make_policy_repo(preset="locked", batch_window=3.0)
        mock_job_repo = _make_job_repo(preset="locked")

        with (
            patch("backend.persistence.policy_repo.PolicyRepository", return_value=mock_repo),
            patch("backend.persistence.job_repo.JobRepository", return_value=mock_job_repo),
        ):
            svc._session_factory = _make_session_factory(AsyncMock())
            await svc._on_policy_settings_changed(_make_policy_event())

        mock_repo.get_usd_ceilings.assert_not_awaited()
        # Batcher window updated + adapter policy pushed with the new preset.
        mock_batcher.set_batch_window.assert_called_once_with(3.0)
        mock_adapter.update_repo_policy.assert_called_once()
        call_args = mock_adapter.update_repo_policy.call_args
        assert call_args[0][0] == "job-1"
        policy = call_args[0][1]
        assert policy.preset == Preset.locked

    async def test_skips_jobs_that_finish_mid_reload(self) -> None:
        svc = _make_runtime_service()

        mock_adapter = MagicMock()
        svc._adapter_registry._adapters = {"claude": mock_adapter}
        svc._policy_routers["job-1"] = MagicMock()

        mock_repo = _make_policy_repo(preset="supervised", batch_window=5.0)

        # The per-job preset lookup removes the job from the registry mid-handler
        # (it finished) — the enforcement loop must then skip it.
        def _pop_job(jid: str) -> Any:
            svc._policy_routers.pop(jid, None)
            job = MagicMock()
            job.preset = "supervised"
            return job

        mock_job_repo = MagicMock()
        mock_job_repo.get = AsyncMock(side_effect=_pop_job)

        with (
            patch("backend.persistence.policy_repo.PolicyRepository", return_value=mock_repo),
            patch("backend.persistence.job_repo.JobRepository", return_value=mock_job_repo),
        ):
            svc._session_factory = _make_session_factory(AsyncMock())
            await svc._on_policy_settings_changed(_make_policy_event())

        mock_adapter.update_repo_policy.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy_event() -> SessionEvent:
    return new_event(session_id="", kind=EventKind.policy_settings_changed, payload={})


def _make_policy_repo(*, preset: str, batch_window: float) -> MagicMock:
    """A PolicyRepository stub for the slimmed reload (config + mcp + usd only)."""
    repo = MagicMock()
    repo.get_config = AsyncMock(return_value={"preset": preset, "batch_window_seconds": batch_window})
    repo.list_mcp_configs = AsyncMock(return_value=[])
    repo.get_usd_ceilings = AsyncMock(return_value={})
    return repo


def _make_job_repo(*, preset: str) -> MagicMock:
    job = MagicMock()
    job.preset = preset
    repo = MagicMock()
    repo.get = AsyncMock(return_value=job)
    return repo


def _make_session_factory(mock_session: AsyncMock) -> Any:
    """Create a mock async session factory."""
    factory = MagicMock()

    class _FakeCtx:
        async def __aenter__(self) -> AsyncMock:
            return mock_session

        async def __aexit__(self, *args: Any) -> None:
            pass

    factory.return_value = _FakeCtx()
    factory.side_effect = None
    return factory


def _make_runtime_service() -> Any:
    """Create a minimal RuntimeService for testing event handlers."""
    from backend.services.runtime import RuntimeService

    svc = object.__new__(RuntimeService)
    svc._event_bus = EventBus()
    svc._policy_routers = {}
    svc._repo_policies = {}
    svc._policy_batchers = {}
    svc._adapter_registry = MagicMock()
    svc._adapter_registry._adapters = {}
    svc._session_factory = MagicMock()
    svc._standalone_governance = None
    return svc
