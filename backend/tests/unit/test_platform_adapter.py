"""Tests for platform adapter — detection, registry, and PR creation."""

from __future__ import annotations

from unittest.mock import patch

from backend.config import PlatformConfig
from backend.services.platform_adapter import (
    AzureDevOpsAdapter,
    GenericGitAdapter,
    GitHubAdapter,
    GitLabAdapter,
    PlatformRegistry,
    PRResult,
    detect_platform,
)

# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------


class TestDetectPlatform:
    def test_github_https(self) -> None:
        assert detect_platform("https://github.com/user/repo.git") == "github"

    def test_github_ssh(self) -> None:
        assert detect_platform("git@github.com:user/repo.git") == "github"

    def test_azdo_https(self) -> None:
        assert detect_platform("https://dev.azure.com/org/project/_git/repo") == "azure_devops"

    def test_azdo_visualstudio(self) -> None:
        assert detect_platform("https://org.visualstudio.com/project/_git/repo") == "azure_devops"

    def test_gitlab_https(self) -> None:
        assert detect_platform("https://gitlab.com/user/repo.git") == "gitlab"

    def test_gitlab_selfhosted(self) -> None:
        assert detect_platform("https://gitlab.example.com/user/repo.git") == "gitlab"

    def test_unknown(self) -> None:
        assert detect_platform("https://bitbucket.org/user/repo.git") is None

    def test_none(self) -> None:
        assert detect_platform(None) is None

    def test_empty(self) -> None:
        assert detect_platform("") is None


# ---------------------------------------------------------------------------
# PRResult
# ---------------------------------------------------------------------------


class TestPRResult:
    def test_ok_with_url(self) -> None:
        r = PRResult(url="https://github.com/user/repo/pull/1")
        assert r.ok is True

    def test_not_ok(self) -> None:
        r = PRResult(error="failed")
        assert r.ok is False

    def test_not_ok_none_url(self) -> None:
        r = PRResult()
        assert r.ok is False


# ---------------------------------------------------------------------------
# GenericGitAdapter
# ---------------------------------------------------------------------------


class TestGenericGitAdapter:
    async def test_name(self) -> None:
        adapter = GenericGitAdapter()
        assert adapter.name == "generic"

    async def test_check_auth(self) -> None:
        adapter = GenericGitAdapter()
        status = await adapter.check_auth()
        assert status.authenticated is True
        assert status.platform == "generic"

    async def test_create_pr_always_fails(self) -> None:
        adapter = GenericGitAdapter()
        result = await adapter.create_pr(
            cwd="/tmp",
            head="feature",
            base="main",
            title="test",
            body="body",
        )
        assert result.ok is False
        assert "unavailable" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# GitHubAdapter
# ---------------------------------------------------------------------------


class TestGitHubAdapter:
    async def test_name(self) -> None:
        assert GitHubAdapter().name == "github"

    @patch("backend.services.platform_adapter.shutil.which", return_value=None)
    async def test_check_auth_no_cli(self, _mock: object) -> None:
        status = await GitHubAdapter().check_auth()
        assert status.authenticated is False
        assert "not installed" in (status.error or "").lower()

    @patch("backend.services.platform_adapter.shutil.which", return_value=None)
    async def test_create_pr_no_cli(self, _mock: object) -> None:
        result = await GitHubAdapter().create_pr(cwd="/tmp", head="feat", base="main", title="t", body="b")
        assert result.ok is False
        assert "not installed" in (result.error or "").lower()

    async def test_create_pr_invalid_ref(self) -> None:
        result = await GitHubAdapter().create_pr(cwd="/tmp", head="feat; rm -rf /", base="main", title="t", body="b")
        assert result.ok is False
        assert "invalid" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# AzureDevOpsAdapter
# ---------------------------------------------------------------------------


