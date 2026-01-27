"""Pytest configuration for git integration tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

# Test repository URLs
PUBLIC_REPO = "https://github.com/dfinson/codeplane-test-public.git"
PRIVATE_REPO = "https://github.com/dfinson/codeplane-test-private.git"

PUBLIC_REPO_SSH = "git@github.com:dfinson/codeplane-test-public.git"
PRIVATE_REPO_SSH = "git@github.com:dfinson/codeplane-test-private.git"


def _get_private_repo_url() -> str:
    """Get private repo URL, with token embedded if available from CI."""
    token = os.environ.get("CODEPLANE_TEST_PRIVATE_TOKEN")
    if token:
        return f"https://x-access-token:{token}@github.com/dfinson/codeplane-test-private.git"
    return PRIVATE_REPO


def _can_access_repo(url: str, timeout: int = 10) -> bool:
    """Check if we can access a repository."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", url],
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


@pytest.fixture(scope="session")
def public_repo_accessible() -> bool:
    """Check if public test repo is accessible."""
    return _can_access_repo(PUBLIC_REPO)


@pytest.fixture(scope="session")
def private_repo_accessible() -> bool:
    """Check if private test repo is accessible (requires auth)."""
    return _can_access_repo(_get_private_repo_url())


@pytest.fixture
def cloned_public_repo(tmp_path: Path, public_repo_accessible: bool) -> Generator[Path, None, None]:
    """Clone public test repo to temp directory."""
    if not public_repo_accessible:
        pytest.skip("Public test repo not accessible")

    repo_path = tmp_path / "public-clone"
    subprocess.run(
        ["git", "clone", PUBLIC_REPO, str(repo_path)],
        capture_output=True,
        check=True,
    )
    yield repo_path


@pytest.fixture
def cloned_private_repo(
    tmp_path: Path, private_repo_accessible: bool
) -> Generator[Path, None, None]:
    """Clone private test repo to temp directory (requires auth)."""
    if not private_repo_accessible:
        pytest.skip("Private test repo not accessible (auth required)")

    repo_path = tmp_path / "private-clone"
    subprocess.run(
        ["git", "clone", _get_private_repo_url(), str(repo_path)],
        capture_output=True,
        check=True,
    )
    yield repo_path


@pytest.fixture
def local_bare_remote(tmp_path: Path) -> Generator[tuple[Path, Path], None, None]:
    """
    Create a local bare repo as a "remote" and a working clone.

    Returns (work_path, bare_path).
    """
    bare = tmp_path / "origin.git"
    bare.mkdir()
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main"],
        cwd=bare,
        capture_output=True,
        check=True,
    )

    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main"], cwd=work, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(bare)], cwd=work, capture_output=True, check=True
    )

    # Seed with initial commit
    (work / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "-A"], cwd=work, capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@test.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "Initial",
        ],
        cwd=work,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "main"], cwd=work, capture_output=True, check=True
    )

    # Create a remote branch
    subprocess.run(
        ["git", "checkout", "-b", "feature/remote-test"], cwd=work, capture_output=True, check=True
    )
    (work / "feature.txt").write_text("feature content\n")
    subprocess.run(["git", "add", "-A"], cwd=work, capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@test.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "Add feature",
        ],
        cwd=work,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "push", "origin", "feature/remote-test"], cwd=work, capture_output=True, check=True
    )
    subprocess.run(["git", "checkout", "main"], cwd=work, capture_output=True, check=True)

    yield work, bare
