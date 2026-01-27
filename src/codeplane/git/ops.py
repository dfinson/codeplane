"""Git operations via pygit2 - returns serializable data models."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pygit2

from codeplane.git._internal import (
    CheckoutPlanner,
    DiffPlanner,
    RebaseFlow,
    RebasePlanner,
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
    BranchNotFoundError,
    RefNotFoundError,
    StashNotFoundError,
    SubmoduleError,
    SubmoduleNotFoundError,
    UnmergedBranchError,
    WorktreeError,
    WorktreeExistsError,
    WorktreeLockedError,
    WorktreeNotFoundError,
)
from codeplane.git.models import (
    BlameInfo,
    BranchInfo,
    CommitInfo,
    DiffInfo,
    MergeAnalysis,
    MergeResult,
    OperationResult,
    RebasePlan,
    RebaseResult,
    RefInfo,
    RemoteInfo,
    Signature,
    StashEntry,
    SubmoduleInfo,
    SubmoduleState,
    SubmoduleStatus,
    SubmoduleUpdateResult,
    TagInfo,
    WorktreeInfo,
)


class GitOps:
    """Thin wrapper around pygit2.Repository with cleaner error handling."""

    def __init__(self, repo_path: Path | str) -> None:
        self._access = RepoAccess(repo_path)
        self._flows = WriteFlows(self._access)
        self._diff_planner = DiffPlanner(self._access)
        self._checkout_planner = CheckoutPlanner(self._access)
        self._rebase_planner = RebasePlanner(self._access)
        self._rebase_flow = RebaseFlow(self._access)

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
            for n in self._access.branches.remote:
                branch = self._access.branches.remote[n]
                # Skip symbolic refs like origin/HEAD (target is str, not Oid)
                if isinstance(branch.target, str):
                    continue
                result.append(BranchInfo.from_pygit2(branch))
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
            normalized = (self._access.normalize_path(p) for p in paths)
            self._access.best_effort_index_remove(normalized)
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
        # Don't update ref - create orphan commit with same parents
        oid = self._access.create_commit(
            None,
            head_commit.author,
            self._access.default_signature,
            new_message,
            tree_id,
            list(head_commit.parent_ids),
        )
        # Reset HEAD to new commit
        self._access.reset(oid, pygit2.GIT_RESET_SOFT)
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
        # Use lambda to avoid pygit2 version differences in keyword arg names
        self._access.run_remote_operation(remote, "fetch", lambda r: r.fetch(callbacks=cbs))

    def push(
        self,
        remote: str = "origin",
        force: bool = False,
        callbacks: SystemCredentialCallback | None = None,
    ) -> None:
        """
        Push current branch to remote.

        Note:
            Always pushes the current local branch to the same-named branch
            on the remote. Does not resolve upstream tracking configuration.
            For tracking-aware push, use git CLI directly.
        """
        branch = require_current_branch(self._access, "push")
        prefix = "+" if force else ""
        refspec = f"{prefix}refs/heads/{branch}:refs/heads/{branch}"
        cbs = callbacks or get_default_callbacks()
        # Use lambda to avoid pygit2 version differences in keyword arg names
        self._access.run_remote_operation(
            remote, "push", lambda r: r.push([refspec], callbacks=cbs)
        )

    # =========================================================================
    # Worktree Operations
    # =========================================================================

    def worktrees(self) -> list[WorktreeInfo]:
        """
        List all worktrees including main working directory.

        Note:
            Lock status detection is best-effort (checks .git/worktrees/<name>/locked).
            For unusual gitdir layouts, lock status may be incorrectly reported as False.
        """
        result: list[WorktreeInfo] = []

        # Main working directory
        main_path = self._access.workdir
        if main_path:
            head = self._access.head_ref
            result.append(
                WorktreeInfo(
                    name="main",
                    path=main_path.rstrip("/"),
                    head_ref=head.shorthand if not self._access.is_detached else "HEAD",
                    head_sha=str(self._access.head_target) if not self._access.is_unborn else "",
                    is_main=True,
                    is_bare=False,
                    is_locked=False,
                    lock_reason=None,
                    is_prunable=False,
                )
            )

        # Additional worktrees
        for name in self._access.list_worktrees():
            try:
                wt = self._access.lookup_worktree(name)
                # pygit2.Repository() handles worktree .git file resolution
                # (worktree dirs contain a .git file pointing to the real gitdir)
                wt_path = wt.path
                wt_repo = pygit2.Repository(wt_path)
                head_sha = str(wt_repo.head.target) if not wt_repo.head_is_unborn else ""
                head_ref = wt_repo.head.shorthand if not wt_repo.head_is_detached else "HEAD"

                # Check lock status via .git/worktrees/<name>/locked file
                git_dir = Path(self._access.repo.path)
                lock_file = git_dir / "worktrees" / name / "locked"
                is_locked = lock_file.exists()
                lock_reason = lock_file.read_text().strip() if is_locked else None

                result.append(
                    WorktreeInfo(
                        name=name,
                        path=wt_path,
                        head_ref=head_ref,
                        head_sha=head_sha,
                        is_main=False,
                        is_bare=False,
                        is_locked=is_locked,
                        lock_reason=lock_reason or None,
                        is_prunable=wt.is_prunable,
                    )
                )
            except (pygit2.GitError, OSError):
                # Worktree may be prunable/missing
                result.append(
                    WorktreeInfo(
                        name=name,
                        path="",
                        head_ref="",
                        head_sha="",
                        is_main=False,
                        is_bare=False,
                        is_locked=False,
                        lock_reason=None,
                        is_prunable=True,
                    )
                )

        return result

    def worktree_add(self, path: Path, ref: str, checkout: bool = True) -> GitOps:  # noqa: ARG002
        """
        Add worktree at path for ref. Returns GitOps for new worktree.

        Args:
            path: Directory path for the new worktree. Must not exist.
            ref: Local branch name to checkout. Must be an existing local branch.
            checkout: Ignored (pygit2 always checks out). Kept for API compatibility.

        Raises:
            BranchNotFoundError: If ref is not an existing local branch.
            WorktreeExistsError: If a worktree with the same name already exists.
            WorktreeError: If path already exists or is used by another worktree.

        Note:
            The worktree name (used internally by git) is derived from path.name.
            This is consistent with git's behavior when no explicit --name is given.
            To create a worktree from a commit SHA or tag, first create a branch.
        """
        # Enforce: ref must be a local branch (not a SHA, tag, or remote branch)
        if not self._access.has_local_branch(ref):
            raise BranchNotFoundError(ref)

        # Enforce: path must not exist
        if path.exists():
            raise WorktreeError(f"Path already exists: {path}")

        # Git worktree name is derived from path basename
        name = path.name
        existing_names = self._access.list_worktrees()
        if name in existing_names:
            raise WorktreeExistsError(name)

        # Also check that path isn't already used by another worktree
        for wt_name in existing_names:
            try:
                wt = self._access.lookup_worktree(wt_name)
                if Path(wt.path).resolve() == path.resolve():
                    raise WorktreeError(f"Path already in use by worktree '{wt_name}'")
            except pygit2.GitError:
                pass

        branch_ref = self._access.repo.references.get(f"refs/heads/{ref}")
        self._access.add_worktree(name, str(path), branch_ref)

        return GitOps(path)

    def worktree_open(self, name: str) -> GitOps:
        """Get GitOps instance for existing worktree by name."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)

        wt = self._access.lookup_worktree(name)
        return GitOps(wt.path)

    def worktree_remove(self, name: str, force: bool = False) -> None:
        """
        Remove worktree.

        Args:
            name: Worktree name (as returned by worktrees()).
            force: If True, removes even if dirty or locked.

        Note:
            Uses `git worktree remove` subprocess for correctness.
            This ensures git metadata is always consistent, even if
            directory removal fails (e.g., file locks on Windows).
        """
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)

        # Check lock status via file system (for better error message)
        lock_file = self._access.worktree_gitdir(name) / "locked"
        if lock_file.exists() and not force:
            raise WorktreeLockedError(name)

        try:
            self._access.remove_worktree(name, force)
        except Exception as e:
            # Map generic errors to domain error
            raise WorktreeError(f"Failed to remove worktree '{name}': {e}") from e

    def worktree_lock(self, name: str, reason: str | None = None) -> None:
        """Lock worktree to prevent pruning."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)

        # Use worktree's actual admin dir
        gitdir = self._access.worktree_gitdir(name)
        if not gitdir.exists():
            raise WorktreeError(f"Invalid worktree gitdir (missing): {gitdir}")

        lock_file = gitdir / "locked"
        if lock_file.exists():
            raise WorktreeLockedError(name)

        try:
            lock_file.write_text(reason or "", encoding="utf-8")
        except OSError as e:
            raise WorktreeError(f"Failed to lock worktree {name}: {e}") from e

    def worktree_unlock(self, name: str) -> None:
        """Unlock worktree."""
        if name not in self._access.list_worktrees():
            raise WorktreeNotFoundError(name)

        lock_file = self._access.worktree_gitdir(name) / "locked"
        if lock_file.exists():
            lock_file.unlink()

    def worktree_prune(self) -> list[str]:
        """Remove stale worktree entries. Returns pruned names."""
        pruned = []
        for name in self._access.list_worktrees():
            try:
                wt = self._access.lookup_worktree(name)
                if wt.is_prunable:
                    wt.prune(False)
                    pruned.append(name)
            except pygit2.GitError:
                pass
        return pruned

    def is_worktree(self) -> bool:
        """True if this GitOps is for a worktree (not main working directory)."""
        return self._access.is_worktree()

    def worktree_info(self) -> WorktreeInfo | None:
        """
        Get info about this worktree, or None if main working directory.

        Note:
            The returned name is the directory basename (self._access.path.name),
            which may differ from the git worktree ID in edge cases.
            For operations requiring the git ID, use worktrees() on the main repo.
        """
        if not self.is_worktree():
            return None

        head = self._access.head_ref
        return WorktreeInfo(
            name=self._access.path.name,
            path=str(self._access.path),
            head_ref=head.shorthand if not self._access.is_detached else "HEAD",
            head_sha=str(self._access.head_target) if not self._access.is_unborn else "",
            is_main=False,
            is_bare=False,
            is_locked=False,  # Can't easily determine from inside worktree
            lock_reason=None,
            is_prunable=False,
        )

    # =========================================================================
    # Submodule Operations
    # =========================================================================

    def submodules(self) -> list[SubmoduleInfo]:
        """List all submodules with status."""
        result = []
        for name in self._access.listall_submodules():
            try:
                sm = self._access.lookup_submodule(name)
                status = self._determine_submodule_status(sm)
                # Branch property throws RuntimeError when NULL
                try:
                    branch = sm.branch
                except RuntimeError:
                    branch = None
                result.append(
                    SubmoduleInfo(
                        name=sm.name,
                        path=sm.path,
                        url=sm.url or "",
                        branch=branch,
                        head_sha=str(sm.head_id) if sm.head_id else None,
                        status=status,
                    )
                )
            except pygit2.GitError:
                result.append(
                    SubmoduleInfo(
                        name=name,
                        path=name,
                        url="",
                        branch=None,
                        head_sha=None,
                        status="missing",
                    )
                )
        return result

    def _determine_submodule_status(self, sm: object) -> SubmoduleState:
        """
        Determine submodule status from pygit2.Submodule.

        Note: "dirty" includes any working tree changes OR untracked files.
        """
        # Check if workdir exists
        sm_path = Path(self._access.path) / getattr(sm, "path", "")
        if not sm_path.exists():
            return "uninitialized"

        # Check if .git exists in submodule (can be file or directory)
        if not (sm_path / ".git").exists():
            return "uninitialized"

        try:
            sm_repo = pygit2.Repository(str(sm_path))
            # Check if dirty
            if sm_repo.status():
                return "dirty"

            # Check if at recorded commit
            head_id = getattr(sm, "head_id", None)
            if head_id and not sm_repo.head_is_unborn and sm_repo.head.target != head_id:
                return "outdated"

            return "clean"
        except pygit2.GitError:
            return "missing"

    def submodule_status(self, path: str) -> SubmoduleStatus:
        """
        Detailed status for one submodule.

        Args:
            path: The submodule path (relative to repo root).
        """
        try:
            sm = self._access.lookup_submodule_by_path(path)
        except Exception:
            raise SubmoduleNotFoundError(path) from None

        # Build SubmoduleInfo for this submodule
        status = self._determine_submodule_status(sm)

        # Handle NULL fields in pygit2 (raises RuntimeError if accessed when NULL)
        try:
            url = sm.url or ""
        except RuntimeError:
            url = ""

        try:
            branch = sm.branch
        except RuntimeError:
            branch = None

        info = SubmoduleInfo(
            name=sm.name,
            path=sm.path,
            url=url,
            branch=branch,
            head_sha=str(sm.head_id) if sm.head_id else None,
            status=status,
        )

        # Get detailed status
        sm_path = Path(self._access.path) / path
        workdir_dirty = False
        index_dirty = False
        untracked_count = 0
        actual_sha = None

        if sm_path.exists():
            try:
                sm_repo = pygit2.Repository(str(sm_path))
                actual_sha = str(sm_repo.head.target) if not sm_repo.head_is_unborn else None
                repo_status = sm_repo.status()
                for flags in repo_status.values():
                    if flags & (pygit2.GIT_STATUS_WT_MODIFIED | pygit2.GIT_STATUS_WT_DELETED):
                        workdir_dirty = True
                    if flags & (
                        pygit2.GIT_STATUS_INDEX_MODIFIED
                        | pygit2.GIT_STATUS_INDEX_DELETED
                        | pygit2.GIT_STATUS_INDEX_NEW
                    ):
                        index_dirty = True
                    if flags & pygit2.GIT_STATUS_WT_NEW:
                        untracked_count += 1
            except pygit2.GitError:
                pass

        return SubmoduleStatus(
            info=info,
            workdir_dirty=workdir_dirty,
            index_dirty=index_dirty,
            untracked_count=untracked_count,
            recorded_sha=str(sm.head_id) if sm.head_id else "",
            actual_sha=actual_sha,
        )

    def submodule_init(self, paths: Sequence[str] | None = None) -> list[str]:
        """
        Initialize submodules.

        Args:
            paths: Submodule paths to initialize. If None, initializes all.

        Returns:
            List of successfully initialized submodule paths.
        """
        initialized = []

        if paths is None:
            # Initialize all submodules
            for name in self._access.listall_submodules():
                try:
                    self._access.init_submodule(name)
                    sm = self._access.lookup_submodule(name)
                    initialized.append(sm.path)
                except pygit2.GitError:
                    pass
        else:
            # Initialize specific submodules by path
            for path in paths:
                sm_name = self._access.submodule_name_for_path(path)
                if sm_name is None:
                    raise SubmoduleNotFoundError(path)
                try:
                    self._access.init_submodule(sm_name)
                    initialized.append(path)
                except pygit2.GitError:
                    pass

        return initialized

    def submodule_update(
        self,
        paths: Sequence[str] | None = None,
        recursive: bool = False,
        init: bool = True,
    ) -> SubmoduleUpdateResult:
        """
        Update submodules to recorded commits.

        Uses subprocess for full credential and recursive support.

        Note:
            Result parsing is best-effort based on git output format.
            - `updated`: paths successfully updated (parsed from stdout)
            - `failed`: ("*", error_message) on failure (not path-specific)
            - `already_current`: always empty (git doesn't report this separately)
        """
        import subprocess

        cmd = ["git", "submodule", "update"]
        if init:
            cmd.append("--init")
        if recursive:
            cmd.append("--recursive")
        if paths:
            cmd.append("--")
            cmd.extend(paths)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self._access.path),
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode == 0:
                # Parse output to determine what was updated
                updated = []
                for line in result.stdout.splitlines():
                    if line.startswith("Submodule path"):
                        # Extract path from "Submodule path 'foo': checked out 'abc123'"
                        parts = line.split("'")
                        if len(parts) >= 2:
                            updated.append(parts[1])
                return SubmoduleUpdateResult(
                    updated=tuple(updated),
                    failed=(),
                    already_current=(),
                )
            else:
                return SubmoduleUpdateResult(
                    updated=(),
                    failed=(("*", result.stderr.strip()),),
                    already_current=(),
                )
        except subprocess.TimeoutExpired:
            return SubmoduleUpdateResult(
                updated=(),
                failed=(("*", "Operation timed out"),),
                already_current=(),
            )

    def submodule_sync(self, paths: Sequence[str] | None = None) -> None:
        """Sync submodule URLs from .gitmodules to .git/config."""
        import subprocess

        cmd = ["git", "submodule", "sync"]
        if paths:
            cmd.append("--")
            cmd.extend(paths)

        subprocess.run(
            cmd,
            cwd=str(self._access.path),
            capture_output=True,
            timeout=60,
            check=True,
        )

    def submodule_add(self, url: str, path: str, branch: str | None = None) -> SubmoduleInfo:
        """Add new submodule."""
        import subprocess

        cmd = ["git", "submodule", "add"]
        if branch:
            cmd.extend(["-b", branch])
        cmd.extend([url, path])

        result = subprocess.run(
            cmd,
            cwd=str(self._access.path),
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            raise SubmoduleError(f"Failed to add submodule: {result.stderr.strip()}")

        # Lookup by path (name may differ from path in some configurations)
        sm = self._access.lookup_submodule_by_path(path)
        return SubmoduleInfo(
            name=sm.name,
            path=sm.path,
            url=sm.url or url,
            branch=branch,
            head_sha=str(sm.head_id) if sm.head_id else None,
            status="clean",
        )

    def submodule_deinit(self, path: str, force: bool = False) -> None:
        """Deinitialize submodule (remove from working tree)."""
        import subprocess

        cmd = ["git", "submodule", "deinit"]
        if force:
            cmd.append("--force")
        cmd.append(path)

        result = subprocess.run(
            cmd,
            cwd=str(self._access.path),
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            raise SubmoduleError(f"Failed to deinit submodule: {result.stderr.strip()}")

    def submodule_remove(self, path: str) -> None:
        """Fully remove submodule (deinit + remove from .gitmodules + delete dir)."""
        import shutil
        import subprocess

        # Resolve submodule name (may differ from path)
        name = self._access.submodule_name_for_path(path)
        if name is None:
            raise SubmoduleNotFoundError(path)

        # Deinit first (uses path)
        self.submodule_deinit(path, force=True)

        # Remove from .gitmodules (use name for section key)
        subprocess.run(
            ["git", "config", "--file", ".gitmodules", "--remove-section", f"submodule.{name}"],
            cwd=str(self._access.path),
            capture_output=True,
            timeout=30,
        )

        # Stage .gitmodules change
        gitmodules_path = Path(self._access.path) / ".gitmodules"
        if gitmodules_path.exists():
            self._access.index.add(".gitmodules")
            self._access.index.write()

        # Remove from .git/config (use name for section key)
        subprocess.run(
            ["git", "config", "--remove-section", f"submodule.{name}"],
            cwd=str(self._access.path),
            capture_output=True,
            timeout=30,
        )

        # Remove from index (use path)
        try:
            self._access.index.remove(path)
            self._access.index.write()
        except pygit2.GitError:
            pass

        # Remove directory (use path)
        sm_path = Path(self._access.path) / path
        if sm_path.exists():
            shutil.rmtree(sm_path)

        # Remove .git/modules/<name> (name is used for module storage)
        modules_path = Path(self._access.repo.path) / "modules" / name
        if modules_path.exists():
            shutil.rmtree(modules_path)

    # =========================================================================
    # Rebase Operations
    # =========================================================================

    def rebase_plan(self, upstream: str, onto: str | None = None) -> RebasePlan:
        """Generate default rebase plan (all picks). Agent can modify before execute."""
        return self._rebase_planner.plan(upstream, onto)

    def rebase_execute(self, plan: RebasePlan) -> RebaseResult:
        """Execute rebase plan. On conflict or edit-pause, returns partial result."""
        return self._rebase_flow.execute(plan)

    def rebase_continue(self) -> RebaseResult:
        """Resume after conflict resolution or edit completion."""
        return self._rebase_flow.continue_rebase()

    def rebase_abort(self) -> None:
        """Abort and restore original state."""
        self._rebase_flow.abort()

    def rebase_skip(self) -> RebaseResult:
        """Skip current commit and continue."""
        return self._rebase_flow.skip()

    def rebase_in_progress(self) -> bool:
        """Check if a rebase is in progress."""
        return self._rebase_flow.has_rebase_in_progress()
