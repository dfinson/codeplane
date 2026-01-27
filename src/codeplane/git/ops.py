"""Git operations via pygit2 - returns serializable data models."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from pathlib import Path

import pygit2

from codeplane.git._internal import (
    CheckoutPlanner,
    DiffPlanner,
    RepoAccess,
    WriteFlows,
    check_nothing_to_commit,
    first_line,
    make_tag_ref,
    require_branch_exists,
    require_current_branch,
    require_not_current_branch,
    require_not_unborn,
)
from codeplane.git._internal.constants import (
    MERGE_FASTFORWARD,
    MERGE_NORMAL,
    MERGE_UP_TO_DATE,
    RESET_HARD,
    RESET_MIXED,
    RESET_SOFT,
    SORT_TIME,
    STATUS_WT_DELETED,
    STATUS_WT_MODIFIED,
    STATUS_WT_NEW,
)
from codeplane.git.credentials import SystemCredentialCallback, get_default_callbacks
from codeplane.git.errors import (
    BranchExistsError,
    RefNotFoundError,
    StashNotFoundError,
    UnmergedBranchError,
)
from codeplane.git.models import (
    BlameInfo,
    BranchInfo,
    CommitInfo,
    DiffInfo,
    MergeAnalysis,
    MergeResult,
    OperationResult,
    RefInfo,
    RemoteInfo,
    Signature,
    StashEntry,
    TagInfo,
)


class GitOps:
    """Thin wrapper around pygit2.Repository with cleaner error handling."""

    def __init__(self, repo_path: Path | str) -> None:
        self._access = RepoAccess(repo_path)
        self._flows = WriteFlows(self._access)
        self._diff_planner = DiffPlanner(self._access)
        self._checkout_planner = CheckoutPlanner(self._access)

    def _head_oid(self) -> pygit2.Oid:
        """Get HEAD target Oid, raising if unborn."""
        return self._access.must_head_target()

    @property
    def repo(self) -> pygit2.Repository:
        """
        Direct access to underlying pygit2 Repository.

        Escape hatch for advanced consumers. Bypasses GitOps error mapping
        and domain model conversion. Use with caution.
        """
        return self._access.repo

    @property
    def path(self) -> Path:
        """Repository root path."""
        return self._access.path

    # =========================================================================
    # Read Operations
    # =========================================================================

    def status(self) -> dict[str, int]:
        """Get status flags by path. Use pygit2.GIT_STATUS_* to interpret."""
        return self._access.status()

    def head(self) -> RefInfo:
        """Get HEAD reference info."""
        ref = self._access.head_ref
        return RefInfo(
            name=ref.name,
            target_sha=str(ref.target),
            shorthand=ref.shorthand,
            is_detached=self._access.is_detached,
        )

    def head_commit(self) -> CommitInfo | None:
        """Get HEAD commit, or None if unborn."""
        commit = self._access.head_commit()
        return CommitInfo.from_pygit2(commit) if commit else None

    def diff(
        self,
        base: str | None = None,
        target: str | None = None,
        staged: bool = False,
        include_patch: bool = False,
    ) -> DiffInfo:
        """Generate diff."""
        plan = self._diff_planner.plan(base, target, staged)
        raw = self._diff_planner.execute(plan)
        return DiffInfo.from_pygit2(raw, include_patch)

    def blame(
        self, path: str, min_line: int | None = None, max_line: int | None = None
    ) -> BlameInfo:
        """Get blame for a file."""
        kwargs: dict[str, int] = {}
        if min_line is not None:
            kwargs["min_line"] = min_line
        if max_line is not None:
            kwargs["max_line"] = max_line
        return BlameInfo.from_pygit2(path, self._access.blame(path, **kwargs))

    def log(self, ref: str = "HEAD", limit: int = 50) -> list[CommitInfo]:
        """Get commit history."""
        try:
            start = self._access.resolve_ref_oid(ref)
        except RefNotFoundError:
            return []
        result: list[CommitInfo] = []
        for commit in self._access.walk_commits(start, SORT_TIME):
            result.append(CommitInfo.from_pygit2(commit))
            if len(result) >= limit:
                break
        return result

    def show(self, ref: str = "HEAD") -> CommitInfo:
        """Get commit info."""
        return CommitInfo.from_pygit2(self._access.resolve_commit(ref))

    def branches(self, include_remote: bool = True) -> list[BranchInfo]:
        """List branches."""
        result = [
            BranchInfo.from_pygit2(self._access.branches.local[n])
            for n in self._access.branches.local
        ]
        if include_remote:
            result.extend(
                BranchInfo.from_pygit2(self._access.branches.remote[n])
                for n in self._access.branches.remote
            )
        return result

    def tags(self) -> list[TagInfo]:
        """List tags."""
        result: list[TagInfo] = []
        for name, target_oid, tag_obj in self._access.iter_tags():
            if tag_obj:
                tagger = Signature.from_pygit2(tag_obj.tagger) if tag_obj.tagger else None
                result.append(TagInfo(name, str(tag_obj.target), True, tag_obj.message, tagger))
            else:
                result.append(TagInfo(name, str(target_oid), False))
        return result

    def remotes(self) -> list[RemoteInfo]:
        """List remotes."""
        return [
            RemoteInfo(r.name or "", r.url or "", getattr(r, "push_url", None))
            for r in self._access.remotes
        ]

    def state(self) -> int:
        """Repository state. Compare with pygit2.GIT_REPOSITORY_STATE_*."""
        return self._access.state()

    def current_branch(self) -> str | None:
        """Current branch name, or None if detached or unborn."""
        return self._access.current_branch_name()

    # =========================================================================
    # Write Operations
    # =========================================================================

    def stage(self, paths: Sequence[str | Path]) -> None:
        """Stage files."""
        index = self._access.index
        status = self._access.status()
        for path in paths:
            p = self._access.normalize_path(path)
            flags = status.get(p, 0)
            if flags & (STATUS_WT_NEW | STATUS_WT_MODIFIED):
                index.add(p)
            elif flags & STATUS_WT_DELETED:
                index.remove(p)
        index.write()

    def unstage(self, paths: Sequence[str | Path]) -> None:
        """Unstage files (keeps working tree changes)."""
        if self._access.is_unborn:
            self._access.best_effort_index_remove(str(p) for p in paths)
            return

        head_tree = self._access.must_head_tree()
        for p in paths:
            self._access.index_reset_entry(self._access.normalize_path(p), head_tree)
        self._access.index.write()

    def commit(self, message: str, allow_empty: bool = False) -> str:
        """Create commit from staged changes. Returns commit sha."""
        check_nothing_to_commit(self._access, allow_empty)
        return self._flows.commit_from_index(message)

    def amend(self, message: str | None = None) -> str:
        """Amend the most recent commit. Returns commit sha."""
        require_not_unborn(self._access, "amend")
        head_commit = self._access.must_head_commit()
        tree_id = self._access.index.write_tree()
        new_message = message if message is not None else head_commit.message
        oid = self._access.create_commit(
            "HEAD",
            head_commit.author,
            self._access.default_signature,
            new_message,
            tree_id,
            list(head_commit.parent_ids),
        )
        return str(oid)

    def create_branch(self, name: str, ref: str = "HEAD") -> BranchInfo:
        """Create branch."""
        if self._access.has_local_branch(name):
            raise BranchExistsError(name)
        branch = self._access.create_local_branch(name, self._access.resolve_commit(ref))
        return BranchInfo.from_pygit2(branch)

    def checkout(self, ref: str, create: bool = False) -> None:
        """Checkout branch or ref."""
        if create:
            self.create_branch(ref)
        plan = self._checkout_planner.plan(ref)
        self._checkout_planner.execute(plan)

    def delete_branch(self, name: str, force: bool = False) -> None:
        """Delete branch."""
        require_branch_exists(self._access, name)
        require_not_current_branch(self._access, name)

        branch = self._access.must_local_branch(name)
        branch_oid = self._access.branch_target_oid(branch)
        if not force and not self._access.descendant_of(self._head_oid(), branch_oid):
            raise UnmergedBranchError(name)
        branch.delete()

    def rename_branch(self, old_name: str, new_name: str) -> BranchInfo:
        """Rename a branch."""
        require_branch_exists(self._access, old_name)
        if self._access.has_local_branch(new_name):
            raise BranchExistsError(new_name)

        branch = self._access.must_local_branch(old_name)
        branch.rename(new_name)
        return BranchInfo.from_pygit2(self._access.must_local_branch(new_name))

    def reset(self, ref: str, mode: str = "mixed") -> None:
        """Reset HEAD. mode: 'soft', 'mixed', or 'hard'."""
        modes = {"soft": RESET_SOFT, "mixed": RESET_MIXED, "hard": RESET_HARD}
        if mode not in modes:
            raise ValueError(
                f"Invalid reset mode {mode!r}. Expected one of: {', '.join(sorted(modes))}"
            )
        self._access.reset(self._access.resolve_ref_oid(ref), modes[mode])

    def merge(self, ref: str) -> MergeResult:
        """Merge ref. Returns MergeResult with success, commit_sha, conflict_paths."""
        their_oid = self._access.resolve_ref_oid(ref)
        analysis, _ = self._access.merge_analysis(their_oid)

        if analysis & MERGE_UP_TO_DATE:
            return MergeResult(True, None)

        if analysis & MERGE_FASTFORWARD:
            self._access.checkout_detached(their_oid)
            current = self._access.current_branch_name()
            if current:
                branch = self._access.must_local_branch(current)
                self._access.set_branch_target(branch, their_oid)
            self._access.set_head_target(their_oid)
            return MergeResult(True, str(their_oid))

        # Non-fastforward merge with guaranteed cleanup
        head_oid = self._head_oid()
        with self._flows.stateful_op():
            self._access.merge(their_oid)
            conflicts = self._flows.check_conflicts()
            if conflicts.has_conflicts:
                return MergeResult(False, None, conflicts.conflict_paths)

            sha = self._flows.write_tree_and_commit(f"Merge {ref}", [head_oid, their_oid])
            return MergeResult(True, sha)

    def abort_merge(self) -> None:
        """Abort in-progress merge."""
        self._access.state_cleanup()
        self._access.reset(self._head_oid(), RESET_HARD)

    def merge_analysis(self, ref: str) -> MergeAnalysis:
        """Analyze potential merge."""
        their_oid = self._access.resolve_ref_oid(ref)
        analysis, _ = self._access.merge_analysis(their_oid)
        return MergeAnalysis(
            up_to_date=bool(analysis & MERGE_UP_TO_DATE),
            fastforward_possible=bool(analysis & MERGE_FASTFORWARD),
            conflicts_likely=bool(analysis & MERGE_NORMAL),
        )

    def cherrypick(self, ref: str) -> OperationResult:
        """Cherry-pick a commit."""
        commit = self._access.resolve_commit(ref)
        head_oid = self._head_oid()

        with self._flows.stateful_op():
            self._access.cherrypick(commit.id)
            conflicts = self._flows.check_conflicts()
            if conflicts.has_conflicts:
                return OperationResult(False, conflicts.conflict_paths)

            self._flows.write_tree_and_commit(commit.message, [head_oid], author=commit.author)
            return OperationResult(True)

    def revert(self, ref: str) -> OperationResult:
        """Revert a commit."""
        commit = self._access.resolve_commit(ref)
        head_commit = self._access.must_head_commit()
        head_oid = self._head_oid()

        with self._flows.stateful_op():
            self._access.revert_commit(commit, head_commit)
            conflicts = self._flows.check_conflicts()
            if conflicts.has_conflicts:
                return OperationResult(False, conflicts.conflict_paths)

            message = f'Revert "{first_line(commit.message)}"'
            self._flows.write_tree_and_commit(message, [head_oid])
            return OperationResult(True)

    def stash_push(self, message: str | None = None, include_untracked: bool = False) -> str:
        """Stash changes. Returns stash commit sha."""
        oid = self._access.stash(
            self._access.default_signature, message, include_untracked=include_untracked
        )
        return str(oid)

    def stash_pop(self, index: int = 0) -> None:
        """Pop stash entry."""
        stashes = self._access.listall_stashes()
        if index >= len(stashes):
            raise StashNotFoundError(index)
        self._access.stash_apply(index)
        self._access.stash_drop(index)

    def stash_list(self) -> list[StashEntry]:
        """List stash entries."""
        return [
            StashEntry(i, s.message, str(s.commit_id))
            for i, s in enumerate(self._access.listall_stashes())
        ]

    def create_tag(self, name: str, ref: str = "HEAD", message: str | None = None) -> str:
        """Create tag. Returns target sha."""
        target = self._access.resolve_ref_oid(ref)
        if message:
            oid = self._access.create_tag(
                name,
                target,
                pygit2.enums.ObjectType.COMMIT,
                self._access.default_signature,
                message,
            )
            return str(oid)
        self._access.create_reference(make_tag_ref(name), target)
        return str(target)

    def delete_tag(self, name: str) -> None:
        """Delete tag."""
        ref = make_tag_ref(name)
        if not self._access.has_reference(ref):
            raise RefNotFoundError(name)
        self._access.delete_reference(ref)

    def fetch(
        self, remote: str = "origin", callbacks: SystemCredentialCallback | None = None
    ) -> None:
        """Fetch from remote."""
        cbs = callbacks or get_default_callbacks()
        self._access.run_remote_operation(
            remote, "fetch", partial(pygit2.Remote.fetch, callbacks=cbs)
        )

    def push(
        self,
        remote: str = "origin",
        force: bool = False,
        callbacks: SystemCredentialCallback | None = None,
    ) -> None:
        """Push to remote."""
        branch = require_current_branch(self._access, "push")
        prefix = "+" if force else ""
        refspec = f"{prefix}refs/heads/{branch}:refs/heads/{branch}"
        cbs = callbacks or get_default_callbacks()
        self._access.run_remote_operation(
            remote, "push", partial(pygit2.Remote.push, specs=[refspec], callbacks=cbs)
        )
