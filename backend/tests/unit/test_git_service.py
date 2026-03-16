"""Tests for GitService utility methods (no subprocess calls)."""

from __future__ import annotations

import pytest

from backend.config import CPLConfig
from backend.services.git_service import GitService


@pytest.fixture
def config() -> CPLConfig:
    return CPLConfig()


@pytest.fixture
def git_service(config: CPLConfig) -> GitService:
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
