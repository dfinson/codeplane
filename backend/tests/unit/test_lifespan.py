from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from backend.lifespan import (
    _RESTART_REQUEST_ID_ENV,
    _build_copilot_steer_client,
    _persist_event_with_retry,
    _publish_restart_readiness,
)
from backend.models.events import EventKind, SessionEvent, new_event

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_asgi_startup_refuses_failed_migration_before_database_consumers(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.lifespan as module

    class MigrationError(RuntimeError):
        pass

    def reject_migration() -> None:
        raise MigrationError("retirement blocked")

    def unexpected_engine() -> None:
        raise AssertionError("Database consumers started before the guarded migration")

    monkeypatch.setattr(module, "run_migrations", reject_migration, raising=False)
    monkeypatch.setattr(module, "create_engine", unexpected_engine)
    with pytest.raises(MigrationError, match="retirement blocked"):
        async with module.lifespan(None):
            raise AssertionError("Startup must not yield after failed migration")


@pytest.mark.asyncio
async def test_asgi_startup_upgrades_before_creating_database_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.lifespan as module

    order: list[str] = []

    class StopAfterEngineError(RuntimeError):
        pass

    def migrate() -> None:
        order.append("migrate")

    def engine() -> None:
        order.append("engine")
        raise StopAfterEngineError

    monkeypatch.setattr(module, "run_migrations", migrate, raising=False)
    monkeypatch.setattr(module, "create_engine", engine)
    with pytest.raises(StopAfterEngineError):
        async with module.lifespan(None):
            raise AssertionError("Probe stops at engine creation")
    assert order == ["migrate", "engine"]


class _FakeSession:
    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


class _FakeSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is not None:
            await self._session.rollback()
        else:
            await self._session.commit()
        return None


def _make_event() -> SessionEvent:
    return new_event(
        event_id="evt-1",
        session_id="job-1",
        timestamp=datetime.now(UTC),
        kind=EventKind.job_state_changed,
        payload={"state": "running"},
    )


@pytest.mark.parametrize("env_var", ["GITHUB_TOKEN", "GH_TOKEN"])
def test_build_copilot_steer_client_uses_github_token(monkeypatch: pytest.MonkeyPatch, env_var: str) -> None:
    class _FakeCopilotSteerClient:
        def __init__(self, github_token: str) -> None:
            self.github_token = github_token

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv(env_var, "steer-token")
    monkeypatch.setattr("backend.services.completers.copilot_steer.CopilotSteerClient", _FakeCopilotSteerClient)

    client = _build_copilot_steer_client()

    assert isinstance(client, _FakeCopilotSteerClient)
    assert client.github_token == "steer-token"


def test_build_copilot_steer_client_returns_none_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    class _UnexpectedCopilotSteerClient:
        def __init__(self, github_token: str) -> None:
            raise AssertionError("CopilotSteerClient should not be constructed without a token")

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr("backend.services.completers.copilot_steer.CopilotSteerClient", _UnexpectedCopilotSteerClient)

    assert _build_copilot_steer_client() is None


@pytest.mark.asyncio
async def test_persist_event_retries_sqlite_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions: list[_FakeSession] = []
    append_attempts = 0

    def _session_factory() -> _FakeSessionContext:
        session = _FakeSession()
        sessions.append(session)
        return _FakeSessionContext(session)

    class _FakeRepo:
        def __init__(self, session: _FakeSession) -> None:
            self._session = session

        async def append(self, event: SessionEvent) -> None:
            nonlocal append_attempts
            append_attempts += 1
            if append_attempts == 1:
                raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr("backend.lifespan.EventRepository", _FakeRepo)

    # Bypass the global write lock in tests by patching serialized_write
    # to just yield a session directly from the factory.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_serialized_write(sf: Any):
        async with sf() as session:
            yield session

    with patch("backend.lifespan.serialized_write", _fake_serialized_write):
        await _persist_event_with_retry(
            event=_make_event(),
            session_factory=_session_factory,
            retry_delay_s=0,
        )

    assert append_attempts == 2
    assert len(sessions) == 2
    sessions[0].rollback.assert_awaited_once()
    sessions[0].commit.assert_not_called()
    sessions[1].commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_event_does_not_retry_non_lock_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()

    def _session_factory() -> _FakeSessionContext:
        return _FakeSessionContext(session)

    class _FakeRepo:
        def __init__(self, session: _FakeSession) -> None:
            self._session = session

        async def append(self, event: SessionEvent) -> None:
            raise OperationalError("INSERT", {}, Exception("disk I/O error"))

    monkeypatch.setattr("backend.lifespan.EventRepository", _FakeRepo)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_serialized_write(sf: Any):
        async with sf() as session:
            yield session

    with patch("backend.lifespan.serialized_write", _fake_serialized_write):  # noqa: SIM117
        with pytest.raises(OperationalError, match="disk I/O error"):
            await _persist_event_with_retry(
                event=_make_event(),
                session_factory=_session_factory,
                retry_delay_s=0,
            )

    session.rollback.assert_awaited_once()
    session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Restart readiness marker (SPEC.md CAP-5)
# ---------------------------------------------------------------------------
#
# ``backend.services.dev_restart.restart_protocol`` is a real, committed
# dependency (integration session 3fd0b7af-27ee-4346-9098-911b5350a34c), so
# these tests exercise the real ``get_request_paths``/``write_json_atomic``
# against an isolated ``CODEPLANE_HOME`` rather than injecting a fake module —
# matching the fixture pattern used in ``test_restart_protocol.py``.


@pytest.fixture(autouse=True)
def _isolated_codeplane_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CODEPLANE_HOME at a throwaway directory for every test in this file."""
    monkeypatch.setenv("CODEPLANE_HOME", str(tmp_path))
    import backend.config as config_module

    monkeypatch.setattr(config_module, "_codeplane_dir", None)
    return tmp_path


def _read_ready_marker(tmp_path: Path, request_id: str) -> dict[str, Any]:
    import json

    path = tmp_path / "dev-restart" / f"{request_id}.ready.json"
    assert path.exists(), f"expected ready marker at {path}"
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


def _ready_marker_path(tmp_path: Path, request_id: str) -> Path:
    return tmp_path / "dev-restart" / f"{request_id}.ready.json"


@pytest.mark.asyncio
async def test_publish_restart_readiness_writes_locked_marker_shape(tmp_path: Path) -> None:
    import asyncio
    import os

    recovery_task = asyncio.create_task(asyncio.sleep(0))
    remote_task = asyncio.create_task(asyncio.sleep(0))

    await _publish_restart_readiness("req-abc123", recovery_task, remote_task)

    payload = _read_ready_marker(tmp_path, "req-abc123")
    # Locked wire contract: exactly these three camelCase keys, nothing else
    # (no diagnostics, no secrets — see ARCHITECTURE-SPINE.md AD-12).
    assert set(payload.keys()) == {"requestId", "pid", "writtenAt"}
    assert payload["requestId"] == "req-abc123"
    assert payload["pid"] == os.getpid()
    # writtenAt must parse as an ISO-8601 UTC timestamp
    datetime.fromisoformat(payload["writtenAt"].replace("Z", "+00:00"))


@pytest.mark.asyncio
async def test_publish_restart_readiness_orders_recovery_then_remote_before_write(
    tmp_path: Path,
) -> None:
    import asyncio

    order: list[str] = []

    async def _recovery() -> None:
        await asyncio.sleep(0.02)
        order.append("recovery")

    async def _remote_check() -> None:
        await asyncio.sleep(0.05)
        order.append("remote")

    recovery_task = asyncio.create_task(_recovery())
    remote_task = asyncio.create_task(_remote_check())

    await _publish_restart_readiness("req-order", recovery_task, remote_task)
    order.append("write")

    assert order == ["recovery", "remote", "write"]
    assert _ready_marker_path(tmp_path, "req-order").exists()


@pytest.mark.asyncio
async def test_publish_restart_readiness_does_not_write_marker_if_recovery_raises(
    tmp_path: Path,
) -> None:
    import asyncio

    async def _failing_recovery() -> None:
        raise RuntimeError("db unavailable")

    recovery_task = asyncio.create_task(_failing_recovery())
    remote_task = asyncio.create_task(asyncio.sleep(0))

    # A failed startup recovery must propagate — no broad failure-tolerance
    # catch that publishes readiness anyway (coordinated with the
    # restart-protocol integration session).
    with pytest.raises(RuntimeError, match="db unavailable"):
        await _publish_restart_readiness("req-recovery-fail", recovery_task, remote_task)

    assert not _ready_marker_path(tmp_path, "req-recovery-fail").exists()


@pytest.mark.asyncio
async def test_publish_restart_readiness_does_not_write_marker_if_remote_check_raises(
    tmp_path: Any,
) -> None:
    import asyncio

    async def _failing_remote_check() -> None:
        raise RuntimeError("cloudflare access not detected")

    recovery_task = asyncio.create_task(asyncio.sleep(0))
    remote_task = asyncio.create_task(_failing_remote_check())

    # A failed deferred remote validation is a real "not ready" outcome —
    # the marker distinguishes "startup + remote validation both succeeded"
    # from anything else, so it must be withheld here too.
    with pytest.raises(RuntimeError, match="cloudflare access not detected"):
        await _publish_restart_readiness("req-remote-fail", recovery_task, remote_task)

    assert not _ready_marker_path(tmp_path, "req-remote-fail").exists()


@pytest.mark.asyncio
async def test_publish_restart_readiness_does_not_write_marker_if_recovery_cancelled(
    tmp_path: Any,
) -> None:
    import asyncio

    async def _hanging_recovery() -> None:
        await asyncio.sleep(10)

    recovery_task = asyncio.create_task(_hanging_recovery())
    remote_task = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0)  # let the recovery task actually start
    recovery_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await _publish_restart_readiness("req-recovery-cancelled", recovery_task, remote_task)

    assert not _ready_marker_path(tmp_path, "req-recovery-cancelled").exists()


@pytest.mark.asyncio
async def test_publish_restart_readiness_without_remote_check_task(tmp_path: Any) -> None:
    import asyncio

    recovery_task = asyncio.create_task(asyncio.sleep(0))

    # Non-Cloudflare / no-tunnel startups schedule no remote-validation task.
    await _publish_restart_readiness("req-no-remote", recovery_task, None)

    payload = _read_ready_marker(tmp_path, "req-no-remote")
    assert payload["requestId"] == "req-no-remote"


def test_restart_request_id_env_name_is_stable() -> None:
    # Locks the env-var name the restart helper actually sets on the
    # replacement process before exec (confirmed from the real, pushed
    # ``backend/services/dev_restart/restart_helper.py``:
    # ``env["CODEPLANE_RESTART_REQUEST_ID"] = request_id``).
    assert _RESTART_REQUEST_ID_ENV == "CODEPLANE_RESTART_REQUEST_ID"


class TestPortableGracefulShutdown:
    """`os.kill(pid, SIGTERM)` is a hard TerminateProcess on Windows.

    That skipped uvicorn's graceful shutdown and every lifespan shutdown
    handler, so the Cloudflare Access safety stop left worktrees, leases and
    pending DB writes mid-flight instead of unwinding them.
    """

    def test_prefers_the_installed_sigterm_handler(self) -> None:
        import signal

        from backend.lifespan import _request_graceful_shutdown

        calls: list[int] = []

        def _handler(signum: int, _frame: object) -> None:
            calls.append(signum)

        with (
            patch("signal.getsignal", return_value=_handler),
            patch("os.kill") as mock_kill,
        ):
            _request_graceful_shutdown()

        assert calls == [signal.SIGTERM]
        mock_kill.assert_not_called()

    def test_falls_back_to_os_kill_without_a_handler(self) -> None:
        import signal

        from backend.lifespan import _request_graceful_shutdown

        with (
            patch("signal.getsignal", return_value=signal.SIG_DFL),
            patch("os.kill") as mock_kill,
        ):
            _request_graceful_shutdown()

        mock_kill.assert_called_once()
        assert mock_kill.call_args[0][1] == signal.SIGTERM

    def test_falls_back_when_the_handler_raises(self) -> None:
        from backend.lifespan import _request_graceful_shutdown

        def _broken_handler(_signum: int, _frame: object) -> None:
            raise RuntimeError("handler exploded")

        with (
            patch("signal.getsignal", return_value=_broken_handler),
            patch("os.kill") as mock_kill,
        ):
            _request_graceful_shutdown()

        mock_kill.assert_called_once()

    def test_a_signal_absorbing_handler_is_not_mistaken_for_a_graceful_one(self) -> None:
        """`cpl up` installs a handler whose whole job is to swallow SIGTERM.

        It is callable, so the handler-first path would call it and return
        with nothing shut down — leaving an unprotected public origin serving
        after the Cloudflare Access check decided to take the server down.
        """
        import signal

        from backend.cli import _neutralize_fatal_termination_signals
        from backend.lifespan import _request_graceful_shutdown

        previous = signal.getsignal(signal.SIGTERM)
        try:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            _neutralize_fatal_termination_signals()
            inert = signal.getsignal(signal.SIGTERM)
            assert callable(inert)

            with patch("os.kill") as mock_kill:
                _request_graceful_shutdown()

            mock_kill.assert_called_once()
            assert mock_kill.call_args[0][1] == signal.SIGTERM
            # The default was restored first, so that kill is guaranteed fatal.
            assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
        finally:
            signal.signal(signal.SIGTERM, previous)
