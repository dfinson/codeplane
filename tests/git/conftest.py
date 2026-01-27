"""Test fixtures for git module."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pygit2
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def temp_repo(tmp_path: Path) -> Generator[pygit2.Repository, None, None]:
    """Create a temporary git repository with initial commit."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    repo = pygit2.init_repository(str(repo_path), initial_head="main")

    # Configure user
    repo.config["user.name"] = "Test User"
    repo.config["user.email"] = "test@example.com"

    # Create initial commit
    (repo_path / "README.md").write_text("# Test Repo\n")
    repo.index.add("README.md")
    repo.index.write()
    tree = repo.index.write_tree()
    sig = pygit2.Signature("Test User", "test@example.com")
    repo.create_commit("refs/heads/main", sig, sig, "Initial commit", tree, [])

    # Set HEAD to main
    repo.set_head("refs/heads/main")

    yield repo


@pytest.fixture
def bare_repo(tmp_path: Path) -> Generator[pygit2.Repository, None, None]:
    """Create a bare repository for remote testing."""
    bare_path = tmp_path / "bare.git"
    yield pygit2.init_repository(str(bare_path), bare=True)


@pytest.fixture
def repo_with_remote(
    temp_repo: pygit2.Repository,
    bare_repo: pygit2.Repository,
) -> pygit2.Repository:
    """Repository with a configured remote."""
    temp_repo.remotes.create("origin", str(Path(bare_repo.path).resolve()))

    # Push initial commit
    remote = temp_repo.remotes["origin"]
    remote.push(["refs/heads/main:refs/heads/main"])

    return temp_repo


@pytest.fixture
def repo_with_branches(temp_repo: pygit2.Repository) -> pygit2.Repository:
    """Repository with multiple branches."""
    workdir = Path(temp_repo.workdir)

    # Create feature branch
    head_commit = temp_repo.head.peel(pygit2.Commit)
    temp_repo.branches.local.create("feature", head_commit)

    # Add commit on main
    (workdir / "main.txt").write_text("main branch\n")
    temp_repo.index.add("main.txt")
    temp_repo.index.write()
    tree = temp_repo.index.write_tree()
    sig = temp_repo.default_signature
    temp_repo.create_commit("HEAD", sig, sig, "Commit on main", tree, [temp_repo.head.target])

    # Checkout feature and add commit
    feature = temp_repo.branches.local["feature"]
    temp_repo.checkout(feature)
    (workdir / "feature.txt").write_text("feature branch\n")
    temp_repo.index.add("feature.txt")
    temp_repo.index.write()
    tree = temp_repo.index.write_tree()
    temp_repo.create_commit("HEAD", sig, sig, "Commit on feature", tree, [temp_repo.head.target])

    # Back to main
    temp_repo.checkout(temp_repo.branches.local["main"])

    return temp_repo


@pytest.fixture
def repo_with_uncommitted(temp_repo: pygit2.Repository) -> pygit2.Repository:
    """Repository with uncommitted changes."""
    workdir = Path(temp_repo.workdir)

    # Staged change
    (workdir / "staged.txt").write_text("staged content\n")
    temp_repo.index.add("staged.txt")
    temp_repo.index.write()

    # Modified (unstaged)
    (workdir / "README.md").write_text("# Modified\n")

    # Untracked
    (workdir / "untracked.txt").write_text("untracked\n")

    return temp_repo


@pytest.fixture
def repo_with_conflict(
    temp_repo: pygit2.Repository,
) -> tuple[pygit2.Repository, str]:
    """Repository with a merge conflict."""
    workdir = Path(temp_repo.workdir)
    sig = temp_repo.default_signature

    # Create branch from initial commit
    head_commit = temp_repo.head.peel(pygit2.Commit)
    temp_repo.branches.local.create("conflict-branch", head_commit)

    # Modify on main
    (workdir / "conflict.txt").write_text("main content\n")
    temp_repo.index.add("conflict.txt")
    temp_repo.index.write()
    tree = temp_repo.index.write_tree()
    temp_repo.create_commit(
        "HEAD", sig, sig, "Add conflict.txt on main", tree, [temp_repo.head.target]
    )

    # Checkout branch and create conflicting change
    temp_repo.checkout(temp_repo.branches.local["conflict-branch"])
    (workdir / "conflict.txt").write_text("branch content\n")
    temp_repo.index.add("conflict.txt")
    temp_repo.index.write()
    tree = temp_repo.index.write_tree()
    temp_repo.create_commit(
        "HEAD", sig, sig, "Add conflict.txt on branch", tree, [temp_repo.head.target]
    )

    # Back to main
    temp_repo.checkout(temp_repo.branches.local["main"])

    return temp_repo, "conflict-branch"


@pytest.fixture
def repo_with_history(temp_repo: pygit2.Repository) -> pygit2.Repository:
    """Repository with multiple commits."""
    workdir = Path(temp_repo.workdir)
    sig = temp_repo.default_signature

    for i in range(5):
        (workdir / f"file{i}.txt").write_text(f"content {i}\n")
        temp_repo.index.add(f"file{i}.txt")
        temp_repo.index.write()
        tree = temp_repo.index.write_tree()
        temp_repo.create_commit("HEAD", sig, sig, f"Commit {i}", tree, [temp_repo.head.target])

    return temp_repo
