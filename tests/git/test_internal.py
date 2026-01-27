"""Tests for git internal modules: parsing, errors, preconditions."""

from __future__ import annotations

from pathlib import Path

import pygit2
import pytest

from codeplane.git._internal.errors import ErrorMapper, git_operation
from codeplane.git._internal.parsing import (
    extract_branch_name,
    extract_local_branch_from_remote,
    extract_tag_name,
    first_line,
    make_branch_ref,
    make_tag_ref,
)
from codeplane.git._internal.preconditions import (
    check_nothing_to_commit,
    require_branch_exists,
    require_current_branch,
    require_not_current_branch,
    require_not_unborn,
)
from codeplane.git.errors import (
    AuthenticationError,
    BranchNotFoundError,
    DetachedHeadError,
    GitError,
    NothingToCommitError,
    RemoteError,
)
from codeplane.git.ops import GitOps

# =============================================================================
# Parsing Tests
# =============================================================================


class TestExtractLocalBranchFromRemote:
    @pytest.mark.parametrize(
        ("remote_ref", "expected"),
        [
            ("origin/main", "main"),
            ("origin/feature/branch", "feature/branch"),
            ("upstream/develop", "develop"),
            ("main", "main"),  # no slash
        ],
    )
    def test_extracts_branch_name(self, remote_ref: str, expected: str) -> None:
        assert extract_local_branch_from_remote(remote_ref) == expected


class TestExtractTagName:
    @pytest.mark.parametrize(
        ("refname", "expected"),
        [
            ("refs/tags/v1.0", "v1.0"),
            ("refs/tags/release-2.0", "release-2.0"),
            ("refs/heads/main", None),
            ("v1.0", None),
        ],
    )
    def test_extracts_tag_name(self, refname: str, expected: str | None) -> None:
        assert extract_tag_name(refname) == expected


class TestExtractBranchName:
    @pytest.mark.parametrize(
        ("refname", "expected"),
        [
            ("refs/heads/main", "main"),
            ("refs/heads/feature/branch", "feature/branch"),
            ("refs/tags/v1.0", None),
            ("main", None),
        ],
    )
    def test_extracts_branch_name(self, refname: str, expected: str | None) -> None:
        assert extract_branch_name(refname) == expected


class TestFirstLine:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("single line", "single line"),
            ("first\nsecond\nthird", "first"),
            ("", ""),
            ("line with\nmore", "line with"),
        ],
    )
    def test_returns_first_line(self, text: str, expected: str) -> None:
        assert first_line(text) == expected


class TestMakeRefs:
    def test_make_tag_ref(self) -> None:
        assert make_tag_ref("v1.0") == "refs/tags/v1.0"

    def test_make_branch_ref(self) -> None:
        assert make_branch_ref("main") == "refs/heads/main"


# =============================================================================
# Error Mapping Tests
# =============================================================================


class TestErrorMapper:
    def test_guard_passes_on_success(self) -> None:
        with ErrorMapper.guard("test"):
            pass  # Should not raise

    def test_guard_maps_git_error(self) -> None:
        with pytest.raises(GitError, match="test error"), ErrorMapper.guard("test"):
            raise pygit2.GitError("test error")

    def test_guard_maps_auth_error_with_remote(self) -> None:
        with (
            pytest.raises(AuthenticationError),
            ErrorMapper.guard("fetch", remote="origin"),
        ):
            raise pygit2.GitError("authentication failed")

    def test_guard_maps_remote_error(self) -> None:
        with pytest.raises(RemoteError), ErrorMapper.guard("push", remote="origin"):
            raise pygit2.GitError("connection refused")


class TestGitOperationDecorator:
    def test_returns_context_manager(self) -> None:
        cm = git_operation("test")
        with cm:
            pass  # Should work


# =============================================================================
# Precondition Tests
# =============================================================================


class TestRequireNotUnborn:
    def test_raises_on_unborn(self, tmp_path: Path) -> None:
        # Create empty repo with no commits
        pygit2.init_repository(tmp_path)
        ops = GitOps(tmp_path)
        with pytest.raises(GitError, match="no commits yet"):
            require_not_unborn(ops._access, "merge")

    def test_passes_with_commit(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        require_not_unborn(ops._access, "merge")  # Should not raise


class TestRequireCurrentBranch:
    def test_returns_branch_name(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        branch = require_current_branch(ops._access, "push")
        assert branch == "main"

    def test_raises_on_detached(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        # Detach HEAD
        ops._access.repo.set_head(ops._access.repo.head.target)
        with pytest.raises(DetachedHeadError):
            require_current_branch(ops._access, "push")


class TestRequireNotCurrentBranch:
    def test_passes_for_different_branch(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        ops.create_branch("other")
        require_not_current_branch(ops._access, "other")  # Should not raise

    def test_raises_for_current_branch(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        with pytest.raises(GitError, match="Cannot delete current branch"):
            require_not_current_branch(ops._access, "main")


class TestRequireBranchExists:
    def test_passes_for_existing_branch(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        require_branch_exists(ops._access, "main")  # Should not raise

    def test_raises_for_nonexistent_branch(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        with pytest.raises(BranchNotFoundError):
            require_branch_exists(ops._access, "nonexistent")


class TestCheckNothingToCommit:
    def test_passes_when_allow_empty(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        check_nothing_to_commit(ops._access, allow_empty=True)  # Should not raise

    def test_raises_when_nothing_to_commit(self, temp_repo: pygit2.Repository) -> None:
        ops = GitOps(temp_repo.workdir)
        with pytest.raises(NothingToCommitError):
            check_nothing_to_commit(ops._access, allow_empty=False)

    def test_passes_when_staged_changes(self, repo_with_uncommitted: pygit2.Repository) -> None:
        ops = GitOps(repo_with_uncommitted.workdir)
        check_nothing_to_commit(ops._access, allow_empty=False)  # Should not raise

    def test_raises_on_unborn_empty_index(self, tmp_path: Path) -> None:
        # Create empty repo with no commits
        pygit2.init_repository(tmp_path)
        ops = GitOps(tmp_path)
        with pytest.raises(NothingToCommitError):
            check_nothing_to_commit(ops._access, allow_empty=False)
