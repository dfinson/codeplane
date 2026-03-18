"""API integration tests for Terminal endpoints.

Tests exercise the REST endpoints for terminal session management
and the AI ask feature.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock, patch

import backend.api.terminal as terminal_mod

if TYPE_CHECKING:
    from httpx import AsyncClient


# ── Helpers ──────────────────────────────────────────────────────────


def _fake_pty_session(
    *,
    session_id: str = "s1",
    shell: str = "/bin/bash",
    cwd: str = "/tmp",
    job_id: str | None = None,
    pid: int = 123,
) -> Mock:
    """Return a mock that looks like a PtySession."""
    session = Mock()
    session.id = session_id
    session.shell = shell
    session.cwd = cwd
    session.job_id = job_id
    session.process = Mock(pid=pid)
    return session


# ── Create session ───────────────────────────────────────────────────


class TestCreateSession:
    async def test_create_success(self, client: AsyncClient) -> None:
        svc = terminal_mod._terminal_service
        svc.create_session = Mock(return_value=_fake_pty_session())

        resp = await client.post(
            "/api/terminal/sessions",
            json={"shell": None, "cwd": None, "jobId": None},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "s1"
        assert data["shell"] == "/bin/bash"
        assert data["cwd"] == "/tmp"
        assert data["pid"] == 123

    async def test_create_with_job_id(self, client: AsyncClient) -> None:
        svc = terminal_mod._terminal_service
        svc.create_session = Mock(
            return_value=_fake_pty_session(job_id="job-42")
        )

        resp = await client.post(
            "/api/terminal/sessions",
            json={"shell": "/bin/zsh", "cwd": "/home", "jobId": "job-42"},
        )
        assert resp.status_code == 201
        assert resp.json()["jobId"] == "job-42"

    async def test_create_runtime_error_returns_400(
        self, client: AsyncClient
    ) -> None:
        svc = terminal_mod._terminal_service
        svc.create_session = Mock(
            side_effect=RuntimeError("Maximum terminal sessions reached")
        )

        resp = await client.post(
            "/api/terminal/sessions", json={}
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_create_value_error_returns_400(
        self, client: AsyncClient
    ) -> None:
        svc = terminal_mod._terminal_service
        svc.create_session = Mock(
            side_effect=ValueError("Shell not found")
        )

        resp = await client.post(
            "/api/terminal/sessions", json={"shell": "/nonexistent"}
        )
        assert resp.status_code == 400


# ── List sessions ────────────────────────────────────────────────────


class TestListSessions:
    async def test_list_returns_sessions(self, client: AsyncClient) -> None:
        svc = terminal_mod._terminal_service
        svc.list_sessions = Mock(return_value=[
            {
                "id": "s1",
                "shell": "/bin/bash",
                "cwd": "/tmp",
                "job_id": None,
                "pid": 100,
                "clients": 0,
            },
            {
                "id": "s2",
                "shell": "/bin/zsh",
                "cwd": "/home",
                "job_id": "job-1",
                "pid": 200,
                "clients": 2,
            },
        ])

        resp = await client.get("/api/terminal/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "s1"
        assert data[1]["jobId"] == "job-1"

    async def test_list_empty(self, client: AsyncClient) -> None:
        svc = terminal_mod._terminal_service
        svc.list_sessions = Mock(return_value=[])

        resp = await client.get("/api/terminal/sessions")
        assert resp.status_code == 200
        assert resp.json() == []


# ── Delete session ───────────────────────────────────────────────────


class TestDeleteSession:
    async def test_delete_success(self, client: AsyncClient) -> None:
        svc = terminal_mod._terminal_service
        svc.kill_session = AsyncMock(return_value=True)

        resp = await client.delete("/api/terminal/sessions/s1")
        assert resp.status_code == 204

    async def test_delete_not_found(self, client: AsyncClient) -> None:
        svc = terminal_mod._terminal_service
        svc.kill_session = AsyncMock(return_value=False)

        resp = await client.delete("/api/terminal/sessions/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()


# ── Ask AI ───────────────────────────────────────────────────────────


class TestAskAI:
    async def test_ask_success(self, client: AsyncClient) -> None:
        mock_session = AsyncMock()
        mock_session.complete = AsyncMock(
            return_value=json.dumps({
                "command": "ls -la",
                "explanation": "List files in detail",
            })
        )
        with patch.object(terminal_mod, "_ask_utility_session", mock_session):
            resp = await client.post(
                "/api/terminal/ask",
                json={"prompt": "list all files"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["command"] == "ls -la"
        assert data["explanation"] == "List files in detail"

    async def test_ask_not_initialized(self, client: AsyncClient) -> None:
        with patch.object(terminal_mod, "_ask_utility_session", None):
            resp = await client.post(
                "/api/terminal/ask",
                json={"prompt": "list files"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["command"] == ""
        assert "not available" in data["explanation"].lower()

    async def test_ask_with_context(self, client: AsyncClient) -> None:
        mock_session = AsyncMock()
        mock_session.complete = AsyncMock(
            return_value='{"command": "cd ..", "explanation": "Go up"}'
        )
        with patch.object(terminal_mod, "_ask_utility_session", mock_session):
            resp = await client.post(
                "/api/terminal/ask",
                json={"prompt": "go up a dir", "context": "~/projects"},
            )
        assert resp.status_code == 200
        assert resp.json()["command"] == "cd .."

    async def test_ask_invalid_json_from_llm(
        self, client: AsyncClient
    ) -> None:
        mock_session = AsyncMock()
        mock_session.complete = AsyncMock(return_value="just run: ls")
        with patch.object(terminal_mod, "_ask_utility_session", mock_session):
            resp = await client.post(
                "/api/terminal/ask",
                json={"prompt": "list files"},
            )
        assert resp.status_code == 200
        assert resp.json()["command"] == "just run: ls"
