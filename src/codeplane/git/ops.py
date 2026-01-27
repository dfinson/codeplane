"""Git operations via pygit2 - returns serializable data models."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pygit2

from codeplane.git.credentials import SystemCredentialCallback, get_default_callbacks
from codeplane.git.errors import (
    AuthenticationError,
    BranchExistsError,
    BranchNotFoundError,
    DetachedHeadError,
    GitError,
    NotARepositoryError,
    NothingToCommitError,
    RefNotFoundError,
    RemoteError,
    StashNotFoundError,
    UnmergedBranchError,
)
from codeplane.git.models import (
    BlameHunk,
    BlameInfo,
    BranchInfo,
    CommitInfo,
    DiffFile,
    DiffInfo,
    MergeResult,
    RefInfo,
    RemoteInfo,
    Signature,
    StashEntry,
    TagInfo,
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
    # Read Operations - return serializable models
    # =========================================================================

    def status(self) -> dict[str, int]:
        """Get status flags by path. Use pygit2.GIT_STATUS_* to interpret."""
        return self._repo.status()

    def head(self) -> RefInfo:
        """Get HEAD reference info."""
        ref = self._repo.head
        return RefInfo(
            name=ref.name,
            target_sha=str(ref.target),
            shorthand=ref.shorthand,
            is_detached=self._repo.head_is_detached,
        )

    def head_commit(self) -> CommitInfo | None:
        """Get HEAD commit, or None if unborn."""
        if self._repo.head_is_unborn:
            return None
        return self._to_commit_info(self._repo.head.peel(pygit2.Commit))

    def diff(
        self,
        base: str | None = None,
        target: str | None = None,
        staged: bool = False,
        include_patch: bool = False,
    ) -> DiffInfo:
        """Generate diff."""
        if staged:
            if self._repo.head_is_unborn:
                raw = self._repo.index.diff_to_tree()
            else:
                raw = self._repo.index.diff_to_tree(self._repo.head.peel(pygit2.Tree))
        elif base is None and target is None:
            raw = self._repo.diff()
        else:
            # Handle unborn HEAD when target is specified without base
            if base is None and self._repo.head_is_unborn:
                raise RefNotFoundError("HEAD (unborn)")
            base_obj = (
                self._resolve_commit_obj(base) if base else self._repo.head.peel(pygit2.Commit)
            )
            if target is None:
                raw = self._repo.diff(base_obj.id)
            else:
                raw = self._repo.diff(base_obj.id, self._resolve_commit_obj(target).id)
        return self._to_diff_info(raw, include_patch)

    def blame(
        self, path: str, min_line: int | None = None, max_line: int | None = None
    ) -> BlameInfo:
        """Get blame for a file."""
        kwargs: dict[str, int] = {}
        if min_line is not None:
            kwargs["min_line"] = min_line
        if max_line is not None:
            kwargs["max_line"] = max_line
        raw = self._repo.blame(path, **kwargs)  # type: ignore[arg-type]
        return self._to_blame_info(path, raw)

    def log(self, ref: str = "HEAD", limit: int = 50) -> list[CommitInfo]:
        """Get commit history."""
        try:
            start = self._resolve_ref_oid(ref)
        except RefNotFoundError:
            return []
        commits: list[CommitInfo] = []
        for commit in self._repo.walk(start, pygit2.GIT_SORT_TIME):  # type: ignore[arg-type]
            if len(commits) >= limit:
                break
            commits.append(self._to_commit_info(commit))
        return commits

    def show(self, ref: str = "HEAD") -> CommitInfo:
        """Get commit info."""
        return self._to_commit_info(self._resolve_commit_obj(ref))

    def branches(self, include_remote: bool = True) -> list[BranchInfo]:
        """List branches."""
        result = [
            self._to_branch_info(self._repo.branches.local[n]) for n in self._repo.branches.local
        ]
        if include_remote:
            result.extend(
                self._to_branch_info(self._repo.branches.remote[n])
                for n in self._repo.branches.remote
            )
        return result

    def tags(self) -> list[TagInfo]:
        """List tags."""
        result: list[TagInfo] = []
        for refname in self._repo.references:
            if not refname.startswith("refs/tags/"):
                continue
            name = refname[len("refs/tags/") :]
            target = self._repo.references[refname].target
            obj = self._repo.get(target)
            if isinstance(obj, pygit2.Tag):
                tagger = self._to_signature(obj.tagger) if obj.tagger else None
                result.append(TagInfo(name, str(obj.target), True, obj.message, tagger))
            else:
                result.append(TagInfo(name, str(target), False))
        return result

    def remotes(self) -> list[RemoteInfo]:
        """List remotes."""
        return [
            RemoteInfo(r.name or "", r.url or "", getattr(r, "push_url", None))
            for r in self._repo.remotes
        ]

    def state(self) -> int:
        """Repository state. Compare with pygit2.GIT_REPOSITORY_STATE_*."""
        return self._repo.state()

    def current_branch(self) -> str | None:
        """Current branch name, or None if detached or unborn."""
        if self._repo.head_is_unborn:
            try:
                ref = self._repo.references["HEAD"]
                target = getattr(ref, "target", None)
                if isinstance(target, str) and target.startswith("refs/heads/"):
                    return target[len("refs/heads/") :]
            except KeyError:
                pass
            return None
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
            p = self._normalize_path(path)
            flags = status.get(p, 0)
            if flags & (pygit2.GIT_STATUS_WT_NEW | pygit2.GIT_STATUS_WT_MODIFIED):
                index.add(p)
            elif flags & pygit2.GIT_STATUS_WT_DELETED:
                index.remove(p)
        index.write()

    def _normalize_path(self, path: str | Path) -> str:
        """Normalize path to repo-relative string."""
        p = Path(path)
        if p.is_absolute():
            with contextlib.suppress(ValueError):
                p = p.relative_to(self.path)
        return str(p)

    def unstage(self, paths: Sequence[str | Path]) -> None:
        """Unstage files (keeps working tree changes)."""
        index = self._repo.index
        if self._repo.head_is_unborn:
            for p in paths:
                with contextlib.suppress(pygit2.GitError):
                    index.remove(str(p))
            index.write()
            return
        # Reset index entries to HEAD without touching working tree
        head_commit = self._repo.head.peel(pygit2.Commit)
        head_tree = head_commit.tree
        for p in paths:
            path_str = self._normalize_path(p)
            try:
                entry = head_tree[path_str]
                index.add(pygit2.IndexEntry(path_str, entry.id, entry.filemode))
            except KeyError:
                # File not in HEAD - remove from index
                with contextlib.suppress(pygit2.GitError):
                    index.remove(path_str)
        index.write()

    def commit(self, message: str, allow_empty: bool = False) -> str:
        """Create commit from staged changes. Returns commit sha."""
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
        return str(self._repo.create_commit("HEAD", sig, sig, message, tree_id, parents))

    def amend(self, message: str | None = None) -> str:
        """Amend the most recent commit. Returns commit sha."""
        if self._repo.head_is_unborn:
            raise GitError("Cannot amend: no commits yet")
        head_commit = self._repo.head.peel(pygit2.Commit)
        index = self._repo.index
        tree_id = index.write_tree()
        new_message = message if message is not None else head_commit.message
        oid = self._repo.create_commit(
            "HEAD",
            head_commit.author,
            self._repo.default_signature,
            new_message,
            tree_id,
            head_commit.parent_ids,
        )
        return str(oid)

    def create_branch(self, name: str, ref: str = "HEAD") -> BranchInfo:
        """Create branch."""
        if name in self._repo.branches.local:
            raise BranchExistsError(name)
        branch = self._repo.branches.local.create(name, self._resolve_commit_obj(ref))
        return self._to_branch_info(branch)

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
            oid = self._resolve_ref_oid(ref)
            self._repo.checkout_tree(self._repo.get(oid))  # type: ignore[no-untyped-call]
            self._repo.set_head(oid)

    def delete_branch(self, name: str, force: bool = False) -> None:
        """Delete branch."""
        if name not in self._repo.branches.local:
            raise BranchNotFoundError(name)
        branch = self._repo.branches.local[name]
        if name == self.current_branch():
            raise GitError(f"Cannot delete current branch: {name}")
        if not force and not self._repo.descendant_of(self._repo.head.target, branch.target):
            raise UnmergedBranchError(name)
        branch.delete()

    def rename_branch(self, old_name: str, new_name: str) -> BranchInfo:
        """Rename a branch."""
        if old_name not in self._repo.branches.local:
            raise BranchNotFoundError(old_name)
        if new_name in self._repo.branches.local:
            raise BranchExistsError(new_name)
        branch = self._repo.branches.local[old_name]
        branch.rename(new_name)
        return self._to_branch_info(self._repo.branches.local[new_name])

    def reset(self, ref: str, mode: str = "mixed") -> None:
        """Reset HEAD. mode: 'soft', 'mixed', or 'hard'."""
        modes = {
            "soft": pygit2.GIT_RESET_SOFT,
            "mixed": pygit2.GIT_RESET_MIXED,
            "hard": pygit2.GIT_RESET_HARD,
        }
        if mode not in modes:
            raise ValueError(
                f"Invalid reset mode {mode!r}. Expected one of: {', '.join(sorted(modes))}"
            )
        self._repo.reset(self._resolve_ref_oid(ref), modes[mode])  # type: ignore[arg-type]

    def merge(self, ref: str) -> MergeResult:
        """Merge ref. Returns MergeResult with success, commit_sha, conflict_paths."""
        their_oid = self._resolve_ref_oid(ref)
        analysis, _ = self._repo.merge_analysis(their_oid)

        if analysis & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
            return MergeResult(True, None)
        if analysis & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD:
            self._repo.checkout_tree(self._repo.get(their_oid))  # type: ignore[no-untyped-call]
            current = self.current_branch()
            if current:
                branch = self._repo.branches.local[current]
                branch.set_target(their_oid)
            self._repo.head.set_target(their_oid)
            return MergeResult(True, str(their_oid))

        self._repo.merge(their_oid)
        if self._repo.index.conflicts:
            # conflicts yields (ancestor, ours, theirs) entries; extract unique paths
            paths: set[str] = set()
            for ancestor, ours, theirs in self._repo.index.conflicts:
                for entry in (ancestor, ours, theirs):
                    if entry:
                        paths.add(entry.path)
            return MergeResult(False, None, tuple(sorted(paths)))

        tree_id = self._repo.index.write_tree()
        sig = self._repo.default_signature
        oid = self._repo.create_commit(
            "HEAD", sig, sig, f"Merge {ref}", tree_id, [self._repo.head.target, their_oid]
        )
        self._repo.state_cleanup()
        return MergeResult(True, str(oid))

    def abort_merge(self) -> None:
        """Abort in-progress merge."""
        self._repo.state_cleanup()
        self._repo.reset(self._repo.head.target, pygit2.GIT_RESET_HARD)  # type: ignore[arg-type]

    def merge_analysis(self, ref: str) -> tuple[bool, bool, bool]:
        """Analyze potential merge. Returns (up_to_date, fastforward_possible, conflicts_likely)."""
        their_oid = self._resolve_ref_oid(ref)
        analysis, _ = self._repo.merge_analysis(their_oid)
        up_to_date = bool(analysis & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE)
        fastforward = bool(analysis & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD)
        normal = bool(analysis & pygit2.GIT_MERGE_ANALYSIS_NORMAL)
        return up_to_date, fastforward, normal  # normal means potential conflicts

    def cherrypick(self, ref: str) -> tuple[bool, list[str]]:
        """Cherry-pick a commit. Returns (success, conflict_paths)."""
        commit = self._resolve_commit_obj(ref)
        self._repo.cherrypick(commit.id)
        if self._repo.index.conflicts:
            paths: set[str] = set()
            for ancestor, ours, theirs in self._repo.index.conflicts:
                for entry in (ancestor, ours, theirs):
                    if entry:
                        paths.add(entry.path)
            return False, sorted(paths)
        # Auto-commit if no conflicts
        tree_id = self._repo.index.write_tree()
        sig = self._repo.default_signature
        self._repo.create_commit(
            "HEAD", commit.author, sig, commit.message, tree_id, [self._repo.head.target]
        )
        self._repo.state_cleanup()
        return True, []

    def revert(self, ref: str) -> tuple[bool, list[str]]:
        """Revert a commit. Returns (success, conflict_paths)."""
        commit = self._resolve_commit_obj(ref)
        self._repo.revert_commit(commit, self._repo.head.peel(pygit2.Commit))
        if self._repo.index.conflicts:
            paths: set[str] = set()
            for ancestor, ours, theirs in self._repo.index.conflicts:
                for entry in (ancestor, ours, theirs):
                    if entry:
                        paths.add(entry.path)
            return False, sorted(paths)
        # Auto-commit if no conflicts
        tree_id = self._repo.index.write_tree()
        sig = self._repo.default_signature
        self._repo.create_commit(
            "HEAD",
            sig,
            sig,
            f'Revert "{commit.message.splitlines()[0]}"',
            tree_id,
            [self._repo.head.target],
        )
        self._repo.state_cleanup()
        return True, []

    def stash_push(self, message: str | None = None, include_untracked: bool = False) -> str:
        """Stash changes. Returns stash commit sha."""
        stasher = self._repo.default_signature
        oid = self._repo.stash(stasher, message, include_untracked=include_untracked)
        return str(oid)

    def stash_pop(self, index: int = 0) -> None:
        """Pop stash entry."""
        stashes = list(self._repo.listall_stashes())
        if index >= len(stashes):
            raise StashNotFoundError(index)
        self._repo.stash_apply(index)
        self._repo.stash_drop(index)

    def stash_list(self) -> list[StashEntry]:
        """List stash entries."""
        return [
            StashEntry(i, s.message, str(s.commit_id))
            for i, s in enumerate(self._repo.listall_stashes())
        ]

    def create_tag(self, name: str, ref: str = "HEAD", message: str | None = None) -> str:
        """Create tag. Returns target sha."""
        target = self._resolve_ref_oid(ref)
        if message:
            oid = self._repo.create_tag(
                name,
                target,
                pygit2.enums.ObjectType.COMMIT,
                self._repo.default_signature,
                message,
            )
            return str(oid)
        self._repo.references.create(f"refs/tags/{name}", target)
        return str(target)

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
    # Internal Helpers
    # =========================================================================

    def _resolve_ref_oid(self, ref: str) -> pygit2.Oid:
        """Resolve ref string to OID."""
        try:
            obj, _ = self._repo.resolve_refish(ref)
            return obj.id
        except (pygit2.GitError, KeyError) as e:
            raise RefNotFoundError(ref) from e

    def _resolve_commit_obj(self, ref: str) -> pygit2.Commit:
        """Resolve ref to Commit."""
        obj: pygit2.Object | None = self._repo.get(self._resolve_ref_oid(ref))
        if isinstance(obj, pygit2.Tag):
            obj = obj.peel(pygit2.Commit)  # type: ignore[assignment]
        if not isinstance(obj, pygit2.Commit):
            raise RefNotFoundError(f"{ref} is not a commit")
        return obj

    def _to_signature(self, sig: pygit2.Signature) -> Signature:
        """Convert pygit2.Signature to Signature."""
        return Signature(
            name=sig.name,
            email=sig.email,
            time=datetime.fromtimestamp(sig.time, tz=UTC),
        )

    def _to_commit_info(self, commit: pygit2.Commit) -> CommitInfo:
        """Convert pygit2.Commit to CommitInfo."""
        sha = str(commit.id)
        return CommitInfo(
            sha=sha,
            short_sha=sha[:7],
            message=commit.message,
            author=self._to_signature(commit.author),
            committer=self._to_signature(commit.committer),
            parent_shas=tuple(str(p) for p in commit.parent_ids),
        )

    def _to_branch_info(self, branch: pygit2.Branch) -> BranchInfo:
        """Convert pygit2.Branch to BranchInfo."""
        upstream = None
        if hasattr(branch, "upstream") and branch.upstream:
            upstream = branch.upstream.shorthand
        is_remote = branch.name.startswith("refs/remotes/")
        return BranchInfo(
            name=branch.name,
            short_name=branch.shorthand,
            target_sha=str(branch.target),
            is_remote=is_remote,
            upstream=upstream,
        )

    def _to_diff_info(self, diff: pygit2.Diff, include_patch: bool = False) -> DiffInfo:
        """Convert pygit2.Diff to DiffInfo."""
        files: list[DiffFile] = []
        for delta in diff.deltas:
            status_map = {
                pygit2.GIT_DELTA_ADDED: "added",
                pygit2.GIT_DELTA_DELETED: "deleted",
                pygit2.GIT_DELTA_MODIFIED: "modified",
                pygit2.GIT_DELTA_RENAMED: "renamed",
                pygit2.GIT_DELTA_COPIED: "copied",
            }
            files.append(
                DiffFile(
                    old_path=delta.old_file.path if delta.old_file else None,
                    new_path=delta.new_file.path if delta.new_file else None,
                    status=status_map.get(delta.status, "unknown"),
                    additions=0,  # Would need to parse patch for accurate counts
                    deletions=0,
                )
            )
        stats = diff.stats
        return DiffInfo(
            files=tuple(files),
            total_additions=stats.insertions,
            total_deletions=stats.deletions,
            files_changed=stats.files_changed,
            patch=diff.patch if include_patch else None,
        )

    def _to_blame_info(self, path: str, blame: pygit2.Blame) -> BlameInfo:
        """Convert pygit2.Blame to BlameInfo."""
        hunks: list[BlameHunk] = []
        for hunk in blame:
            hunks.append(
                BlameHunk(
                    commit_sha=str(hunk.final_commit_id),
                    author=self._to_signature(hunk.final_committer),  # type: ignore[arg-type]
                    start_line=hunk.final_start_line_number,
                    line_count=hunk.lines_in_hunk,
                    original_start_line=hunk.orig_start_line_number,
                )
            )
        return BlameInfo(path=path, hunks=tuple(hunks))
