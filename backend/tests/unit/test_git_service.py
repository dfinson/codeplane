"""Tests for GitService utility methods (no subprocess calls)."""

from __future__ import annotations

import pytest

from backend.config import TowerConfig
from backend.services.git_service import GitService


@pytest.fixture
def config() -> TowerConfig:
    return TowerConfig()


@pytest.fixture
def git_service(config: TowerConfig) -> GitService:
    return GitService(config)


class TestIsRemoteUrl:
    def test_https_url(self) -> None:
        assert GitService.is_remote_url("https://github.com/org/repo.git") is True

    def test_ssh_url(self) -> None:
        assert GitService.is_remote_url("git@github.com:org/repo.git") is True

    def test_ssh_protocol_url(self) -> None:
        assert GitService.is_remote_url("ssh://git@github.com/org/repo.git") is True

    def test_local_path_not_remote(self) -> None:
        assert GitService.is_remote_url("/repos/test") is False

    def test_relative_path_not_remote(self) -> None:
        assert GitService.is_remote_url("./repos/test") is False


class TestDeriveCloneDir:
    def test_https_github_url(self) -> None:
        result = GitService.derive_clone_dir(
            "https://github.com/org/repo.git",
            "~/tower-repos",
        )
        assert result.endswith("org/repo")
        assert ".git" not in result

    def test_ssh_github_url(self) -> None:
        result = GitService.derive_clone_dir(
            "git@github.com:org/repo.git",
            "~/tower-repos",
        )
        assert result.endswith("org/repo")

    def test_simple_url(self) -> None:
        result = GitService.derive_clone_dir(
            "https://example.com/my-repo.git",
            "~/tower-repos",
        )
        assert result.endswith("example.com/my-repo")

    def test_no_git_suffix(self) -> None:
        result = GitService.derive_clone_dir(
            "https://github.com/org/repo",
            "~/tower-repos",
        )
        assert result.endswith("org/repo")
