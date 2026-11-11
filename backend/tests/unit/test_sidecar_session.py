"""Tests for backend.services.sidecar_session — named sessions, lifecycle, metrics.

Covers SidecarSession (wrapper), SidecarSessionManager named sidecar
management, warm/open/release lifecycle, windowed expiry, metrics,
and orphan cleanup.
"""

from __future__ import annotations

import asyncio
from collections import namedtuple
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.sidecar.session import (
    _DEFAULT_SYSTEM_PROMPT,
    SidecarSession,
    SidecarSessionManager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CompletionResult = namedtuple("_CompletionResult", ["text", "input_tokens", "output_tokens", "cost_usd"])


def _mock_adapter(text: str = "ok", latency: float = 0.0) -> MagicMock:
    """Return a mock adapter whose complete() returns a result namedtuple."""
    adapter = MagicMock()
    adapter.complete = AsyncMock(
        return_value=_CompletionResult(text=text, input_tokens=10, output_tokens=5, cost_usd=0.001)
    )
    return adapter


def _make_manager(adapter=None, pool_size: int = 2) -> SidecarSessionManager:
    """Create a SidecarSessionManager with a mock adapter."""
    if adapter is None:
        adapter = _mock_adapter()
    mgr = SidecarSessionManager(adapter, model="test-model", pool_size=pool_size)
    return mgr


@dataclass
class _FakeSidecarConfig:
    """Minimal stand-in for SidecarConfig in tests."""

    name: str = "test"
    phase: str = "midflight"
    lifetime: str = "persistent"
    system_prompt: str = "You are a test sidecar."
    max_turns: int | None = None
    timeout_s: float | None = None
    session_kind: str = "sidecar"


# ===================================================================
# SidecarSession wrapper
# ===================================================================


class TestRuntimeContract:
    @pytest.mark.asyncio
    async def test_custom_context_is_system_only_and_not_pooled(self) -> None:
        adapter = _mock_adapter("discussion")
        manager = _make_manager(adapter, pool_size=1)
        manager._fill_pool()
        generic = manager._pool[0]
        original_complete = adapter.complete

        async def complete(prompt, **kwargs):
            manager._pool.clear()
            return await original_complete(prompt, **kwargs)

        adapter.complete = complete
        error = None
        try:
            result = await manager.complete("user: hello", system_prompt="Discussion only")
        except TypeError as exc:
            error = exc
            result = None
        assert error is None, "Manager must accept a per-call system_prompt"
        assert result == "discussion"
        call = original_complete.call_args
        assert call.args == ("user: hello",)
        assert call.kwargs["system_message"] == "Discussion only"
        assert call.kwargs["excluded_tools"] == ["*"]
        assert not manager._pool, "A custom wrapper cannot refill the generic pool"
        adapter.complete = original_complete
        await manager.complete("title please")
        assert original_complete.call_args.kwargs["system_message"] == _DEFAULT_SYSTEM_PROMPT
        assert generic._system_prompt == _DEFAULT_SYSTEM_PROMPT

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure", [OSError("provider unavailable"), RuntimeError("provider failed"), TimeoutError()]
    )
    async def test_provider_failure_propagates_as_typed_error(self, failure: Exception) -> None:
        adapter = _mock_adapter()
        adapter.complete.side_effect = failure
        manager = _make_manager(adapter)
        error = None
        try:
            await manager.complete("title please")
        except Exception as exc:
            error = exc
        assert error is not None, "An empty string is not a successful provider response"
        assert type(error).__name__ == "UtilityCompletionError"
        assert manager.get_metrics()["global"]["totalCalls"] == 0


