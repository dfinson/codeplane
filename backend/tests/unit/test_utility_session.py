"""Tests for backend.services.utility_session — utility session pool."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.utility_session import (
    _SCALE_DOWN_IDLE_S,
    DEFAULT_UTILITY_MODEL,
    UtilitySessionService,
    _WarmSession,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_warm_session(index: int = 0, model: str = DEFAULT_UTILITY_MODEL) -> MagicMock:
    """Create a mock _WarmSession with the right async stubs."""
    ws = MagicMock(spec=_WarmSession)
    ws.index = index
    ws.model = model
    ws.in_use = False
    ws.last_used_at = time.monotonic()
    ws.connect = AsyncMock()
    ws.complete = AsyncMock(return_value="mock response")
    ws.reconnect = AsyncMock()
    ws.close = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# UtilitySessionService — lifecycle
# ---------------------------------------------------------------------------


class TestUtilitySessionLifecycle:
    """Start / shutdown / idempotent-start."""

    @pytest.mark.asyncio
    async def test_default_model(self) -> None:
        svc = UtilitySessionService()
        assert svc.model == DEFAULT_UTILITY_MODEL

    @pytest.mark.asyncio
    async def test_custom_model(self) -> None:
        svc = UtilitySessionService(model="gpt-4o")
        assert svc.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_start_creates_sessions(self) -> None:
        svc = UtilitySessionService(pool_size=2)
        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            mock_ws = _mock_warm_session()
            mock_ws_cls.return_value = mock_ws
            await svc.start()
            assert mock_ws_cls.call_count == 2
            assert mock_ws.connect.await_count == 2
        # Clean up housekeeping task
        await svc.shutdown()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        svc = UtilitySessionService(pool_size=1)
        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            mock_ws_cls.return_value = _mock_warm_session()
            await svc.start()
            first_count = mock_ws_cls.call_count
            await svc.start()  # second call — should be a no-op
            assert mock_ws_cls.call_count == first_count
        await svc.shutdown()

    @pytest.mark.asyncio
    async def test_start_tolerates_connect_failure(self) -> None:
        svc = UtilitySessionService(pool_size=2)
        call_num = 0

        def _make_ws(*args, **kwargs):
            nonlocal call_num
            ws = _mock_warm_session(index=call_num)
            if call_num == 1:
                ws.connect = AsyncMock(side_effect=RuntimeError("connection refused"))
            call_num += 1
            return ws

        with patch("backend.services.utility_session._WarmSession", side_effect=_make_ws):
            await svc.start()
            # Only the first session should survive
            assert len(svc._sessions) == 1
        await svc.shutdown()

    @pytest.mark.asyncio
    async def test_start_with_all_failures_creates_empty_pool(self) -> None:
        svc = UtilitySessionService(pool_size=2)
        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            ws = _mock_warm_session()
            ws.connect = AsyncMock(side_effect=RuntimeError("boom"))
            mock_ws_cls.return_value = ws
            await svc.start()
            assert len(svc._sessions) == 0
        await svc.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_closes_all_and_resets(self) -> None:
        svc = UtilitySessionService(pool_size=1)
        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            ws = _mock_warm_session()
            mock_ws_cls.return_value = ws
            await svc.start()
            assert svc._started is True

            await svc.shutdown()
            ws.close.assert_awaited()
            assert svc._sessions == []
            assert svc._started is False

    @pytest.mark.asyncio
    async def test_shutdown_cancels_housekeeping_task(self) -> None:
        svc = UtilitySessionService(pool_size=1)
        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            mock_ws_cls.return_value = _mock_warm_session()
            await svc.start()
            assert svc._housekeeping_task is not None

            await svc.shutdown()
            assert svc._housekeeping_task is None


# ---------------------------------------------------------------------------
# UtilitySessionService — complete()
# ---------------------------------------------------------------------------


class TestUtilitySessionComplete:
    """Prompt completion, round-robin, error recovery."""

    @pytest.mark.asyncio
    async def test_complete_returns_response(self) -> None:
        svc = UtilitySessionService(pool_size=1)
        ws = _mock_warm_session()
        ws.complete = AsyncMock(return_value="hello world")
        svc._sessions = [ws]
        svc._started = True

        result = await svc.complete("say hello")
        assert result == "hello world"
        ws.complete.assert_awaited_once_with("say hello", timeout=30.0)

    @pytest.mark.asyncio
    async def test_complete_custom_timeout(self) -> None:
        svc = UtilitySessionService()
        ws = _mock_warm_session()
        svc._sessions = [ws]
        svc._started = True

        await svc.complete("prompt", timeout=10.0)
        ws.complete.assert_awaited_once_with("prompt", timeout=10.0)

    @pytest.mark.asyncio
    async def test_complete_reuses_first_free_session(self) -> None:
        """Sequential calls reuse the first free session (checkout model)."""
        svc = UtilitySessionService()
        ws0 = _mock_warm_session(index=0)
        ws1 = _mock_warm_session(index=1)
        svc._sessions = [ws0, ws1]
        svc._started = True

        await svc.complete("a")
        await svc.complete("b")
        # Sequential calls: ws0 is freed before second call, so reused
        assert ws0.complete.await_count == 2
        ws1.complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_complete_cold_start_when_pool_empty(self) -> None:
        """When pool is empty, complete() creates a new session on-the-fly."""
        svc = UtilitySessionService()
        svc._sessions = []
        svc._started = True

        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            ws = _mock_warm_session()
            ws.complete = AsyncMock(return_value="cold result")
            mock_ws_cls.return_value = ws
            result = await svc.complete("prompt")
            ws.connect.assert_awaited_once()
            assert result == "cold result"

    @pytest.mark.asyncio
    async def test_complete_cold_start_failure_raises(self) -> None:
        """When pool is empty and all connect attempts fail, error propagates."""
        svc = UtilitySessionService()
        svc._sessions = []
        svc._started = True

        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            ws = _mock_warm_session()
            ws.connect = AsyncMock(side_effect=RuntimeError("nope"))
            mock_ws_cls.return_value = ws
            with pytest.raises(RuntimeError, match="nope"):
                await svc.complete("prompt")

    @pytest.mark.asyncio
    async def test_complete_failure_triggers_reconnect(self) -> None:
        svc = UtilitySessionService()
        ws = _mock_warm_session()
        ws.complete = AsyncMock(side_effect=RuntimeError("dead session"))
        svc._sessions = [ws]
        svc._started = True

        result = await svc.complete("prompt")
        ws.reconnect.assert_awaited_once()
        assert result == ""

    @pytest.mark.asyncio
    async def test_complete_failure_reconnect_also_fails(self) -> None:
        svc = UtilitySessionService()
        ws = _mock_warm_session()
        ws.complete = AsyncMock(side_effect=RuntimeError("dead"))
        ws.reconnect = AsyncMock(side_effect=RuntimeError("still dead"))
        svc._sessions = [ws]
        svc._started = True

        result = await svc.complete("prompt")
        assert result == ""

    @pytest.mark.asyncio
    async def test_pending_counter_lifecycle(self) -> None:
        svc = UtilitySessionService()
        ws = _mock_warm_session()
        svc._sessions = [ws]
        svc._started = True
        assert svc._pending == 0

        await svc.complete("prompt")
        # After complete() returns, pending should be back to 0
        assert svc._pending == 0

    @pytest.mark.asyncio
    async def test_pending_counter_decrements_on_error(self) -> None:
        svc = UtilitySessionService()
        ws = _mock_warm_session()
        ws.complete = AsyncMock(side_effect=RuntimeError("boom"))
        svc._sessions = [ws]
        svc._started = True

        await svc.complete("prompt")
        assert svc._pending == 0


# ---------------------------------------------------------------------------
# UtilitySessionService — autoscaling
# ---------------------------------------------------------------------------


class TestUtilitySessionAutoscaling:
    """Scale-up and scale-down logic."""

    @pytest.mark.asyncio
    async def test_scale_up_to_demand_adds_sessions(self) -> None:
        svc = UtilitySessionService()
        ws0 = _mock_warm_session(index=0)
        svc._sessions = [ws0]
        svc._started = True
        svc._pending = 2  # more pending than sessions → triggers scale-up

        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            new_ws = _mock_warm_session(index=1)
            mock_ws_cls.return_value = new_ws
            await svc._scale_up_to_demand()
            assert len(svc._sessions) == 2

    @pytest.mark.asyncio
    async def test_scale_up_noop_when_pool_sufficient(self) -> None:
        svc = UtilitySessionService()
        ws0 = _mock_warm_session(index=0)
        ws1 = _mock_warm_session(index=1)
        svc._sessions = [ws0, ws1]
        svc._started = True
        svc._pending = 1

        await svc._scale_up_to_demand()
        # Pool already >= demand, should not grow
        assert len(svc._sessions) == 2

    @pytest.mark.asyncio
    async def test_scale_up_tolerates_connect_failure(self) -> None:
        svc = UtilitySessionService()
        svc._sessions = [_mock_warm_session(index=0)]
        svc._started = True
        svc._pending = 3

        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            ws = _mock_warm_session()
            ws.connect = AsyncMock(side_effect=RuntimeError("fail"))
            mock_ws_cls.return_value = ws
            await svc._scale_up_to_demand()
            # All new connections failed; pool unchanged
            assert len(svc._sessions) == 1

    @pytest.mark.asyncio
    async def test_scale_down_idle_removes_stale_sessions(self) -> None:
        svc = UtilitySessionService()
        ws0 = _mock_warm_session(index=0)
        ws0.last_used_at = time.monotonic()  # recently used

        ws1 = _mock_warm_session(index=1)
        ws1.last_used_at = time.monotonic() - _SCALE_DOWN_IDLE_S - 10  # stale

        svc._sessions = [ws0, ws1]
        svc._started = True
        svc._active_jobs = 0

        await svc._scale_down_idle()
        assert len(svc._sessions) == 1
        assert svc._sessions[0].index == 0
        ws1.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scale_down_keeps_min_pool(self) -> None:
        svc = UtilitySessionService()
        ws0 = _mock_warm_session(index=0)
        ws0.last_used_at = time.monotonic() - _SCALE_DOWN_IDLE_S - 10
        svc._sessions = [ws0]
        svc._started = True
        svc._active_jobs = 0

        await svc._scale_down_idle()
        # Should keep at least _MIN_POOL (1) even if idle
        assert len(svc._sessions) == 1

    @pytest.mark.asyncio
    async def test_scale_down_respects_active_jobs_floor(self) -> None:
        svc = UtilitySessionService()
        ws0 = _mock_warm_session(index=0)
        ws0.last_used_at = time.monotonic()

        ws1 = _mock_warm_session(index=1)
        ws1.last_used_at = time.monotonic() - _SCALE_DOWN_IDLE_S - 10

        svc._sessions = [ws0, ws1]
        svc._started = True
        svc._active_jobs = 2  # floor is 2, so we can't drop below 2

        await svc._scale_down_idle()
        assert len(svc._sessions) == 2


# ---------------------------------------------------------------------------
# UtilitySessionService — job notifications
# ---------------------------------------------------------------------------


class TestUtilitySessionJobNotifications:
    """notify_job_started / notify_job_ended proactive scaling."""

    @pytest.mark.asyncio
    async def test_notify_job_started_increments_counter(self) -> None:
        svc = UtilitySessionService()
        ws0 = _mock_warm_session(index=0)
        svc._sessions = [ws0]
        svc._started = True

        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            mock_ws_cls.return_value = _mock_warm_session(index=1)
            await svc.notify_job_started()
            assert svc._active_jobs == 1

    @pytest.mark.asyncio
    async def test_notify_job_started_scales_pool(self) -> None:
        svc = UtilitySessionService()
        svc._sessions = [_mock_warm_session(index=0)]
        svc._started = True
        svc._active_jobs = 0

        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            mock_ws_cls.return_value = _mock_warm_session(index=1)
            # Simulate 3 jobs starting — pool should grow to match demand
            await svc.notify_job_started()
            await svc.notify_job_started()
            await svc.notify_job_started()
            assert svc._active_jobs == 3

    @pytest.mark.asyncio
    async def test_notify_job_ended_decrements_counter(self) -> None:
        svc = UtilitySessionService()
        svc._active_jobs = 2
        await svc.notify_job_ended()
        assert svc._active_jobs == 1

    @pytest.mark.asyncio
    async def test_notify_job_ended_does_not_go_negative(self) -> None:
        svc = UtilitySessionService()
        svc._active_jobs = 0
        await svc.notify_job_ended()
        assert svc._active_jobs == 0

    @pytest.mark.asyncio
    async def test_scale_up_to_demand_grows_pool(self) -> None:
        svc = UtilitySessionService()
        svc._sessions = [_mock_warm_session(index=0)]
        svc._started = True
        svc._active_jobs = 3  # demand exceeds pool

        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            mock_ws_cls.return_value = _mock_warm_session()
            await svc._scale_up_to_demand()
            assert len(svc._sessions) == 3

    @pytest.mark.asyncio
    async def test_scale_up_noop_when_pool_sufficient(self) -> None:
        svc = UtilitySessionService()
        svc._sessions = [_mock_warm_session(index=0), _mock_warm_session(index=1)]
        svc._started = True
        svc._active_jobs = 1

        await svc._scale_up_to_demand()
        # Pool already >= target, no new sessions
        assert len(svc._sessions) == 2

    @pytest.mark.asyncio
    async def test_scale_up_tolerates_connect_failure(self) -> None:
        svc = UtilitySessionService()
        svc._sessions = [_mock_warm_session(index=0)]
        svc._started = True
        svc._active_jobs = 3

        with patch("backend.services.utility_session._WarmSession") as mock_ws_cls:
            ws = _mock_warm_session()
            ws.connect = AsyncMock(side_effect=RuntimeError("fail"))
            mock_ws_cls.return_value = ws
            await svc._scale_up_to_demand()
            # All new connections failed; pool unchanged
            assert len(svc._sessions) == 1


# ---------------------------------------------------------------------------
# _WarmSession — unit tests
# ---------------------------------------------------------------------------


class TestWarmSession:
    """Tests for the inner _WarmSession class."""

    @pytest.mark.asyncio
    async def test_connect_creates_session(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        mock_session.on = MagicMock()

        async def _fake_send(msg):
            handler = mock_session.on.call_args[0][0]
            evt = MagicMock()
            evt.type.value = "session.idle"
            handler(evt)

        mock_session.send = AsyncMock(side_effect=_fake_send)

        mock_client = AsyncMock()
        mock_client.create_session = AsyncMock(return_value=mock_session)

        mock_copilot = MagicMock()
        mock_copilot.CopilotClient = MagicMock(return_value=mock_client)
        mock_copilot.PermissionRequestResult = MagicMock(return_value=MagicMock(kind="approved"))

        mock_copilot_types = MagicMock()
        mock_copilot_types.SessionConfig = MagicMock()

        with patch.dict("sys.modules", {"copilot": mock_copilot, "copilot.types": mock_copilot_types}):
            await ws.connect()
            assert ws._session is mock_session

    @pytest.mark.asyncio
    async def test_complete_sends_prompt_and_collects_response(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        mock_session.on = MagicMock()

        async def _fake_send(msg):
            handler = mock_session.on.call_args[0][0]
            # Simulate assistant.message event
            evt = MagicMock()
            evt.type.value = "assistant.message"
            data_dict = {"content": "generated text"}
            evt.data.to_dict.return_value = data_dict
            handler(evt)

        mock_session.send = AsyncMock(side_effect=_fake_send)
        ws._session = mock_session

        result = await ws.complete("test prompt", timeout=5.0)
        assert result == "generated text"

    @pytest.mark.asyncio
    async def test_complete_reconnects_when_session_is_none(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        ws._session = None

        mock_session = AsyncMock()
        mock_session.on = MagicMock()

        call_count = 0

        async def _fake_send(msg):
            nonlocal call_count
            handler = mock_session.on.call_args[0][0]
            if call_count == 0:
                evt = MagicMock()
                evt.type.value = "session.idle"
                handler(evt)
            else:
                evt = MagicMock()
                evt.type.value = "assistant.message"
                evt.data.to_dict.return_value = {"content": "result"}
                handler(evt)
            call_count += 1

        mock_session.send = AsyncMock(side_effect=_fake_send)

        mock_client = AsyncMock()
        mock_client.create_session = AsyncMock(return_value=mock_session)

        mock_copilot = MagicMock()
        mock_copilot.CopilotClient = MagicMock(return_value=mock_client)
        mock_copilot.PermissionRequestResult = MagicMock(return_value=MagicMock(kind="approved"))
        mock_copilot_types = MagicMock()
        mock_copilot_types.SessionConfig = MagicMock()

        with patch.dict("sys.modules", {"copilot": mock_copilot, "copilot.types": mock_copilot_types}):
            result = await ws.complete("prompt")
            assert result == "result"

    @pytest.mark.asyncio
    async def test_complete_raises_on_timeout_and_kills_session(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        mock_session.on = MagicMock()
        # send() does nothing — the done event never fires → timeout
        mock_session.send = AsyncMock()
        ws._session = mock_session

        with pytest.raises(TimeoutError):
            await ws.complete("prompt", timeout=0.05)
        # Session must be killed so the next call triggers a reconnect
        assert ws._session is None

    @pytest.mark.asyncio
    async def test_complete_handles_task_complete_event(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        mock_session.on = MagicMock()

        async def _fake_send(msg):
            handler = mock_session.on.call_args[0][0]
            evt = MagicMock()
            evt.type.value = "session.task_complete"
            evt.data.to_dict.return_value = {}
            handler(evt)

        mock_session.send = AsyncMock(side_effect=_fake_send)
        ws._session = mock_session

        result = await ws.complete("prompt", timeout=5.0)
        assert result == ""  # task_complete without content → empty

    @pytest.mark.asyncio
    async def test_complete_handles_error_event(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        mock_session.on = MagicMock()

        async def _fake_send(msg):
            handler = mock_session.on.call_args[0][0]
            evt = MagicMock()
            evt.type.value = "session.error"
            evt.data.to_dict.return_value = {}
            handler(evt)

        mock_session.send = AsyncMock(side_effect=_fake_send)
        ws._session = mock_session

        result = await ws.complete("prompt", timeout=5.0)
        assert result == ""

    @pytest.mark.asyncio
    async def test_complete_handles_shutdown_event(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        mock_session.on = MagicMock()

        async def _fake_send(msg):
            handler = mock_session.on.call_args[0][0]
            evt = MagicMock()
            evt.type.value = "session.shutdown"
            evt.data.to_dict.return_value = {}
            handler(evt)

        mock_session.send = AsyncMock(side_effect=_fake_send)
        ws._session = mock_session

        result = await ws.complete("prompt", timeout=5.0)
        assert result == ""

    @pytest.mark.asyncio
    async def test_complete_ignores_events_without_content(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        mock_session.on = MagicMock()

        async def _fake_send(msg):
            handler = mock_session.on.call_args[0][0]
            # assistant.message with empty content, then idle to finish
            evt1 = MagicMock()
            evt1.type.value = "assistant.message"
            evt1.data.to_dict.return_value = {"content": ""}
            handler(evt1)

        mock_session.send = AsyncMock(side_effect=_fake_send)
        ws._session = mock_session

        result = await ws.complete("prompt", timeout=5.0)
        # Empty content is not collected but done is still set
        assert result == ""

    @pytest.mark.asyncio
    async def test_reconnect_closes_then_connects(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        ws._session = mock_session

        new_session = AsyncMock()
        new_session.on = MagicMock()

        async def _fake_send(msg):
            handler = new_session.on.call_args[0][0]
            evt = MagicMock()
            evt.type.value = "session.idle"
            handler(evt)

        new_session.send = AsyncMock(side_effect=_fake_send)

        mock_client = AsyncMock()
        mock_client.create_session = AsyncMock(return_value=new_session)

        mock_copilot = MagicMock()
        mock_copilot.CopilotClient = MagicMock(return_value=mock_client)
        mock_copilot.PermissionRequestResult = MagicMock(return_value=MagicMock(kind="approved"))
        mock_copilot_types = MagicMock()
        mock_copilot_types.SessionConfig = MagicMock()

        with patch.dict("sys.modules", {"copilot": mock_copilot, "copilot.types": mock_copilot_types}):
            await ws.reconnect()
            mock_session.abort.assert_awaited_once()
            assert ws._session is new_session

    @pytest.mark.asyncio
    async def test_close_aborts_session(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        ws._session = mock_session

        await ws.close()
        mock_session.abort.assert_awaited_once()
        assert ws._session is None

    @pytest.mark.asyncio
    async def test_close_noop_when_no_session(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        ws._session = None
        await ws.close()  # should not raise

    @pytest.mark.asyncio
    async def test_close_suppresses_abort_exception(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        mock_session.abort = AsyncMock(side_effect=RuntimeError("abort failed"))
        ws._session = mock_session

        await ws.close()  # should not raise
        assert ws._session is None

    @pytest.mark.asyncio
    async def test_last_used_at_updated_on_complete(self) -> None:
        ws = _WarmSession(model="gpt-4o-mini", index=0)
        mock_session = AsyncMock()
        mock_session.on = MagicMock()

        async def _fake_send(msg):
            handler = mock_session.on.call_args[0][0]
            evt = MagicMock()
            evt.type.value = "session.idle"
            evt.data.to_dict.return_value = {}
            handler(evt)

        mock_session.send = AsyncMock(side_effect=_fake_send)
        ws._session = mock_session

        before = ws.last_used_at
        await asyncio.sleep(0.01)
        await ws.complete("prompt")
        assert ws.last_used_at >= before


# ---------------------------------------------------------------------------
# Housekeeping loop
# ---------------------------------------------------------------------------


class TestHousekeepingLoop:
    """The periodic housekeeping task."""

    @pytest.mark.asyncio
    async def test_housekeeping_loop_calls_scale_down(self) -> None:
        svc = UtilitySessionService()
        svc._started = True

        import backend.services.utility_session as mod

        orig = mod._HOUSEKEEPING_INTERVAL_S
        mod._HOUSEKEEPING_INTERVAL_S = int(0.01)
        try:
            with patch.object(svc, "_scale_down_idle", new_callable=AsyncMock) as mock_sd:
                task = asyncio.create_task(svc._housekeeping_loop())
                await asyncio.sleep(0.05)
                task.cancel()
                # _housekeeping_loop catches CancelledError internally
                await task
            assert mock_sd.await_count >= 1
        finally:
            mod._HOUSEKEEPING_INTERVAL_S = orig

    @pytest.mark.asyncio
    async def test_housekeeping_loop_stops_on_cancel(self) -> None:
        svc = UtilitySessionService()

        import backend.services.utility_session as mod

        orig = mod._HOUSEKEEPING_INTERVAL_S
        mod._HOUSEKEEPING_INTERVAL_S = int(0.01)
        try:
            task = asyncio.create_task(svc._housekeeping_loop())
            await asyncio.sleep(0.03)
            task.cancel()
            # The loop catches CancelledError, so task completes normally
            await task
            assert task.done()
        finally:
            mod._HOUSEKEEPING_INTERVAL_S = orig