class TestAzureDevOpsAdapter:
    async def test_name(self) -> None:
        assert AzureDevOpsAdapter().name == "azure_devops"

    @patch("backend.services.platform_adapter.shutil.which", return_value=None)
    async def test_check_auth_no_cli(self, _mock: object) -> None:
        status = await AzureDevOpsAdapter().check_auth()
        assert status.authenticated is False
        assert "not installed" in (status.error or "").lower()

    @patch("backend.services.platform_adapter.shutil.which", return_value=None)
    async def test_create_pr_no_cli(self, _mock: object) -> None:
        result = await AzureDevOpsAdapter().create_pr(cwd="/tmp", head="feat", base="main", title="t", body="b")
        assert result.ok is False


# ---------------------------------------------------------------------------
# GitLabAdapter
# ---------------------------------------------------------------------------


class TestGitLabAdapter:
    async def test_name(self) -> None:
        assert GitLabAdapter().name == "gitlab"

    @patch("backend.services.platform_adapter.shutil.which", return_value=None)
    async def test_check_auth_no_cli(self, _mock: object) -> None:
        status = await GitLabAdapter().check_auth()
        assert status.authenticated is False

    @patch("backend.services.platform_adapter.shutil.which", return_value=None)
    async def test_create_pr_no_cli(self, _mock: object) -> None:
        result = await GitLabAdapter().create_pr(cwd="/tmp", head="feat", base="main", title="t", body="b")
        assert result.ok is False


# ---------------------------------------------------------------------------
# PlatformRegistry
# ---------------------------------------------------------------------------


class TestPlatformRegistry:
    async def test_auto_detect_github(self) -> None:
        registry = PlatformRegistry()
        adapter = await registry.get_adapter("/tmp/repo", origin_url="https://github.com/user/repo.git")
        assert adapter.name == "github"

    async def test_auto_detect_azdo(self) -> None:
        registry = PlatformRegistry()
        adapter = await registry.get_adapter("/tmp/repo", origin_url="https://dev.azure.com/org/proj/_git/repo")
        assert adapter.name == "azure_devops"

    async def test_auto_detect_gitlab(self) -> None:
        registry = PlatformRegistry()
        adapter = await registry.get_adapter("/tmp/repo", origin_url="https://gitlab.com/user/repo.git")
        assert adapter.name == "gitlab"

    async def test_fallback_generic(self) -> None:
        registry = PlatformRegistry()
        adapter = await registry.get_adapter("/tmp/repo", origin_url="https://unknown.example.com/repo")
        assert adapter.name == "generic"

    async def test_config_override(self) -> None:
        configs = {
            "azure_devops": PlatformConfig(auth="cli", repos=["/tmp/my-work-repo"]),
        }
        registry = PlatformRegistry(platform_configs=configs)
        adapter = await registry.get_adapter("/tmp/my-work-repo", origin_url="https://github.com/user/repo")
        # Config override takes precedence over URL detection
        assert adapter.name == "azure_devops"

    async def test_cache_hit(self) -> None:
        registry = PlatformRegistry()
        a1 = await registry.get_adapter("/tmp/repo", origin_url="https://github.com/user/repo.git")
        a2 = await registry.get_adapter("/tmp/repo")  # No URL needed on cache hit
        assert a1 is a2

    async def test_invalidate(self) -> None:
        registry = PlatformRegistry()
        await registry.get_adapter("/tmp/repo", origin_url="https://github.com/user/repo.git")
        registry.invalidate("/tmp/repo")
        # After invalidation, with different URL, should pick up new platform
        adapter = await registry.get_adapter("/tmp/repo", origin_url="https://gitlab.com/user/repo.git")
        assert adapter.name == "gitlab"

    async def test_check_all(self) -> None:
        registry = PlatformRegistry()
        await registry.get_adapter("/tmp/repo", origin_url="https://github.com/user/repo.git")
        statuses = await registry.check_all()
        assert len(statuses) >= 1
        platforms = [s.platform for s in statuses]
        assert "github" in platforms