class TestSidecarSession:
    @pytest.mark.asyncio
    async def test_first_call_prepends_system_prompt(self) -> None:
        adapter = _mock_adapter()
        session = SidecarSession(adapter)
        await session.complete("hello")
        call_args = adapter.complete.call_args[0][0]
        assert _DEFAULT_SYSTEM_PROMPT in call_args
        assert "hello" in call_args

    @pytest.mark.asyncio
    async def test_second_call_no_system_prompt(self) -> None:
        adapter = _mock_adapter()
        session = SidecarSession(adapter)
        await session.complete("first")
        adapter.complete.reset_mock()
        await session.complete("second")
        call_args = adapter.complete.call_args[0][0]
        assert _DEFAULT_SYSTEM_PROMPT not in call_args
        assert call_args == "second"

    @pytest.mark.asyncio
    async def test_custom_system_prompt(self) -> None:
        adapter = _mock_adapter()
        session = SidecarSession(adapter, system_prompt="Custom prompt.")
        await session.complete("hello")
        call = adapter.complete.call_args
        assert call.args == ("hello",)
        assert call.kwargs["system_message"] == "Custom prompt."
        assert call.kwargs["excluded_tools"] == ["*"]

    @pytest.mark.asyncio
    async def test_metrics_updated(self) -> None:
        adapter = _mock_adapter()
        session = SidecarSession(adapter)
        await session.complete("test")
        assert session.call_count == 1
        assert session.total_input_tokens == 10
        assert session.total_output_tokens == 5
        assert session.total_cost_usd == 0.001
        assert session.last_call_at is not None
        assert session.total_latency_ms >= 0

    @pytest.mark.asyncio
    async def test_timeout_retries_once(self) -> None:
        adapter = MagicMock()
        call_count = 0

        async def flaky_complete(prompt: str, **kwargs) -> _CompletionResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(10)
            return _CompletionResult(text="ok", input_tokens=1, output_tokens=1, cost_usd=0)

        adapter.complete = flaky_complete
        session = SidecarSession(adapter)
        result = await session.complete("test", timeout=0.05)
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_timeout_exhausted_raises(self) -> None:
        adapter = MagicMock()

        async def slow_complete(prompt: str, **kwargs) -> _CompletionResult:
            await asyncio.sleep(10)
            return _CompletionResult(text="ok", input_tokens=0, output_tokens=0, cost_usd=0)

        adapter.complete = slow_complete
        session = SidecarSession(adapter)
        with pytest.raises(TimeoutError):
            await session.complete("test", timeout=0.05)

    def test_reset_metrics(self) -> None:
        adapter = _mock_adapter()
        session = SidecarSession(adapter)
        session.call_count = 5
        session.total_latency_ms = 100.0
        session.total_input_tokens = 50
        session.total_output_tokens = 25
        session.total_cost_usd = 0.01
        session.last_call_at = 123.0
        session._reset_metrics()
        assert session.call_count == 0
        assert session.total_latency_ms == 0.0
        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0
        assert session.total_cost_usd == 0.0
        assert session.last_call_at is None


# ===================================================================
# Windowed lifetime
# ===================================================================


class TestWindowedLifetime:
    def test_not_expired_by_default(self) -> None:
        session = SidecarSession(_mock_adapter())
        assert session.expired is False

    def test_expired_by_max_turns(self) -> None:
        session = SidecarSession(_mock_adapter(), max_turns=3)
        session.call_count = 3
        assert session.expired is True

    def test_not_expired_under_max_turns(self) -> None:
        session = SidecarSession(_mock_adapter(), max_turns=3)
        session.call_count = 2
        assert session.expired is False

    def test_expired_by_timeout(self) -> None:
        session = SidecarSession(_mock_adapter(), timeout_s=0.0)
        # timeout_s=0 means it expires immediately
        assert session.expired is True

    def test_not_expired_with_generous_timeout(self) -> None:
        session = SidecarSession(_mock_adapter(), timeout_s=9999)
        assert session.expired is False


# ===================================================================
# SidecarSessionManager — pool management
# ===================================================================


class TestPool:
    def test_pool_seeded_on_fill(self) -> None:
        mgr = _make_manager(pool_size=3)
        mgr._fill_pool()
        assert len(mgr._pool) == 3

    def test_pop_or_create_from_pool(self) -> None:
        mgr = _make_manager(pool_size=2)
        mgr._fill_pool()
        session = mgr._pop_or_create()
        assert isinstance(session, SidecarSession)
        assert len(mgr._pool) == 2  # refilled

    def test_pop_or_create_empty_pool(self) -> None:
        mgr = _make_manager(pool_size=0)
        session = mgr._pop_or_create()
        assert isinstance(session, SidecarSession)

    def test_pop_or_create_with_custom_config_skips_pool(self) -> None:
        mgr = _make_manager(pool_size=2)
        mgr._fill_pool()
        initial_pool = len(mgr._pool)
        session = mgr._pop_or_create(system_prompt="custom")
        assert session._system_prompt == "custom"
        # Pool was not depleted — a fresh session was created
        assert len(mgr._pool) >= initial_pool


# ===================================================================
# Warm / Release lifecycle
# ===================================================================


