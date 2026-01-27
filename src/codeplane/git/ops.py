"""Git operations via pygit2 - thin wrapper with better error handling."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

import pygit2

from codeplane.git.credentials import SystemCredentialCallback, get_default_callbacks
from codeplane.git.errors import (
    AuthenticationError,
    BranchExistsError,
    BranchNotFoundError,
    DetachedHeadError,
    NotARepositoryError,
    NothingToCommitError,
    RefNotFoundError,
    RemoteError,
    StashNotFoundError,
    UnmergedBranchError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class GitOps:
    """Thin wrapper around pygit2.Repository with cleaner error handling."""

    def __init__(self, repo_path: Path | str) -> None:
        self._path = Path(repo_path)
        try:
            self._repo = pygit2.Repository(str(self._path))
        except pygit2.GitError as e:
            raise NotARepositoryError(str(self._path)) from e

    @property
    def repo(self) -> pygit2.Repository:
        """Direct access to underlying pygit2 Repository."""
        return self._repo

    @property
    def path(self) -> Path:
        """Repository root path."""
        return Path(self._repo.workdir) if self._repo.workdir else self._path

    # =========================================================================
    # Read Operations - return pygit2 objects directly
    # =========================================================================

    def status(self) -> dict[str, int]:
        """Get status flags by path. Use pygit2.GIT_STATUS_* to interpret."""
        return self._repo.status()

    def head(self) -> pygit2.Reference:
        """Get HEAD reference."""
        return self._repo.head

    def head_commit(self) -> pygit2.Commit | None:
        """Get HEAD commit, or None if unborn."""
        if self._repo.head_is_unborn:
            return None
        return self._repo.head.peel(pygit2.Commit)

    def diff(
        self,
        base: str | None = None,
        target: str | None = None,
        staged: bool = False,
    ) -> pygit2.Diff:
        """Generate diff."""
        if staged:
            if self._repo.head_is_unborn:
                return self._repo.index.diff_to_tree()  # type: ignore[no-any-return]
            return self._repo.index.diff_to_tree(self._repo.head.peel(pygit2.Tree))  # type: ignore[no-any-return]
        if base is None and target is None:
            return self._repo.diff()
        base_obj = self.resolve_commit(base) if base else self._repo.head.peel(pygit2.Commit)
        if target is None:
            return self._repo.diff(base_obj.id)
        return self._repo.diff(base_obj.id, self.resolve_commit(target).id)

    def blame(
        self, path: str, min_line: int | None = None, max_line: int | None = None
    ) -> pygit2.Blame:
        """Get blame for a file."""
        kwargs: dict[str, int] = {}
        if min_line is not None:
            kwargs["min_line"] = min_line
        if max_line is not None:
            kwargs["max_line"] = max_line
        return self._repo.blame(path, **kwargs)  # type: ignore[arg-type]

    def log(self, ref: str = "HEAD", limit: int = 50) -> list[pygit2.Commit]:
        """Get commit history."""
        try:
            start = self.resolve_ref(ref)
        except RefNotFoundError:
            return []
        commits: list[pygit2.Commit] = []
        for commit in self._repo.walk(start, pygit2.GIT_SORT_TIME):  # type: ignore[arg-type]
            if len(commits) >= limit:
                break
            commits.append(commit)
        return commits

    def show(self, ref: str = "HEAD") -> pygit2.Commit:
        """Get commit object."""
        return self.resolve_commit(ref)

    def branches(self, include_remote: bool = True) -> list[pygit2.Branch]:
        """List branches."""
        result = [self._repo.branches.local[n] for n in self._repo.branches.local]
        if include_remote:
            result.extend(self._repo.branches.remote[n] for n in self._repo.branches.remote)
        return result

    def tags(self) -> list[tuple[str, pygit2.Oid | str]]:
        """List tags as (name, oid) tuples."""
        return [
            (ref[len("refs/tags/") :], self._repo.references[ref].target)
            for ref in self._repo.references
            if ref.startswith("refs/tags/")
        ]

    def remotes(self) -> list[pygit2.Remote]:
        """List remotes."""
        return list(self._repo.remotes)

    def state(self) -> int:
        """Repository state. Compare with pygit2.GIT_REPOSITORY_STATE_*."""
        return self._repo.state()

    def current_branch(self) -> str | None:
        """Current branch name, or None if detached."""
        if self._repo.head_is_unborn:
            try:
                target = self._repo.references["HEAD"].target
                return target.replace("refs/heads/", "") if isinstance(target, str) else None
            except (KeyError, AttributeError):
                return "main"
        if self._repo.head_is_detached:
            return None
        return self._repo.head.shorthand

    # =========================================================================
    # Write Operations
    # =========================================================================

    def stage(self, paths: Sequence[str | Path]) -> None:
        """Stage files."""
        index = self._repo.index
        status = self._repo.status()
        for path in paths:
            p = str(path)
            flags = status.get(p, 0)
            if flags & (pygit2.GIT_STATUS_WT_NEW | pygit2.GIT_STATUS_WT_MODIFIED):
                index.add(p)
            elif flags & pygit2.GIT_STATUS_WT_DELETED:
                index.remove(p)
        index.write()

    def unstage(self, paths: Sequence[str | Path]) -> None:
        """Unstage files."""
        if self._repo.head_is_unborn:
            index = self._repo.index
            for p in paths:
                with contextlib.suppress(pygit2.GitError):
                    index.remove(str(p))
            index.write()
            return
        head_tree = self._repo.head.peel(pygit2.Tree)
        self._repo.checkout_tree(head_tree, paths=[str(p) for p in paths])  # type: ignore[no-untyped-call]

    def commit(self, message: str, allow_empty: bool = False) -> pygit2.Oid:
        """Create commit from staged changes."""
        index = self._repo.index
        if not allow_empty:
            if self._repo.head_is_unborn:
                if len(index) == 0:
                    raise NothingToCommitError
            else:
                diff = index.diff_to_tree(self._repo.head.peel(pygit2.Tree))
                if diff.stats.files_changed == 0:
                    raise NothingToCommitError

        tree_id = index.write_tree()
        sig = self._repo.default_signature
        parents = [] if self._repo.head_is_unborn else [self._repo.head.target]
        return self._repo.create_commit("HEAD", sig, sig, message, tree_id, parents)

    def create_branch(self, name: str, ref: str = "HEAD") -> pygit2.Branch:
        """Create branch."""
        if name in self._repo.branches.local:
            raise BranchExistsError(name)
        return self._repo.branches.local.create(name, self.resolve_commit(ref))

    def checkout(self, ref: str, create: bool = False) -> None:
        """Checkout branch or ref."""
        if create:
            self.create_branch(ref)
        if ref in self._repo.branches.local:
            branch = self._repo.branches.local[ref]
            self._repo.checkout(branch)
            self._repo.set_head(branch.name)
        elif ref in self._repo.branches.remote:
            remote = self._repo.branches.remote[ref]
            local_name = ref.split("/", 1)[-1]
            if local_name not in self._repo.branches.local:
                self._repo.branches.local.create(local_name, self._repo.get(remote.target))  # type: ignore[arg-type]
            self.checkout(local_name)
        else:
            oid = self.resolve_ref(ref)
            self._repo.checkout_tree(self._repo.get(oid))  # type: ignore[no-untyped-call]
            self._repo.set_head(oid)

    def delete_branch(self, name: str, force: bool = False) -> None:
        """Delete branch."""
        if name not in self._repo.branches.local:
            raise BranchNotFoundError(name)
        branch = self._repo.branches.local[name]
        if name == self.current_branch():
            raise BranchNotFoundError(f"Cannot delete current branch: {name}")
        if not force and not self._repo.descendant_of(self._repo.head.target, branch.target):
            raise UnmergedBranchError(name)
        branch.delete()

    def reset(self, ref: str, mode: str = "mixed") -> None:
        """Reset HEAD. mode: 'soft', 'mixed', or 'hard'."""
        modes = {
            "soft": pygit2.GIT_RESET_SOFT,
            "mixed": pygit2.GIT_RESET_MIXED,
            "hard": pygit2.GIT_RESET_HARD,
        }
        self._repo.reset(self.resolve_ref(ref), modes[mode])  # type: ignore[arg-type]

    def merge(self, ref: str) -> tuple[bool, pygit2.Oid | None]:
        """Merge ref. Returns (success, merge_commit_oid or None)."""
        their_oid = self.resolve_ref(ref)
        analysis, _ = self._repo.merge_analysis(their_oid)

        if analysis & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
            return True, None
        if analysis & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD:
            self._repo.checkout_tree(self._repo.get(their_oid))  # type: ignore[no-untyped-call]
            current = self.current_branch()
            if current:
                branch = self._repo.branches.local[current]
                branch.set_target(their_oid)
            self._repo.head.set_target(their_oid)
            return True, their_oid

        self._repo.merge(their_oid)
        if self._repo.index.conflicts:
            return False, None

        tree_id = self._repo.index.write_tree()
        sig = self._repo.default_signature
        oid = self._repo.create_commit(
            "HEAD", sig, sig, f"Merge {ref}", tree_id, [self._repo.head.target, their_oid]
        )
        self._repo.state_cleanup()
        return True, oid

    def abort_merge(self) -> None:
        """Abort in-progress merge."""
        self._repo.state_cleanup()
        self._repo.reset(self._repo.head.target, pygit2.GIT_RESET_HARD)  # type: ignore[arg-type]

    def stash_push(self, message: str | None = None, include_untracked: bool = False) -> pygit2.Oid:
        """Stash changes."""
        stasher = self._repo.default_signature
        # pygit2.Repository.stash() signature: stash(stasher, message, include_untracked, include_ignored, keep_index, paths)
        return self._repo.stash(stasher, message, include_untracked=include_untracked)

    def stash_pop(self, index: int = 0) -> None:
        """Pop stash entry."""
        stashes = list(self._repo.listall_stashes())
        if index >= len(stashes):
            raise StashNotFoundError(index)
        self._repo.stash_apply(index)
        self._repo.stash_drop(index)

    def stash_list(self) -> list[tuple[int, str, pygit2.Oid]]:
        """List stashes as (index, message, commit_oid) tuples."""
        return [(i, s.message, s.commit_id) for i, s in enumerate(self._repo.listall_stashes())]

    def create_tag(self, name: str, ref: str = "HEAD", message: str | None = None) -> pygit2.Oid:
        """Create tag."""
        target = self.resolve_ref(ref)
        if message:
            return self._repo.create_tag(
                name,
                target,
                pygit2.enums.ObjectType.COMMIT,
                self._repo.default_signature,
                message,
            )
        self._repo.references.create(f"refs/tags/{name}", target)
        return target

    def delete_tag(self, name: str) -> None:
        """Delete tag."""
        ref = f"refs/tags/{name}"
        if ref not in self._repo.references:
            raise RefNotFoundError(name)
        self._repo.references.delete(ref)

    def fetch(
        self, remote: str = "origin", callbacks: SystemCredentialCallback | None = None
    ) -> None:
        """Fetch from remote."""
        if remote not in [r.name for r in self._repo.remotes]:
            raise RemoteError(remote, "Remote not found")
        try:
            self._repo.remotes[remote].fetch(callbacks=callbacks or get_default_callbacks())
        except pygit2.GitError as e:
            if "authentication" in str(e).lower():
                raise AuthenticationError(remote) from e
            raise RemoteError(remote, str(e)) from e

    def push(
        self,
        remote: str = "origin",
        force: bool = False,
        callbacks: SystemCredentialCallback | None = None,
    ) -> None:
        """Push to remote."""
        if remote not in [r.name for r in self._repo.remotes]:
            raise RemoteError(remote, "Remote not found")
        branch = self.current_branch()
        if not branch:
            raise DetachedHeadError("push")
        prefix = "+" if force else ""
        refspec = f"{prefix}refs/heads/{branch}:refs/heads/{branch}"
        try:
            self._repo.remotes[remote].push(
                [refspec], callbacks=callbacks or get_default_callbacks()
            )
        except pygit2.GitError as e:
            if "authentication" in str(e).lower():
                raise AuthenticationError(remote) from e
            raise RemoteError(remote, str(e)) from e

    # =========================================================================
    # Helpers
    # =========================================================================

    def resolve_ref(self, ref: str) -> pygit2.Oid:
        """Resolve ref string to OID."""
        try:
            obj, _ = self._repo.resolve_refish(ref)
            return obj.id
        except (pygit2.GitError, KeyError) as e:
            raise RefNotFoundError(ref) from e

    def resolve_commit(self, ref: str) -> pygit2.Commit:
        """Resolve ref to Commit."""
        obj: pygit2.Object | None = self._repo.get(self.resolve_ref(ref))
        if isinstance(obj, pygit2.Tag):
            obj = obj.peel(pygit2.Commit)  # type: ignore[assignment]
        if not isinstance(obj, pygit2.Commit):
            raise RefNotFoundError(f"{ref} is not a commit")
        return obj
