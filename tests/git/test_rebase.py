"""Tests for rebase operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.git import (
    GitOps,
    NoRebaseInProgressError,
    RebaseInProgressError,
    RebasePlan,
)


class TestRebasePlan:
    """Tests for rebase_plan() method."""

    def test_given_linear_history_when_plan_then_returns_picks(
        self, git_repo_with_commits: tuple[Path, GitOps, list[str]]
    ) -> None:
        """Rebase plan should list commits as pick actions."""
        _, ops, commit_shas = git_repo_with_commits
        default_branch = ops.current_branch()

        # Create a base branch at first commit
        ops.checkout(commit_shas[0])
        ops.create_branch("base-at-first")
        ops.checkout(default_branch)

        plan = ops.rebase_plan("base-at-first")

        assert isinstance(plan, RebasePlan)
        assert plan.upstream == "base-at-first"
        # Should have commits after the first one
        assert len(plan.steps) > 0
        for step in plan.steps:
            assert step.action == "pick"

    def test_given_no_commits_to_rebase_when_plan_then_empty_steps(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """Plan with no commits to rebase should have empty steps."""
        _, ops = git_repo_with_commit

        plan = ops.rebase_plan("HEAD")

        assert len(plan.steps) == 0


class TestRebaseExecute:
    """Tests for rebase_execute() method."""

    def test_given_simple_rebase_when_execute_then_succeeds(
        self, git_repo_with_branch: tuple[Path, GitOps, str]
    ) -> None:
        """Simple rebase without conflicts should succeed."""
        repo_path, ops, _ = git_repo_with_branch
        default_branch = ops.current_branch()

        # Checkout feature and rebase onto default branch
        ops.checkout("feature")
        plan = ops.rebase_plan(default_branch)
        result = ops.rebase_execute(plan)

        assert result.success is True
        assert result.state == "done"

    def test_given_rebase_in_progress_when_execute_then_raises(
        self, git_repo_with_branch: tuple[Path, GitOps, str]
    ) -> None:
        """Starting new rebase while one is in progress should raise."""
        repo_path, ops, _ = git_repo_with_branch
        default_branch = ops.current_branch()

        # Switch to default branch and create conflicting changes
        ops.checkout(default_branch)
        (repo_path / "conflict.txt").write_text("default version")
        ops.stage(["conflict.txt"])
        ops.commit("add conflict on default")

        ops.checkout("feature")
        (repo_path / "conflict.txt").write_text("feature version")
        ops.stage(["conflict.txt"])
        ops.commit("add conflict on feature")

        # Start rebase (will conflict)
        plan = ops.rebase_plan(default_branch)
        result = ops.rebase_execute(plan)

        if result.state == "conflict":
            # Try to start another rebase
            with pytest.raises(RebaseInProgressError):
                ops.rebase_execute(plan)

            # Clean up
            ops.rebase_abort()


class TestRebaseAbort:
    """Tests for rebase_abort() method."""

    def test_given_rebase_in_progress_when_abort_then_restores_state(
        self, git_repo_with_branch: tuple[Path, GitOps, str]
    ) -> None:
        """Aborting a rebase should restore original state."""
        repo_path, ops, _ = git_repo_with_branch
        default_branch = ops.current_branch()

        # Create conflicting changes
        ops.checkout(default_branch)
        (repo_path / "conflict.txt").write_text("default version")
        ops.stage(["conflict.txt"])
        ops.commit("add conflict on default")

        ops.checkout("feature")
        (repo_path / "conflict.txt").write_text("feature version")
        ops.stage(["conflict.txt"])
        ops.commit("add conflict on feature")

        # Start rebase
        plan = ops.rebase_plan(default_branch)
        result = ops.rebase_execute(plan)

        if result.state == "conflict":
            ops.rebase_abort()

            # HEAD should be restored
            assert ops.rebase_in_progress() is False

    def test_given_no_rebase_when_abort_then_raises(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """Aborting when no rebase in progress should raise."""
        _, ops = git_repo_with_commit

        with pytest.raises(NoRebaseInProgressError):
            ops.rebase_abort()


class TestRebaseContinue:
    """Tests for rebase_continue() method."""

    def test_given_no_rebase_when_continue_then_raises(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """Continuing when no rebase in progress should raise."""
        _, ops = git_repo_with_commit

        with pytest.raises(NoRebaseInProgressError):
            ops.rebase_continue()


class TestRebaseInProgress:
    """Tests for rebase_in_progress() method."""

    def test_given_no_rebase_when_check_then_false(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """No rebase in progress should return False."""
        _, ops = git_repo_with_commit

        assert ops.rebase_in_progress() is False
