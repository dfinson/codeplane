"""Integration tests for the settings API endpoints.

Exercises:
  GET    /api/settings
  PUT    /api/settings
  GET    /api/settings/repos
  GET    /api/settings/repos/{repo_path}
  POST   /api/settings/repos
  DELETE /api/settings/repos/{repo_path}
  POST   /api/settings/cleanup-worktrees
  GET    /api/settings/browse
  GET    /api/platforms/status
  GET    /api/sdks
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from backend.api.settings import _get_config
from backend.config import CPLConfig

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from httpx import AsyncClient


def _test_config() -> CPLConfig:
    return CPLConfig(repos=["/test/repo"])


# ---------------------------------------------------------------------------
# Get / Update Settings
# ---------------------------------------------------------------------------


class TestGetSettings:
    """GET /api/settings"""

    @pytest.mark.asyncio
    async def test_returns_all_expected_fields(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config

        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        expected_keys = {
            "maxConcurrentJobs",
            "permissionMode",
            "autoPush",
            "cleanupWorktree",
            "deleteBranchAfterMerge",
            "artifactRetentionDays",
            "maxArtifactSizeMb",
            "autoArchiveDays",
            "verify",
            "selfReview",
            "maxTurns",
            "verifyPrompt",
            "selfReviewPrompt",
        }
        assert expected_keys.issubset(data.keys())

    @pytest.mark.asyncio
    async def test_default_values_have_correct_types(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config

        resp = await client.get("/api/settings")
        data = resp.json()
        assert isinstance(data["maxConcurrentJobs"], int)
        assert isinstance(data["permissionMode"], str)
        assert isinstance(data["autoPush"], bool)
        assert isinstance(data["cleanupWorktree"], bool)
        assert isinstance(data["artifactRetentionDays"], int)
        assert isinstance(data["verify"], bool)


class TestUpdateSettings:
    """PUT /api/settings"""

    @pytest.mark.asyncio
    async def test_update_max_concurrent_jobs(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "backend.api.settings.load_config", lambda path=None: _test_config()
        )
        monkeypatch.setattr(
            "backend.api.settings.save_config", lambda config, path=None: None
        )

        resp = await client.put("/api/settings", json={"maxConcurrentJobs": 5})
        assert resp.status_code == 200
        assert resp.json()["maxConcurrentJobs"] == 5

    @pytest.mark.asyncio
    async def test_partial_update_preserves_other_fields(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "backend.api.settings.load_config", lambda path=None: _test_config()
        )
        monkeypatch.setattr(
            "backend.api.settings.save_config", lambda config, path=None: None
        )

        baseline = (await client.get("/api/settings")).json()

        resp = await client.put("/api/settings", json={"autoPush": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["autoPush"] is True
        assert data["maxConcurrentJobs"] == baseline["maxConcurrentJobs"]
        assert data["permissionMode"] == baseline["permissionMode"]

    @pytest.mark.asyncio
    async def test_update_verification_fields(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "backend.api.settings.load_config", lambda path=None: _test_config()
        )
        monkeypatch.setattr(
            "backend.api.settings.save_config", lambda config, path=None: None
        )

        resp = await client.put(
            "/api/settings",
            json={"verify": True, "selfReview": True, "maxTurns": 3},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["verify"] is True
        assert data["selfReview"] is True
        assert data["maxTurns"] == 3

    @pytest.mark.asyncio
    async def test_invalid_value_returns_422(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "backend.api.settings.load_config", lambda path=None: _test_config()
        )
        monkeypatch.setattr(
            "backend.api.settings.save_config", lambda config, path=None: None
        )

        resp = await client.put("/api/settings", json={"maxConcurrentJobs": 0})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Repo Endpoints
# ---------------------------------------------------------------------------


class TestListRepos:
    """GET /api/settings/repos"""

    @pytest.mark.asyncio
    async def test_returns_registered_repos(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config

        resp = await client.get("/api/settings/repos")
        assert resp.status_code == 200
        assert "/test/repo" in resp.json()["items"]


class TestGetRepoDetail:
    """GET /api/settings/repos/{repo_path}"""

    @pytest.mark.asyncio
    async def test_registered_repo(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config

        resp = await client.get("/api/settings/repos//test/repo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "/test/repo"
        assert data["originUrl"] is not None
        assert data["baseBranch"] == "main"
        assert "platform" in data

    @pytest.mark.asyncio
    async def test_unregistered_repo_returns_404(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config

        resp = await client.get("/api/settings/repos//not/registered")
        assert resp.status_code == 404


class TestRegisterRepo:
    """POST /api/settings/repos"""

    @pytest.mark.asyncio
    async def test_register_local_repo(
        self, client: AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config
        monkeypatch.setattr(
            "backend.api.settings.register_repo",
            lambda config, repo_path, config_path=None: repo_path,
        )

        resp = await client.post("/api/settings/repos", json={"source": "/some/path"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["source"] == "/some/path"
        assert data["cloned"] is False

    @pytest.mark.asyncio
    async def test_remote_url_without_clone_to_returns_400(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config

        resp = await client.post(
            "/api/settings/repos",
            json={"source": "https://github.com/test/repo.git"},
        )
        assert resp.status_code == 400
        assert "clone_to" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_invalid_local_repo_returns_400(
        self,
        client: AsyncClient,
        app: FastAPI,
        mock_git_service: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config
        monkeypatch.setattr(
            "backend.api.settings.register_repo",
            lambda config, repo_path, config_path=None: repo_path,
        )
        mock_git_service.validate_repo.return_value = False

        resp = await client.post("/api/settings/repos", json={"source": "/bad/repo"})
        assert resp.status_code == 400


class TestUnregisterRepo:
    """DELETE /api/settings/repos/{repo_path}"""

    @pytest.mark.asyncio
    async def test_unregister_succeeds(
        self, client: AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config
        monkeypatch.setattr(
            "backend.api.settings.unregister_repo",
            lambda config, repo_path, config_path=None: repo_path,
        )

        resp = await client.delete("/api/settings/repos//test/repo")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_returns_404(
        self, client: AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config

        def _raise(config: CPLConfig, repo_path: str, config_path: Path | None = None) -> str:
            raise ValueError(f"Repository '{repo_path}' is not in the allowlist.")

        monkeypatch.setattr("backend.api.settings.unregister_repo", _raise)

        resp = await client.delete("/api/settings/repos//not/registered")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cleanup Worktrees
# ---------------------------------------------------------------------------


class TestCleanupWorktrees:
    """POST /api/settings/cleanup-worktrees"""

    @pytest.mark.asyncio
    async def test_returns_removed_count(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        app.dependency_overrides[_get_config] = _test_config

        resp = await client.post("/api/settings/cleanup-worktrees")
        assert resp.status_code == 200
        assert resp.json() == {"removed": 0}


# ---------------------------------------------------------------------------
# Browse Directories
# ---------------------------------------------------------------------------


class TestBrowseDirectories:
    """GET /api/settings/browse"""

    @pytest.mark.asyncio
    async def test_browse_home(self, client: AsyncClient) -> None:
        resp = await client.get("/api/settings/browse", params={"path": "~"})
        assert resp.status_code == 200
        data = resp.json()
        assert "current" in data
        assert data["parent"] is None  # at home, no parent exposed
        assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_browse_returns_subdirectory_structure(
        self,
        client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        project = fake_home / "myproject"
        project.mkdir()
        (project / ".git").mkdir()
        (fake_home / ".hidden").mkdir()  # hidden — excluded
        (fake_home / "readme.txt").write_text("hi")  # not a dir — excluded

        monkeypatch.setenv("HOME", str(fake_home))

        resp = await client.get(
            "/api/settings/browse", params={"path": str(fake_home)}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"] == str(fake_home)
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "myproject"
        assert data["items"][0]["isGitRepo"] == "true"

    @pytest.mark.asyncio
    async def test_browse_nonexistent_returns_404(
        self,
        client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        resp = await client.get(
            "/api/settings/browse",
            params={"path": str(fake_home / "nope")},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_browse_outside_home_returns_403(
        self,
        client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        resp = await client.get(
            "/api/settings/browse", params={"path": str(outside)}
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Platform Status
# ---------------------------------------------------------------------------


class TestPlatformStatus:
    """GET /api/platforms/status

    Note: PlatformStatusListResponse requires a ``timestamp`` field, but the
    endpoint never supplies one.  Both code paths therefore raise a Pydantic
    ``ValidationError`` at construction time.
    """

    @pytest.mark.asyncio
    async def test_raises_due_to_missing_timestamp(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        from backend.services.platform_adapter import PlatformStatus

        registry = AsyncMock()
        registry.check_all.return_value = [
            PlatformStatus(
                platform="github", authenticated=True, user="octocat"
            ),
        ]
        app.state.platform_registry = registry

        with pytest.raises(ValidationError, match="timestamp"):
            await client.get("/api/platforms/status")

    @pytest.mark.asyncio
    async def test_no_registry_also_raises(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        app.state.platform_registry = None

        with pytest.raises(ValidationError, match="timestamp"):
            await client.get("/api/platforms/status")


# ---------------------------------------------------------------------------
# SDK List
# ---------------------------------------------------------------------------


class TestListSDKs:
    """GET /api/sdks"""

    @pytest.mark.asyncio
    async def test_returns_available_sdks(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.services.setup_service import AgentCLIStatus

        def _mock_check(sdk_id: str) -> AgentCLIStatus:
            return AgentCLIStatus(
                sdk_id=sdk_id,
                name=sdk_id,
                installed=True,
                cli_reachable=True,
                ready=True,
                detail="Ready",
                hint="",
            )

        monkeypatch.setattr(
            "backend.services.setup_service.check_agent_cli", _mock_check
        )

        resp = await client.get("/api/sdks")
        assert resp.status_code == 200
        data = resp.json()
        assert "default" in data
        assert "sdks" in data
        assert len(data["sdks"]) >= 1
        sdk_ids = {s["id"] for s in data["sdks"]}
        assert "copilot" in sdk_ids
        for sdk in data["sdks"]:
            assert sdk["enabled"] is True
            assert sdk["status"] == "ready"
