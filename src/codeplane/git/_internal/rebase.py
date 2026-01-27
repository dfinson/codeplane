"""Interactive rebase implementation using low-level pygit2 operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pygit2

from codeplane.git._internal.constants import RESET_HARD, SORT_REVERSE, SORT_TOPOLOGICAL
from codeplane.git.errors import (
    GitError,
    NoRebaseInProgressError,
    RebaseConflictError,
    RebaseError,
    RebaseInProgressError,
    RefNotFoundError,
)
from codeplane.git.models import RebasePlan, RebaseResult, RebaseStep

if TYPE_CHECKING:
    from codeplane.git._internal.access import RepoAccess


# Rebase state file stored in .git/
REBASE_STATE_FILE = "codeplane-rebase-state.json"


@dataclass
class RebaseState:
    """Persisted rebase state for recovery."""

    original_head: str
    original_branch: str | None  # Branch name before detach (None if already detached)
    onto: str
    steps: list[dict[str, str | None]]  # Serialized RebaseStep
    current_step: int
    completed_commits: list[str]  # SHAs of commits created so far


class RebasePlanner:
    """Generates rebase plans from commit ranges."""

    def __init__(self, access: RepoAccess) -> None:
        self._access = access

    def plan(self, upstream: str, onto: str | None = None) -> RebasePlan:
        """
        Generate a rebase plan.

        Args:
            upstream: The upstream ref (commits between upstream..HEAD will be rebased)
            onto: Optional base to rebase onto (defaults to upstream)

        Returns:
            RebasePlan with all commits as "pick" actions
        """
        head_oid = self._access.must_head_target()
        upstream_oid = self._access.resolve_ref_oid(upstream)
        onto_ref = onto or upstream

        # Find commits to rebase: upstream..HEAD
        commits = self._commits_between(upstream_oid, head_oid)

        steps = tuple(
            RebaseStep(action="pick", commit_sha=str(c.id), message=c.message) for c in commits
        )

        return RebasePlan(upstream=upstream, onto=onto_ref, steps=steps)

    def _commits_between(self, exclude: pygit2.Oid, include: pygit2.Oid) -> list[pygit2.Commit]:
        """Get commits in include that are not reachable from exclude (topological order)."""
        walker = self._access.walk_commits(include, SORT_TOPOLOGICAL | SORT_REVERSE)
        walker.hide(exclude)

        commits = []
        for commit in walker:
            commits.append(commit)
        return commits


class RebaseFlow:
    """Executes rebase plans with state persistence."""

    def __init__(self, access: RepoAccess) -> None:
        self._access = access

    @property
    def _state_path(self) -> Path:
        """Path to rebase state file."""
        git_dir = Path(self._access.repo.path)
        return git_dir / REBASE_STATE_FILE

    def has_rebase_in_progress(self) -> bool:
        """Check if a rebase is in progress."""
        return self._state_path.exists()

    def execute(self, plan: RebasePlan) -> RebaseResult:
        """
        Execute a rebase plan.

        Returns RebaseResult with state indicating success, conflict, or edit_pause.
        """
        if self.has_rebase_in_progress():
            raise RebaseInProgressError()

        # Save original HEAD and branch for abort/finalize
        original_head = str(self._access.must_head_target())
        original_branch = self._access.current_branch_name()  # None if already detached

        # Resolve onto commit
        try:
            onto_oid = self._access.resolve_ref_oid(plan.onto)
        except RefNotFoundError as e:
            raise RebaseError(f"Invalid onto ref: {plan.onto}") from e

        # Checkout onto commit (detached HEAD)
        self._access.checkout_detached(onto_oid)

        # Initialize state
        state = RebaseState(
            original_head=original_head,
            original_branch=original_branch,
            onto=plan.onto,
            steps=[
                {"action": s.action, "commit_sha": s.commit_sha, "message": s.message}
                for s in plan.steps
            ],
            current_step=0,
            completed_commits=[],
        )
        self._save_state(state)

        return self._execute_steps(state)

    def continue_rebase(self) -> RebaseResult:
        """Continue a paused rebase (after conflict resolution or edit)."""
        state = self._load_state()
        if state is None:
            raise NoRebaseInProgressError()

        # Check if conflicts are resolved
        if self._has_conflicts():
            conflicts = self._get_conflict_paths()
            raise RebaseConflictError(conflicts)

        # After conflicts are resolved, we need to commit the current step
        # before continuing to the next step
        step = self._step_from_dict(state.steps[state.current_step])
        original_commit = self._access.resolve_commit(step.commit_sha)
        head_oid = self._access.must_head_target()
        tree_id = self._access.index.write_tree()

        if step.action in ("squash", "fixup") and state.completed_commits:
            # For squash/fixup after conflict, merge into previous commit
            return self._squash_into_previous(step, state, concat_message=(step.action == "squash"))

        # For pick, reword, edit - create commit from resolved state
        if step.action == "reword":
            message = step.message or original_commit.message
        else:
            message = step.message or original_commit.message

        new_oid = self._access.create_commit(
            "HEAD",
            original_commit.author,
            self._access.default_signature,
            message,
            tree_id,
            [head_oid],
        )
        state.completed_commits.append(str(new_oid))
        state.current_step += 1
        self._save_state(state)

        return self._execute_steps(state)

    def skip(self) -> RebaseResult:
        """Skip the current commit and continue."""
        state = self._load_state()
        if state is None:
            raise NoRebaseInProgressError()

        # Reset to clean state
        head_oid = self._access.must_head_target()
        self._access.reset(head_oid, RESET_HARD)
        self._access.state_cleanup()

        # Skip to next step
        state.current_step += 1
        self._save_state(state)

        return self._execute_steps(state)

    def abort(self) -> None:
        """Abort the rebase and restore original state."""
        state = self._load_state()
        if state is None:
            raise NoRebaseInProgressError()

        # Restore original HEAD
        original_oid = self._access.resolve_ref_oid(state.original_head)
        self._access.reset(original_oid, RESET_HARD)

        # Restore original branch ref if we were on one
        if state.original_branch:
            self._access.set_head(f"refs/heads/{state.original_branch}")

        self._access.state_cleanup()

        # Clean up state file
        self._state_path.unlink(missing_ok=True)

    def _execute_steps(self, state: RebaseState) -> RebaseResult:
        """Execute remaining steps in the plan."""
        total = len(state.steps)

        while state.current_step < total:
            step_dict = state.steps[state.current_step]
            step = self._step_from_dict(step_dict)

            if step.action == "drop":
                # Skip this commit entirely
                state.current_step += 1
                self._save_state(state)
                continue

            result = self._apply_step(step, state)
            if not result.success:
                return result

            state.current_step += 1
            self._save_state(state)

        # Rebase complete - update branch ref
        return self._finalize(state)

    def _apply_step(self, step: RebaseStep, state: RebaseState) -> RebaseResult:
        """Apply a single rebase step."""
        commit = self._access.resolve_commit(step.commit_sha)
        head_oid = self._access.must_head_target()
        total = len(state.steps)

        if step.action == "edit":
            # Apply changes but pause before committing
            self._cherry_pick_changes(commit)

            if self._has_conflicts():
                conflicts = self._get_conflict_paths()
                return RebaseResult(
                    success=False,
                    completed_steps=state.current_step,
                    total_steps=total,
                    state="conflict",
                    conflict_paths=tuple(conflicts),
                    current_commit=step.commit_sha,
                )

            # Pause for user edits
            return RebaseResult(
                success=False,
                completed_steps=state.current_step,
                total_steps=total,
                state="edit_pause",
                current_commit=step.commit_sha,
            )

        # For pick, reword, squash, fixup - apply and commit
        self._cherry_pick_changes(commit)

        if self._has_conflicts():
            conflicts = self._get_conflict_paths()
            return RebaseResult(
                success=False,
                completed_steps=state.current_step,
                total_steps=total,
                state="conflict",
                conflict_paths=tuple(conflicts),
                current_commit=step.commit_sha,
            )

        # Determine message based on action
        if step.action == "squash":
            # Combine with previous commit
            if state.completed_commits:
                return self._squash_into_previous(step, state, concat_message=True)
            message = step.message or commit.message
        elif step.action == "fixup":
            # Combine with previous, discard this message
            if state.completed_commits:
                return self._squash_into_previous(step, state, concat_message=False)
            message = commit.message
        elif step.action == "reword":
            message = step.message or commit.message
        else:  # pick
            message = commit.message

        # Create the commit
        tree_id = self._access.index.write_tree()
        new_oid = self._access.create_commit(
            "HEAD",
            commit.author,
            self._access.default_signature,
            message,
            tree_id,
            [head_oid],
        )
        state.completed_commits.append(str(new_oid))

        return RebaseResult(
            success=True,
            completed_steps=state.current_step + 1,
            total_steps=total,
            state="done",
        )

    def _squash_into_previous(
        self, step: RebaseStep, state: RebaseState, *, concat_message: bool
    ) -> RebaseResult:
        """Squash current changes into the previous commit."""
        commit = self._access.resolve_commit(step.commit_sha)
        total = len(state.steps)

        # Get the previous commit we're squashing into
        prev_oid = self._access.resolve_ref_oid(state.completed_commits[-1])
        prev_commit = self._access.resolve_commit(str(prev_oid))

        # Build combined message
        if concat_message:
            message = prev_commit.message.rstrip() + "\n\n" + commit.message
        else:
            message = prev_commit.message

        # Create new commit with current tree (includes squashed changes) and
        # the same parents as the previous commit (replacing it in history)
        tree_id = self._access.index.write_tree()
        # Use previous commit's parent list; for root commits this is empty
        parents = list(prev_commit.parent_ids)

        # Use ref=None because HEAD doesn't point to parent
        # (pygit2 requires first parent == current HEAD when ref is set)
        new_oid = self._access.create_commit(
            None,  # Don't update any ref directly
            prev_commit.author,
            self._access.default_signature,
            message,
            tree_id,
            parents,
        )

        # Replace the previous completed commit
        state.completed_commits[-1] = str(new_oid)

        # Update HEAD to point to the new squashed commit
        self._access.reset(new_oid, RESET_HARD)

        return RebaseResult(
            success=True,
            completed_steps=state.current_step + 1,
            total_steps=total,
            state="done",
        )

    def _cherry_pick_changes(self, commit: pygit2.Commit) -> None:
        """Apply changes from commit without creating a new commit."""
        self._access.cherrypick(commit.id)

    def _has_conflicts(self) -> bool:
        """Check if index has conflicts."""
        conflicts = self._access.index.conflicts
        if conflicts is None:
            return False
        # ConflictCollection is iterable, convert to bool via any()
        return any(True for _ in conflicts)

    def _get_conflict_paths(self) -> list[str]:
        """Get paths with conflicts."""
        conflicts = self._access.index.conflicts
        if conflicts is None:
            return []
        # Check all three sides (ancestor, ours, theirs) for paths
        paths: list[str] = []
        seen: set[str] = set()
        for entry in conflicts:
            for side in entry:
                if side is None:
                    continue
                path = side.path
                if path not in seen:
                    seen.add(path)
                    paths.append(path)
        return paths

    def _finalize(self, state: RebaseState) -> RebaseResult:
        """Finalize the rebase - update branch ref if on a branch."""
        new_head_oid = self._access.must_head_target()
        new_head = str(new_head_oid)

        # Update original branch to point to new head and reattach
        if state.original_branch:
            branch = self._access.must_local_branch(state.original_branch)
            self._access.set_branch_target(branch, new_head_oid)
            self._access.set_head(f"refs/heads/{state.original_branch}")

        # Clean up state file
        self._state_path.unlink(missing_ok=True)
        self._access.state_cleanup()

        return RebaseResult(
            success=True,
            completed_steps=len(state.steps),
            total_steps=len(state.steps),
            state="done",
            new_head=new_head,
        )

    def _save_state(self, state: RebaseState) -> None:
        """Persist rebase state to disk."""
        data = {
            "original_head": state.original_head,
            "original_branch": state.original_branch,
            "onto": state.onto,
            "steps": state.steps,
            "current_step": state.current_step,
            "completed_commits": state.completed_commits,
        }
        self._state_path.write_text(json.dumps(data))

    def _load_state(self) -> RebaseState | None:
        """Load persisted rebase state."""
        if not self._state_path.exists():
            return None
        try:
            data = json.loads(self._state_path.read_text())
            return RebaseState(
                original_head=data["original_head"],
                original_branch=data.get("original_branch"),  # May be None or missing
                onto=data["onto"],
                steps=data["steps"],
                current_step=data["current_step"],
                completed_commits=data["completed_commits"],
            )
        except (json.JSONDecodeError, KeyError) as e:
            raise GitError(f"Corrupt rebase state file: {e}") from e

    def _step_from_dict(self, d: dict[str, str | None]) -> RebaseStep:
        """Convert dict to RebaseStep."""
        action = d["action"]
        commit_sha = d["commit_sha"]
        if action is None or commit_sha is None:
            raise GitError("Invalid rebase step: missing action or commit_sha")
        return RebaseStep(
            action=action,  # type: ignore[arg-type]
            commit_sha=commit_sha,
            message=d.get("message"),
        )
