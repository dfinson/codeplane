"""Platform adapter abstraction for git hosting platforms (GitHub, Azure DevOps, GitLab).

Provides a pluggable interface for PR creation, auth checking, and platform
detection so MergeService doesn't hard-code any single provider.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess  # noqa: S404
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from backend.config import PlatformConfig

log = structlog.get_logger()

_REF_PATTERN = re.compile(r"^[a-zA-Z0-9/_.-]+$")


@dataclass
class PRResult:
    """Outcome of a PR creation attempt."""

    url: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.url is not None


@dataclass
class PlatformStatus:
    """Auth / health status for a platform."""

    platform: str
    authenticated: bool
    user: str | None = None
    error: str | None = None


@runtime_checkable
class PlatformAdapter(Protocol):
    """Interface for git hosting platform operations."""

    @property
    def name(self) -> str:
        """Platform identifier (e.g. 'github', 'azure_devops', 'gitlab')."""
        ...

    async def check_auth(self) -> PlatformStatus:
        """Verify that credentials are valid and return status."""
        ...

    async def create_pr(
        self,
        *,
        cwd: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> PRResult:
        """Create a pull/merge request. Returns a PRResult with URL or error."""
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_refs(*refs: str) -> bool:
    """Return True if all refs are safe for use in CLI args."""
    return all(_REF_PATTERN.match(r) for r in refs)


async def _run_cli(
    args: list[str],
    *,
    cwd: str,
    timeout: int = 60,
) -> tuple[int, str, str]:
    """Run a CLI command and return (returncode, stdout, stderr)."""
    result = await asyncio.to_thread(
        subprocess.run,  # noqa: S603
        args,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ---------------------------------------------------------------------------
# GitHub Adapter (gh CLI)
# ---------------------------------------------------------------------------


class GitHubAdapter:
    """GitHub PR operations via the ``gh`` CLI."""

    def __init__(self, config: PlatformConfig | None = None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "github"

    async def check_auth(self) -> PlatformStatus:
        if shutil.which("gh") is None:
            return PlatformStatus(
                platform=self.name,
                authenticated=False,
                error="gh CLI not installed",
            )
        try:
            rc, stdout, stderr = await _run_cli(["gh", "auth", "status"], cwd="/tmp")  # noqa: S108
            if rc == 0:
                # Extract username from output like "Logged in to github.com account user123"
                user = None
                for line in stdout.splitlines() + stderr.splitlines():
                    if "account" in line.lower():
                        parts = line.strip().split()
                        idx = next(
                            (i for i, p in enumerate(parts) if p.lower() == "account"),
                            -1,
                        )
                        if idx >= 0 and idx + 1 < len(parts):
                            user = parts[idx + 1].strip("()")
                            break
                return PlatformStatus(platform=self.name, authenticated=True, user=user)
            return PlatformStatus(
                platform=self.name,
                authenticated=False,
                error=stderr[:200] or "Not authenticated",
            )
        except Exception as exc:
            return PlatformStatus(platform=self.name, authenticated=False, error=str(exc)[:200])

    async def create_pr(
        self,
        *,
        cwd: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> PRResult:
        if shutil.which("gh") is None:
            return PRResult(error="gh CLI not installed")
        if not _validate_refs(head, base):
            return PRResult(error="Invalid branch ref")

        try:
            rc, stdout, stderr = await _run_cli(
                [
                    "gh",
                    "pr",
                    "create",
                    "--title",
                    title,
                    "--body",
                    body,
                    "--head",
                    head,
                    "--base",
                    base,
                    "--",
                ],
                cwd=cwd,
            )
            if rc == 0:
                log.info("github_pr_created", pr_url=stdout)
                return PRResult(url=stdout)
            log.warning("github_pr_failed", returncode=rc, stderr=stderr[:500])
            return PRResult(error=stderr[:500] or f"gh exited {rc}")
        except Exception as exc:
            log.warning("github_pr_error", exc_info=True)
            return PRResult(error=str(exc)[:200])


# ---------------------------------------------------------------------------
# Azure DevOps Adapter (az repos CLI)
# ---------------------------------------------------------------------------


class AzureDevOpsAdapter:
    """Azure DevOps PR operations via the ``az repos`` CLI."""

    def __init__(self, config: PlatformConfig | None = None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "azure_devops"

    async def check_auth(self) -> PlatformStatus:
        if shutil.which("az") is None:
            return PlatformStatus(
                platform=self.name,
                authenticated=False,
                error="az CLI not installed",
            )
        try:
            rc, stdout, stderr = await _run_cli(
                ["az", "account", "show", "--output", "json"],
                cwd="/tmp",  # noqa: S108
            )
            if rc == 0:
                import json

                data = json.loads(stdout)
                user = data.get("user", {}).get("name")
                return PlatformStatus(platform=self.name, authenticated=True, user=user)
            return PlatformStatus(
                platform=self.name,
                authenticated=False,
                error=stderr[:200] or "Not authenticated",
            )
        except Exception as exc:
            return PlatformStatus(platform=self.name, authenticated=False, error=str(exc)[:200])

    async def create_pr(
        self,
        *,
        cwd: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> PRResult:
        if shutil.which("az") is None:
            return PRResult(error="az CLI not installed")
        if not _validate_refs(head, base):
            return PRResult(error="Invalid branch ref")

        try:
            rc, stdout, stderr = await _run_cli(
                [
                    "az",
                    "repos",
                    "pr",
                    "create",
                    "--title",
                    title,
                    "--description",
                    body,
                    "--source-branch",
                    head,
                    "--target-branch",
                    base,
                    "--detect",
                    "--output",
                    "json",
                ],
                cwd=cwd,
            )
            if rc == 0:
                import json

                data = json.loads(stdout)
                # AzDO returns the PR URL in the "url" or construct from webUrl
                pr_url = data.get("repository", {}).get("webUrl", "")
                pr_id = data.get("pullRequestId", "")
                if pr_url and pr_id:
                    pr_url = f"{pr_url}/pullrequest/{pr_id}"
                elif data.get("url"):
                    pr_url = data["url"]
                log.info("azdo_pr_created", pr_url=pr_url)
                return PRResult(url=pr_url)
            log.warning("azdo_pr_failed", returncode=rc, stderr=stderr[:500])
            return PRResult(error=stderr[:500] or f"az exited {rc}")
        except Exception as exc:
            log.warning("azdo_pr_error", exc_info=True)
            return PRResult(error=str(exc)[:200])


# ---------------------------------------------------------------------------
# GitLab Adapter (glab CLI)
# ---------------------------------------------------------------------------


class GitLabAdapter:
    """GitLab merge request operations via the ``glab`` CLI."""

    def __init__(self, config: PlatformConfig | None = None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "gitlab"

    async def check_auth(self) -> PlatformStatus:
        if shutil.which("glab") is None:
            return PlatformStatus(
                platform=self.name,
                authenticated=False,
                error="glab CLI not installed",
            )
        try:
            rc, stdout, stderr = await _run_cli(
                ["glab", "auth", "status"],
                cwd="/tmp",  # noqa: S108
            )
            if rc == 0:
                user = None
                for line in stdout.splitlines() + stderr.splitlines():
                    if "logged in" in line.lower():
                        parts = line.strip().split()
                        for i, p in enumerate(parts):
                            if p.lower() == "as":
                                if i + 1 < len(parts):
                                    user = parts[i + 1].strip()
                                break
                        break
                return PlatformStatus(platform=self.name, authenticated=True, user=user)
            return PlatformStatus(
                platform=self.name,
                authenticated=False,
                error=stderr[:200] or "Not authenticated",
            )
        except Exception as exc:
            return PlatformStatus(platform=self.name, authenticated=False, error=str(exc)[:200])

    async def create_pr(
        self,
        *,
        cwd: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> PRResult:
        if shutil.which("glab") is None:
            return PRResult(error="glab CLI not installed")
        if not _validate_refs(head, base):
            return PRResult(error="Invalid branch ref")

        try:
            rc, stdout, stderr = await _run_cli(
                [
                    "glab",
                    "mr",
                    "create",
                    "--title",
                    title,
                    "--description",
                    body,
                    "--source-branch",
                    head,
                    "--target-branch",
                    base,
                    "--yes",
                ],
                cwd=cwd,
            )
            if rc == 0:
                # glab outputs the MR URL on the last non-empty line
                pr_url = stdout.splitlines()[-1] if stdout.strip() else None
                log.info("gitlab_mr_created", pr_url=pr_url)
                return PRResult(url=pr_url)
            log.warning("gitlab_mr_failed", returncode=rc, stderr=stderr[:500])
            return PRResult(error=stderr[:500] or f"glab exited {rc}")
        except Exception as exc:
            log.warning("gitlab_mr_error", exc_info=True)
            return PRResult(error=str(exc)[:200])


# ---------------------------------------------------------------------------
# Generic fallback (no PR support)
# ---------------------------------------------------------------------------


class GenericGitAdapter:
    """Fallback adapter for repos with no detected platform. No PR creation."""

    @property
    def name(self) -> str:
        return "generic"

    async def check_auth(self) -> PlatformStatus:
        return PlatformStatus(
            platform=self.name,
            authenticated=True,
            error="No platform detected — local merge only",
        )

    async def create_pr(
        self,
        *,
        cwd: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> PRResult:
        return PRResult(error="No git platform detected for this repo — PR creation unavailable")


# ---------------------------------------------------------------------------
# Platform Registry — detects and caches adapter per repo
# ---------------------------------------------------------------------------

# URL patterns → platform name
_PLATFORM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"github\.com[:/]", re.IGNORECASE), "github"),
    (re.compile(r"dev\.azure\.com[:/]|\.visualstudio\.com[:/]", re.IGNORECASE), "azure_devops"),
    (re.compile(r"gitlab\.com[:/]|gitlab\.", re.IGNORECASE), "gitlab"),
]

_ADAPTER_CLASSES: dict[str, type] = {
    "github": GitHubAdapter,
    "azure_devops": AzureDevOpsAdapter,
    "gitlab": GitLabAdapter,
}


def detect_platform(origin_url: str | None) -> str | None:
    """Detect the hosting platform from a git remote URL."""
    if not origin_url:
        return None
    for pattern, platform in _PLATFORM_PATTERNS:
        if pattern.search(origin_url):
            return platform
    return None


class PlatformRegistry:
    """Resolves the correct PlatformAdapter for a given repository.

    Detection order:
    1. Per-repo config override (platforms.<repo_path>)
    2. Auto-detect from git remote origin URL
    3. Fall back to GenericGitAdapter
    """

    def __init__(self, platform_configs: dict[str, PlatformConfig] | None = None) -> None:
        self._configs = platform_configs or {}
        self._cache: dict[str, PlatformAdapter] = {}
        self._fallback = GenericGitAdapter()

    def _make_adapter(self, platform: str) -> PlatformAdapter:
        cls = _ADAPTER_CLASSES.get(platform)
        if cls is None:
            return self._fallback
        config = self._configs.get(platform)
        return cls(config=config)  # type: ignore[no-any-return]

    async def get_adapter(self, repo_path: str, origin_url: str | None = None) -> PlatformAdapter:
        """Get (or auto-detect and cache) the platform adapter for a repo."""
        if repo_path in self._cache:
            return self._cache[repo_path]

        # 1. Check per-platform config
        for platform_name, cfg in self._configs.items():
            if cfg and cfg.repos and repo_path in cfg.repos:
                adapter = self._make_adapter(platform_name)
                self._cache[repo_path] = adapter
                log.info("platform_resolved_from_config", repo=repo_path, platform=platform_name)
                return adapter

        # 2. Auto-detect from origin URL
        if origin_url is None:
            from backend.services.git_service import GitService

            git = GitService.__new__(GitService)
            git._worktrees_dirname = ".tower-worktrees"
            try:
                origin_url = await git.get_origin_url(repo_path)
            except Exception:
                origin_url = None

        platform = detect_platform(origin_url)
        if platform:
            adapter = self._make_adapter(platform)
            self._cache[repo_path] = adapter
            log.info("platform_auto_detected", repo=repo_path, platform=platform, origin=origin_url)
            return adapter

        # 3. Fallback
        self._cache[repo_path] = self._fallback
        log.info("platform_fallback_generic", repo=repo_path)
        return self._fallback

    async def check_all(self) -> list[PlatformStatus]:
        """Check auth status for all known platform adapters."""
        # Check each platform type that has a CLI available
        results: list[PlatformStatus] = []
        seen: set[str] = set()
        for adapter in self._cache.values():
            if adapter.name not in seen:
                seen.add(adapter.name)
                results.append(await adapter.check_auth())
        # Also check any not yet cached but configured
        for platform_name in self._configs:
            if platform_name not in seen:
                seen.add(platform_name)
                adapter = self._make_adapter(platform_name)
                results.append(await adapter.check_auth())
        # Always include GitHub if not already checked (most common)
        if "github" not in seen:
            results.append(await GitHubAdapter().check_auth())
        return results

    def invalidate(self, repo_path: str) -> None:
        """Remove cached adapter for a repo (e.g. after config change)."""
        self._cache.pop(repo_path, None)
