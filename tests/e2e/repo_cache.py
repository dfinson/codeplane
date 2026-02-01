"""Repository cache and materialization for E2E tests.

Implements shallow clone caching per E2E_TEST_PROPOSALS.md:
- Clone with --depth=1
- Checkout pinned SHA/tag
- Cache validation and auto-repair
- Copy to tmp for mutation tests
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_CACHE = Path.home() / ".codeplane-test-cache"


@dataclass
class RepoCase:
    """Test case for a repository."""

    owner: str
    name: str
    commit: str  # SHA or tag
    tier: int = 1

    @property
    def key(self) -> str:
        """Unique key for budgets/anchors lookup."""
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        """GitHub clone URL."""
        return f"https://github.com/{self.owner}/{self.name}.git"

    @property
    def slug(self) -> str:
        """Filesystem-safe slug."""
        return f"{self.owner}_{self.name}"


def repo_slug(repo_url: str) -> str:
    """Convert repo URL to filesystem-safe slug."""
    # https://github.com/owner/name.git -> owner_name
    parts = repo_url.rstrip(".git").split("/")
    return f"{parts[-2]}_{parts[-1]}"


def is_git_repo_healthy(path: Path) -> bool:
    """Check if git repo is valid and not corrupted."""
    if not (path / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "fsck", "--no-progress"],
            cwd=path,
            capture_output=True,
            timeout=60,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def git_head(path: Path) -> str:
    """Get current HEAD commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_clone_shallow(repo_url: str, dest: Path, ref: str) -> None:
    """Clone repo with depth=1 at specific ref."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth=1", "--branch", ref, repo_url, str(dest)],
        check=True,
        capture_output=True,
    )


def git_fetch_and_checkout(path: Path, ref: str) -> None:
    """Fetch and checkout a specific ref."""
    subprocess.run(
        ["git", "fetch", "--depth=1", "origin", ref],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "FETCH_HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def ensure_repo_cached(case: RepoCase) -> Path:
    """Ensure repo is cached and at correct commit.

    Returns path to cached repo (read-only, do not modify).
    """
    cache_path = REPO_CACHE / case.slug

    if cache_path.exists():
        if not is_git_repo_healthy(cache_path):
            shutil.rmtree(cache_path)
        else:
            # Check if at correct ref
            current = git_head(cache_path)
            # For tags, we need to resolve to SHA
            try:
                result = subprocess.run(
                    ["git", "rev-parse", case.commit],
                    cwd=cache_path,
                    capture_output=True,
                    text=True,
                )
                expected_sha = result.stdout.strip()
                if current != expected_sha:
                    git_fetch_and_checkout(cache_path, case.commit)
            except subprocess.CalledProcessError:
                # Tag not found, re-clone
                shutil.rmtree(cache_path)

    if not cache_path.exists():
        git_clone_shallow(case.url, cache_path, case.commit)

    return cache_path


def materialize_repo(case: RepoCase, dest: Path) -> Path:
    """Copy cached repo to destination for mutation tests.

    Args:
        case: Repository case
        dest: Destination directory (e.g., tmp_path)

    Returns:
        Path to materialized repo copy
    """
    cache_path = ensure_repo_cached(case)
    repo_path = dest / case.slug

    # Copy entire repo (fast for shallow clones)
    # Use symlinks=True to preserve symlinks, ignore_dangling_symlinks to skip broken ones
    shutil.copytree(cache_path, repo_path, symlinks=True, ignore_dangling_symlinks=True)

    # Remove any existing .codeplane directory from cache copy
    # so that `cpl init` can properly initialize
    codeplane_dir = repo_path / ".codeplane"
    if codeplane_dir.exists():
        shutil.rmtree(codeplane_dir)

    return repo_path
