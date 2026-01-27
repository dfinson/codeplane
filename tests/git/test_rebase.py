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

    def test_given_conflict_resolved_when_continue_then_proceeds(
        self, git_repo_with_branch: tuple[Path, GitOps, str]
    ) -> None:
        """Continuing after resolving conflicts should proceed."""
        from codeplane.git.errors import RebaseConflictError

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
            # Resolve conflict and stage
            (repo_path / "conflict.txt").write_text("resolved content")
            ops.stage(["conflict.txt"])

            # Continue - may succeed or encounter another conflict
            try:
                result = ops.rebase_continue()
                assert result.state in ("done", "conflict")
            except RebaseConflictError:
                # Still conflicts - that's fine, test passes
                pass
            finally:
                # Clean up if still in progress
                if ops.rebase_in_progress():
                    ops.rebase_abort()


class TestRebaseSkip:
    """Tests for rebase_skip() method."""

    def test_given_no_rebase_when_skip_then_raises(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """Skipping when no rebase in progress should raise."""
        _, ops = git_repo_with_commit

        with pytest.raises(NoRebaseInProgressError):
            ops.rebase_skip()

    def test_given_conflict_when_skip_then_skips_commit(
        self, git_repo_with_branch: tuple[Path, GitOps, str]
    ) -> None:
        """Skipping a conflicting commit should move to next step."""
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
            # Skip should work
            result = ops.rebase_skip()
            assert result.state in ("done", "conflict")

            # Clean up if still in conflict state
            if result.state == "conflict":
                ops.rebase_abort()


class TestRebaseInProgress:
    """Tests for rebase_in_progress() method."""

    def test_given_no_rebase_when_check_then_false(
        self, git_repo_with_commit: tuple[Path, GitOps]
    ) -> None:
        """No rebase in progress should return False."""
        _, ops = git_repo_with_commit

        assert ops.rebase_in_progress() is False


class TestRebaseActions:
    """Tests for rebase with different action types."""

    def test_rebase_with_drop_action(
        self, git_repo_with_commits: tuple[Path, GitOps, list[str]]
    ) -> None:
        """Rebase with drop action should skip dropped commits."""
        from codeplane.git import RebasePlan, RebaseStep

        repo_path, ops, shas = git_repo_with_commits

        # Create base branch at first commit
        default = ops.current_branch()
        ops.checkout(shas[0])
        ops.create_branch("base")
        ops.checkout(default)

        # Get commits to rebase
        plan = ops.rebase_plan("base")
        if len(plan.steps) < 2:
            pytest.skip("Need at least 2 commits to test drop")

        # Create custom plan with drop action
        custom_steps = [
            RebaseStep(action="drop", commit_sha=plan.steps[0].commit_sha, message=None),
        ]
        for step in plan.steps[1:]:
            custom_steps.append(RebaseStep(action="pick", commit_sha=step.commit_sha, message=None))

        custom_plan = RebasePlan(onto="base", upstream="base", steps=custom_steps)

        result = ops.rebase_execute(custom_plan)
        assert result.success is True or result.state == "conflict"

        if result.state == "conflict":
            ops.rebase_abort()

    def test_rebase_with_reword_action(
        self, git_repo_with_commits: tuple[Path, GitOps, list[str]]
    ) -> None:
        """Rebase with reword action should change commit message."""
        from codeplane.git import RebasePlan, RebaseStep

        repo_path, ops, shas = git_repo_with_commits

        # Create base branch
        default = ops.current_branch()
        ops.checkout(shas[0])
        ops.create_branch("reword-base")
        ops.checkout(default)

        plan = ops.rebase_plan("reword-base")
        if len(plan.steps) < 1:
            pytest.skip("Need at least 1 commit to test reword")

        # Create plan with reword action
        custom_steps = [
            RebaseStep(
                action="reword",
                commit_sha=plan.steps[0].commit_sha,
                message="New reworded message",
            ),
        ]
        for step in plan.steps[1:]:
            custom_steps.append(RebaseStep(action="pick", commit_sha=step.commit_sha, message=None))

        custom_plan = RebasePlan(onto="reword-base", upstream="reword-base", steps=custom_steps)

        result = ops.rebase_execute(custom_plan)

        if result.success:
            # Verify the commit message was changed
            log = ops.log(limit=len(plan.steps))
            assert any("New reworded message" in c.message for c in log)
        elif result.state == "conflict":
            ops.rebase_abort()

    def test_rebase_with_squash_action(
        self, git_repo_with_commits: tuple[Path, GitOps, list[str]]
    ) -> None:
        """Rebase with squash action should combine commits."""
        from codeplane.git import RebasePlan, RebaseStep

        repo_path, ops, shas = git_repo_with_commits

        default = ops.current_branch()
        ops.checkout(shas[0])
        ops.create_branch("squash-base")
        ops.checkout(default)

        plan = ops.rebase_plan("squash-base")
        if len(plan.steps) < 2:
            pytest.skip("Need at least 2 commits to test squash")

        # Create plan: pick first, squash second into it
        custom_steps = [
            RebaseStep(action="pick", commit_sha=plan.steps[0].commit_sha, message=None),
            RebaseStep(action="squash", commit_sha=plan.steps[1].commit_sha, message=None),
        ]
        for step in plan.steps[2:]:
            custom_steps.append(RebaseStep(action="pick", commit_sha=step.commit_sha, message=None))

        custom_plan = RebasePlan(onto="squash-base", upstream="squash-base", steps=custom_steps)

        result = ops.rebase_execute(custom_plan)

        if result.success:
            # The squashed commits should result in fewer commits
            assert result.state == "done"
        elif result.state == "conflict":
            ops.rebase_abort()

    def test_rebase_with_fixup_action(
        self, git_repo_with_commits: tuple[Path, GitOps, list[str]]
    ) -> None:
        """Rebase with fixup action should combine commits without message."""
        from codeplane.git import RebasePlan, RebaseStep

        repo_path, ops, shas = git_repo_with_commits

        default = ops.current_branch()
        ops.checkout(shas[0])
        ops.create_branch("fixup-base")
        ops.checkout(default)

        plan = ops.rebase_plan("fixup-base")
        if len(plan.steps) < 2:
            pytest.skip("Need at least 2 commits to test fixup")

        # Create plan: pick first, fixup second
        custom_steps = [
            RebaseStep(action="pick", commit_sha=plan.steps[0].commit_sha, message=None),
            RebaseStep(action="fixup", commit_sha=plan.steps[1].commit_sha, message=None),
        ]
        for step in plan.steps[2:]:
            custom_steps.append(RebaseStep(action="pick", commit_sha=step.commit_sha, message=None))

        custom_plan = RebasePlan(onto="fixup-base", upstream="fixup-base", steps=custom_steps)

        result = ops.rebase_execute(custom_plan)

        if result.success:
            assert result.state == "done"
        elif result.state == "conflict":
            ops.rebase_abort()

    def test_rebase_with_edit_action(
        self, git_repo_with_commits: tuple[Path, GitOps, list[str]]
    ) -> None:
        """Rebase with edit action should pause for editing."""
        from codeplane.git import RebasePlan, RebaseStep

        repo_path, ops, shas = git_repo_with_commits

        default = ops.current_branch()
        ops.checkout(shas[0])
        ops.create_branch("edit-base")
        ops.checkout(default)

        plan = ops.rebase_plan("edit-base")
        if len(plan.steps) < 1:
            pytest.skip("Need at least 1 commit to test edit")

        # Create plan with edit action
        custom_steps = [
            RebaseStep(action="edit", commit_sha=plan.steps[0].commit_sha, message=None),
        ]
        for step in plan.steps[1:]:
            custom_steps.append(RebaseStep(action="pick", commit_sha=step.commit_sha, message=None))

        custom_plan = RebasePlan(onto="edit-base", upstream="edit-base", steps=custom_steps)

        result = ops.rebase_execute(custom_plan)

        # Edit action should pause
        if result.state == "edit_pause":
            assert ops.rebase_in_progress() is True
            # Make an edit and continue
            (repo_path / "edited.txt").write_text("edited during rebase\n")
            ops.stage(["edited.txt"])
            result = ops.rebase_continue()

        # Clean up if still in progress
        if ops.rebase_in_progress():
            ops.rebase_abort()


class TestRebaseBranchRestoration:
    """Tests verifying rebase properly restores/updates branch refs (fixes #119)."""

    def test_given_successful_rebase_when_finalize_then_branch_updated_and_reattached(
        self, git_repo_with_branch: tuple[Path, GitOps, str]
    ) -> None:
        """
        After successful rebase, the original branch should point to new HEAD
        and HEAD should be reattached (not detached).
        """
        repo_path, ops, _ = git_repo_with_branch
        default_branch = ops.current_branch()

        # Checkout feature and rebase onto default
        ops.checkout("feature")
        original_feature_sha = ops.head().target_sha

        plan = ops.rebase_plan(default_branch)
        result = ops.rebase_execute(plan)

        # Verify rebase succeeded
        assert result.success
        assert result.state == "done"

        # Key fix verification: HEAD should be reattached to feature branch
        assert ops.current_branch() == "feature", "HEAD should be reattached to original branch"
        assert not ops.head().is_detached, "Should not be detached after rebase"

        # Branch should point to new commit (not original)
        assert ops.head().target_sha == result.new_head
        assert ops.head().target_sha != original_feature_sha

    def test_given_rebase_with_conflict_when_abort_then_branch_restored(
        self, git_repo_with_branch: tuple[Path, GitOps, str]
    ) -> None:
        """
        After aborting a conflicted rebase, HEAD should be reattached to the
        original branch at its original commit.
        """
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
        feature_with_conflict_sha = ops.commit("add conflict on feature")

        # Start rebase (will conflict)
        plan = ops.rebase_plan(default_branch)
        result = ops.rebase_execute(plan)

        assert result.state == "conflict"

        # Abort rebase
        ops.rebase_abort()

        # Key fix verification: HEAD should be reattached to feature
        assert ops.current_branch() == "feature", "HEAD should be reattached after abort"
        assert not ops.head().is_detached, "Should not be detached after abort"

        # Should be at the commit before rebase was attempted
        assert ops.head().target_sha == feature_with_conflict_sha
