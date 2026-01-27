"""Test fixtures for git module."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pygit2
import pytest

from codeplane.git import GitOps

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


# --- GitOps-returning fixtures ---


@pytest.fixture
def git_repo(tmp_path: Path) -> Generator[tuple[Path, GitOps], None, None]:
    """GitOps wrapper around a fresh repository with initial commit."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = pygit2.init_repository(repo_path)

    # Configure user for default_signature
    repo.config["user.name"] = "Test User"
    repo.config["user.email"] = "test@example.com"

    sig = pygit2.Signature("Test User", "test@example.com")
    (repo_path / "README.md").write_text("# Test\n")
    repo.index.add("README.md")
    repo.index.write()
    tree = repo.index.write_tree()
    repo.create_commit("HEAD", sig, sig, "Initial commit", tree, [])
    yield repo_path, GitOps(repo_path)


@pytest.fixture
def git_repo_with_commit(git_repo: tuple[Path, GitOps]) -> tuple[Path, GitOps]:
    """GitOps repo with one additional commit beyond initial."""
    repo_path, ops = git_repo
    (repo_path / "file.txt").write_text("content\n")
    ops.stage(["file.txt"])
    ops.commit("Add file")
    return repo_path, ops


@pytest.fixture
def git_repo_with_commits(git_repo: tuple[Path, GitOps]) -> tuple[Path, GitOps, list[str]]:
    """GitOps repo with multiple commits for rebase testing."""
    repo_path, ops = git_repo
    shas = []
    for i in range(5):
        (repo_path / f"file{i}.txt").write_text(f"content {i}\n")
        ops.stage([f"file{i}.txt"])
        sha = ops.commit(f"Commit {i}")
        shas.append(sha)
    return repo_path, ops, shas


@pytest.fixture
def git_repo_with_branch(git_repo: tuple[Path, GitOps]) -> tuple[Path, GitOps, str]:
    """GitOps repo with a feature branch diverged from default branch."""
    repo_path, ops = git_repo
    # Get current branch name (could be master or main depending on git config)
    default_branch = ops.current_branch()

    # Add a commit on default branch
    (repo_path / "default.txt").write_text("default content\n")
    ops.stage(["default.txt"])
    ops.commit("Default branch commit")
    default_head = ops.head()

    # Create feature branch from initial commit
    initial_sha = ops.log()[1].sha  # second commit is initial
    ops.create_branch("feature", initial_sha)
    ops.checkout("feature")

    # Add commits on feature
    (repo_path / "feature.txt").write_text("feature content\n")
    ops.stage(["feature.txt"])
    ops.commit("Feature commit")

    # Return to default branch
    ops.checkout(default_branch)

    return repo_path, ops, default_head.target_sha


@pytest.fixture
def git_repo_pair(
    tmp_path: Path,
) -> Generator[tuple[tuple[Path, GitOps], tuple[Path, GitOps]], None, None]:
    """Two separate repos for testing operations requiring multiple repositories.

    Note: Enables file:// protocol for submodule tests (required by modern git).
    """
    # First repo (main)
    main_path = tmp_path / "main_repo"
    main_path.mkdir()
    main_repo = pygit2.init_repository(main_path)
    main_repo.config["user.name"] = "Test User"
    main_repo.config["user.email"] = "test@example.com"
    # Enable file:// protocol for submodule operations
    main_repo.config["protocol.file.allow"] = "always"
    sig = pygit2.Signature("Test User", "test@example.com")
    (main_path / "README.md").write_text("# Main Repo\n")
    main_repo.index.add("README.md")
    main_repo.index.write()
    tree = main_repo.index.write_tree()
    main_repo.create_commit("HEAD", sig, sig, "Initial commit", tree, [])

    # Second repo (to be used as submodule source)
    sub_path = tmp_path / "sub_repo"
    sub_path.mkdir()
    sub_repo = pygit2.init_repository(sub_path)
    sub_repo.config["user.name"] = "Test User"
    sub_repo.config["user.email"] = "test@example.com"
    (sub_path / "lib.py").write_text("# Library\n")
    sub_repo.index.add("lib.py")
    sub_repo.index.write()
    tree = sub_repo.index.write_tree()
    sub_repo.create_commit("HEAD", sig, sig, "Initial lib commit", tree, [])

    yield (main_path, GitOps(main_path)), (sub_path, GitOps(sub_path))