class TestWarmLifecycle:
    def test_warm_returns_token(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        token = mgr.warm()
        assert isinstance(token, str)
        assert len(token) > 0
        assert token in mgr._warm

    def test_release_returns_to_pool(self) -> None:
        mgr = _make_manager(pool_size=2)
        mgr._fill_pool()
        token = mgr.warm()
        initial_pool_size = len(mgr._pool)
        found = mgr.release(token)
        assert found is True
        assert token not in mgr._warm
        assert len(mgr._pool) >= initial_pool_size

    def test_release_unknown_token(self) -> None:
        mgr = _make_manager()
        found = mgr.release("nonexistent-token")
        assert found is False


# ===================================================================
# Named sidecar open / get / close
# ===================================================================


class TestNamedSidecars:
    def test_open_creates_session(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        session = mgr.open("job-1", "arbiter")
        assert isinstance(session, SidecarSession)
        assert mgr.get("job-1", "arbiter") is session

    def test_open_with_token_adopts(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        token = mgr.warm()
        warmed = mgr._warm[token]
        session = mgr.open("job-1", "arbiter", token=token)
        assert session is warmed
        assert token not in mgr._warm

    def test_open_with_expired_token_creates_new(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        session = mgr.open("job-1", "arbiter", token="expired-token")
        assert isinstance(session, SidecarSession)

    def test_open_with_config(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        cfg = _FakeSidecarConfig(system_prompt="Custom.", max_turns=5)
        session = mgr.open("job-1", "planner", config=cfg)
        assert session._system_prompt == "Custom."
        assert session._max_turns == 5

    def test_open_returns_existing_if_not_expired(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        s1 = mgr.open("job-1", "arbiter")
        s2 = mgr.open("job-1", "arbiter")
        assert s1 is s2

    def test_open_replaces_expired_session(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        cfg = _FakeSidecarConfig(max_turns=1)
        s1 = mgr.open("job-1", "windowed", config=cfg)
        s1.call_count = 1  # mark as expired
        assert s1.expired
        s2 = mgr.open("job-1", "windowed", config=cfg)
        assert s2 is not s1

    def test_get_returns_none_for_unknown_job(self) -> None:
        mgr = _make_manager()
        assert mgr.get("nope", "arbiter") is None

    def test_get_returns_none_for_unknown_name(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        mgr.open("job-1", "arbiter")
        assert mgr.get("job-1", "planner") is None

    def test_get_returns_none_and_removes_expired(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        cfg = _FakeSidecarConfig(max_turns=1)
        s = mgr.open("job-1", "windowed", config=cfg)
        s.call_count = 1
        result = mgr.get("job-1", "windowed")
        assert result is None
        # Session was cleaned up
        assert "windowed" not in mgr._jobs.get("job-1", {})

    def test_list_names(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        mgr.open("job-1", "arbiter")
        mgr.open("job-1", "planner")
        mgr.open("job-1", "enricher")
        names = mgr.list_names("job-1")
        assert set(names) == {"arbiter", "planner", "enricher"}

    def test_list_names_empty(self) -> None:
        mgr = _make_manager()
        assert mgr.list_names("nope") == []

    def test_close_single_sidecar(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        mgr.open("job-1", "arbiter")
        mgr.open("job-1", "planner")
        mgr.close("job-1", "arbiter")
        assert mgr.get("job-1", "arbiter") is None
        assert mgr.get("job-1", "planner") is not None

    def test_close_last_sidecar_removes_job(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        mgr.open("job-1", "only")
        mgr.close("job-1", "only")
        assert "job-1" not in mgr._jobs


# ===================================================================
# Close job and metrics
# ===================================================================


class TestCloseJobAndMetrics:
    def test_close_job_removes_all(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        mgr.open("j1", "a")
        mgr.open("j1", "b")
        mgr.close_job("j1")
        assert "j1" not in mgr._jobs

    def test_close_job_preserves_aggregated_metrics(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        s1 = mgr.open("j1", "arbiter")
        s2 = mgr.open("j1", "planner")
        s1.call_count = 3
        s1.total_latency_ms = 150.0
        s1.total_input_tokens = 30
        s1.total_output_tokens = 15
        s1.total_cost_usd = 0.003
        s2.call_count = 2
        s2.total_latency_ms = 100.0
        s2.total_input_tokens = 20
        s2.total_output_tokens = 10
        s2.total_cost_usd = 0.002

        mgr.close_job("j1")

        assert "j1" in mgr._closed_jobs
        snap = mgr._closed_jobs["j1"]
        assert snap["callCount"] == 5
        assert snap["inputTokens"] == 50
        assert snap["outputTokens"] == 25
        assert snap["costUsd"] == 0.005
        assert mgr._global_call_count == 5
        assert mgr._global_cost_usd == 0.005

    def test_close_job_nonexistent_noop(self) -> None:
        mgr = _make_manager()
        mgr.close_job("nope")  # should not raise

    def test_get_metrics_structure(self) -> None:
        mgr = _make_manager(pool_size=2)
        mgr._fill_pool()
        mgr.open("j1", "arbiter")
        metrics = mgr.get_metrics()
        assert "global" in metrics
        assert "jobs" in metrics
        assert "totalCalls" in metrics["global"]
        assert "activeJobs" in metrics["global"]
        assert "poolSize" in metrics["global"]

    def test_closed_jobs_capped(self) -> None:
        mgr = _make_manager()
        mgr._fill_pool()
        from backend.services.sidecar.session import _CLOSED_JOBS_MAX

        for i in range(_CLOSED_JOBS_MAX + 10):
            s = mgr.open(f"j-{i}", "test")
            s.call_count = 1
            s.total_latency_ms = 1.0
            mgr.close_job(f"j-{i}")

        assert len(mgr._closed_jobs) <= _CLOSED_JOBS_MAX


# ===================================================================
# Shutdown
# ===================================================================


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self) -> None:
        adapter = _mock_adapter()
        mgr = _make_manager(adapter=adapter)
        mgr._completer.close = AsyncMock()
        mgr._fill_pool()
        mgr.open("j1", "arbiter")
        _token = mgr.warm()

        await mgr.shutdown()

        assert len(mgr._pool) == 0
        assert len(mgr._warm) == 0
        assert len(mgr._jobs) == 0
        mgr._completer.close.assert_awaited_once()


# ===================================================================
# Model property
# ===================================================================


class TestModel:
    def test_model_property(self) -> None:
        mgr = _make_manager()
        assert mgr.model == "test-model"
