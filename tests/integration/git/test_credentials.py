"""Integration tests for credential handling with real GitHub repos."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.git import GitOps
from codeplane.git.credentials import SystemCredentialCallback, get_default_callbacks

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class TestPublicRepoAccess:
    """Tests for public repository access (no auth required for read)."""

    def test_clone_public_repo(self, cloned_public_repo: Path) -> None:
        """Cloning public repo should succeed."""
        ops = GitOps(cloned_public_repo)

        assert ops.path.exists()
        assert (ops.path / "seed.txt").exists()

    def test_fetch_public_repo(self, cloned_public_repo: Path) -> None:
        """Fetching from public repo should work without explicit credentials."""
        ops = GitOps(cloned_public_repo)

        # Should not raise
        ops.fetch("origin")

    def test_remote_branches_visible(self, cloned_public_repo: Path) -> None:
        """Remote branches should be visible after clone."""
        ops = GitOps(cloned_public_repo)

        branches = ops.branches(include_remote=True)
        remote_names = {b.short_name for b in branches if b.is_remote}

        assert "origin/main" in remote_names
        assert "origin/feature/test-branch" in remote_names


class TestPrivateRepoAccess:
    """Tests for private repository access (requires auth)."""

    def test_clone_private_repo(self, cloned_private_repo: Path) -> None:
        """Cloning private repo should succeed with valid credentials."""
        ops = GitOps(cloned_private_repo)

        assert ops.path.exists()
        assert (ops.path / "seed.txt").exists()

    def test_fetch_private_repo(self, cloned_private_repo: Path) -> None:
        """Fetching from private repo should work with credentials."""
        ops = GitOps(cloned_private_repo)

        # Uses system credential helper
        ops.fetch("origin")


class TestCredentialCallback:
    """Tests for SystemCredentialCallback behavior."""

    def test_get_default_callbacks_returns_instance(self) -> None:
        """get_default_callbacks should return SystemCredentialCallback."""
        cb = get_default_callbacks()

        assert isinstance(cb, SystemCredentialCallback)

    def test_credential_callback_instantiation(self) -> None:
        """SystemCredentialCallback should instantiate without error."""
        cb = SystemCredentialCallback()

        assert cb is not None
