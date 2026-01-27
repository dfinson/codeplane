"""Repository access layer - owns pygit2.Repository and exposes computed facts."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pygit2

from codeplane.git.errors import (
    AuthenticationError,
    GitError,
    NotARepositoryError,
    RefNotFoundError,
    RemoteError,
)


class RepoAccess:
    """Owns pygit2.Repository and provides normalized access to repo state."""

    def __init__(self, repo_path: Path | str) -> None:
        self._path = Path(repo_path)
        try:
            self._repo = pygit2.Repository(str(self._path))
        except pygit2.GitError as e:
            raise NotARepositoryError(str(self._path)) from e

    @property
    def repo(self) -> pygit2.Repository:
        return self._repo

    @property
    def path(self) -> Path:
        return Path(self._repo.workdir) if self._repo.workdir else self._path

    @property
    def index(self) -> pygit2.Index:
        return self._repo.index  # type: ignore[no-any-return]

    # =========================================================================
    # Repository State Facts
    # =========================================================================

    @property
    def is_unborn(self) -> bool:
        return self._repo.head_is_unborn

    @property
    def is_detached(self) -> bool:
        return self._repo.head_is_detached

    @property
    def head_ref(self) -> pygit2.Reference:
        return self._repo.head

    @property
    def head_target(self) -> pygit2.Oid:
        """Return HEAD target as Oid, resolving symbolic refs."""
        target = self._repo.head.target
        if isinstance(target, str):
            # Symbolic ref, resolve it
            return self._repo.references[target].target  # type: ignore[return-value]
        return target

    def head_commit(self) -> pygit2.Commit | None:
        if self.is_unborn:
            return None
        return self._repo.head.peel(pygit2.Commit)

    def head_tree(self) -> pygit2.Tree | None:
        if self.is_unborn:
            return None
        return self._repo.head.peel(pygit2.Tree)

    @property
    def default_signature(self) -> pygit2.Signature:
        return self._repo.default_signature

    def current_branch_name(self) -> str | None:
        if self.is_unborn:
            try:
                ref = self._repo.references["HEAD"]
                target = getattr(ref, "target", None)
                if isinstance(target, str) and target.startswith("refs/heads/"):
                    return target[len("refs/heads/") :]
            except KeyError:
                # HEAD reference missing in unborn repo; no branch name available
                pass
            return None
        if self.is_detached:
            return None
        return self._repo.head.shorthand

    def state(self) -> int:
        return self._repo.state()

    # =========================================================================
    # Resolution Helpers
    # =========================================================================

    def resolve_ref_oid(self, ref: str) -> pygit2.Oid:
        try:
            obj, _ = self._repo.resolve_refish(ref)
            return obj.id
        except (pygit2.GitError, KeyError) as e:
            raise RefNotFoundError(ref) from e

    def resolve_commit(self, ref: str) -> pygit2.Commit:
        obj: pygit2.Object | None = self._repo.get(self.resolve_ref_oid(ref))
        if isinstance(obj, pygit2.Tag):
            obj = obj.peel(pygit2.Commit)  # type: ignore[assignment]
        if not isinstance(obj, pygit2.Commit):
            raise RefNotFoundError(f"{ref} is not a commit")
        return obj

    # =========================================================================
    # Must Helpers (assert replacements with proper errors)
    # =========================================================================

    def must_head_target(self) -> pygit2.Oid:
        """Return HEAD target Oid, raising if unborn."""
        if self.is_unborn:
            raise GitError("HEAD has no target (unborn branch)")
        return self.head_target

    def must_head_commit(self) -> pygit2.Commit:
        commit = self.head_commit()
        if commit is None:
            raise GitError("HEAD has no commits (unborn branch)")
        return commit

    def must_head_tree(self) -> pygit2.Tree:
        tree = self.head_tree()
        if tree is None:
            raise GitError("HEAD has no tree (unborn branch)")
        return tree

    def must_local_branch(self, name: str) -> pygit2.Branch:
        branch = self.local_branch(name)
        if branch is None:
            raise GitError(f"Branch {name!r} not found")
        return branch

    def must_remote_branch(self, name: str) -> pygit2.Branch:
        branch = self.remote_branch(name)
        if branch is None:
            raise GitError(f"Remote branch {name!r} not found")
        return branch

    # =========================================================================
    # Normalization Helpers
    # =========================================================================

    def normalize_path(self, path: str | Path) -> str:
        p = Path(path)
        if p.is_absolute():
            with contextlib.suppress(ValueError):
                p = p.relative_to(self.path)
        return str(p)

    # =========================================================================
    # Remote Access
    # =========================================================================

    def get_remote(self, name: str) -> pygit2.Remote:
        if name not in [r.name for r in self._repo.remotes]:
            raise RemoteError(name, "Remote not found")
        return self._repo.remotes[name]

    # =========================================================================
    # Branch Access
    # =========================================================================

    def local_branch(self, name: str) -> pygit2.Branch | None:
        if name in self._repo.branches.local:
            return self._repo.branches.local[name]
        return None

    def remote_branch(self, name: str) -> pygit2.Branch | None:
        if name in self._repo.branches.remote:
            return self._repo.branches.remote[name]
        return None

    def has_local_branch(self, name: str) -> bool:
        return name in self._repo.branches.local

    def has_remote_branch(self, name: str) -> bool:
        return name in self._repo.branches.remote

    def branch_target_oid(self, branch: pygit2.Branch) -> pygit2.Oid:
        """Get branch target as Oid, resolving symbolic refs."""
        target = branch.target
        if isinstance(target, str):
            return self._repo.references[target].target  # type: ignore[return-value]
        return target

    # =========================================================================
    # Low-level pygit2 Operations (all pygit2 quirks live here)
    # =========================================================================

    def status(self) -> dict[str, int]:
        return self._repo.status()

    def blame(self, path: str, **kwargs: int) -> pygit2.Blame:
        return self._repo.blame(path, **kwargs)  # type: ignore[arg-type]

    def walk_commits(self, start: pygit2.Oid, sort: int) -> pygit2.Walker:
        return self._repo.walk(start, sort)  # type: ignore[arg-type]

    def diff_working_tree(self) -> pygit2.Diff:
        return self._repo.diff()

    def diff_refs(self, base_id: pygit2.Oid, target_id: pygit2.Oid | None = None) -> pygit2.Diff:
        if target_id is None:
            return self._repo.diff(base_id)
        return self._repo.diff(base_id, target_id)

    def get_empty_tree(self) -> pygit2.Tree:
        """Get an empty tree for diff operations (e.g., staged diff on unborn repo)."""
        builder = self._repo.TreeBuilder()
        empty_tree_oid = builder.write()
        return self._repo.get(empty_tree_oid)  # type: ignore[return-value]

    def checkout_branch(self, branch: pygit2.Branch) -> None:
        self._repo.checkout(branch)
        self._repo.set_head(branch.name)

    def checkout_detached(self, oid: pygit2.Oid) -> None:
        self._repo.checkout_tree(self._repo.get(oid))  # type: ignore[no-untyped-call]
        self._repo.set_head(oid)

    def create_local_branch(self, name: str, target: pygit2.Commit) -> pygit2.Branch:
        return self._repo.branches.local.create(name, target)

    def set_branch_target(self, branch: pygit2.Branch, oid: pygit2.Oid) -> None:
        branch.set_target(oid)

    def set_head_target(self, oid: pygit2.Oid) -> None:
        self._repo.head.set_target(oid)

    def set_head(self, refname: str | pygit2.Oid) -> None:
        """Set HEAD to a ref name (e.g., 'refs/heads/main') or oid."""
        self._repo.set_head(refname)

    def merge_analysis(self, their_oid: pygit2.Oid) -> tuple[int, int]:
        return self._repo.merge_analysis(their_oid)

    def merge(self, their_oid: pygit2.Oid) -> None:
        self._repo.merge(their_oid)

    def cherrypick(self, commit_id: pygit2.Oid) -> None:
        self._repo.cherrypick(commit_id)

    def revert_commit(self, commit: pygit2.Commit, our_commit: pygit2.Commit) -> None:
        self._repo.revert_commit(commit, our_commit)

    def reset(self, oid: pygit2.Oid, reset_type: int) -> None:
        self._repo.reset(oid, reset_type)  # type: ignore[arg-type]

    def state_cleanup(self) -> None:
        self._repo.state_cleanup()

    def stash(
        self, stasher: pygit2.Signature, message: str | None, *, include_untracked: bool
    ) -> pygit2.Oid:
        return self._repo.stash(stasher, message, include_untracked=include_untracked)

    def stash_apply(self, index: int) -> None:
        self._repo.stash_apply(index)

    def stash_drop(self, index: int) -> None:
        self._repo.stash_drop(index)

    def listall_stashes(self) -> list[Any]:
        return list(self._repo.listall_stashes())

    def create_tag(
        self,
        name: str,
        target: pygit2.Oid,
        obj_type: pygit2.enums.ObjectType,
        tagger: pygit2.Signature,
        message: str,
    ) -> pygit2.Oid:
        return self._repo.create_tag(name, target, obj_type, tagger, message)

    def create_reference(self, name: str, target: pygit2.Oid) -> None:
        self._repo.references.create(name, target)

    def delete_reference(self, name: str) -> None:
        self._repo.references.delete(name)

    def has_reference(self, name: str) -> bool:
        return name in self._repo.references

    def get_reference_target(self, name: str) -> pygit2.Oid:
        target = self._repo.references[name].target
        if isinstance(target, str):
            return self._repo.references[target].target  # type: ignore[return-value]
        return target

    def get_object(self, oid: pygit2.Oid) -> pygit2.Object | None:
        return self._repo.get(oid)

    def create_commit(
        self,
        ref: str | None,
        author: pygit2.Signature,
        committer: pygit2.Signature,
        message: str,
        tree_id: pygit2.Oid,
        parents: list[pygit2.Oid],
    ) -> pygit2.Oid:
        return self._repo.create_commit(ref, author, committer, message, tree_id, parents)

    def descendant_of(self, commit: pygit2.Oid, ancestor: pygit2.Oid) -> bool:
        return self._repo.descendant_of(commit, ancestor)

    @property
    def references(self) -> Any:
        return self._repo.references

    @property
    def remotes(self) -> Any:
        return self._repo.remotes

    @property
    def branches(self) -> pygit2.branches.Branches:
        return self._repo.branches

    # =========================================================================
    # Index Helpers
    # =========================================================================

    def best_effort_index_remove(self, paths: Iterator[str]) -> None:
        """
        Remove paths from index, suppressing errors for missing entries.

        Guarantees: Processes all paths even if some fail. Always writes index.
        Does not raise for missing entries or already-removed paths.
        """
        for p in paths:
            with contextlib.suppress(pygit2.GitError):
                self.index.remove(p)
        self.index.write()

    def index_reset_entry(self, path: str, tree: pygit2.Tree) -> None:
        """Reset a single index entry to match tree, or remove if not in tree."""
        try:
            entry = tree[path]
            self.index.add(pygit2.IndexEntry(path, entry.id, entry.filemode))
        except KeyError:
            with contextlib.suppress(pygit2.GitError):
                self.index.remove(path)

    # =========================================================================
    # Tag Iteration
    # =========================================================================

    def iter_tags(self) -> Iterator[tuple[str, pygit2.Oid, pygit2.Tag | None]]:
        """
        Iterate tags as (name, target_oid, tag_object_or_none).

        Contract:
        - name: normalized tag name (no 'refs/tags/' prefix)
        - target_oid: for annotated tags, the commit the tag points to;
          for lightweight tags, the direct target Oid
        - tag_obj: pygit2.Tag object for annotated tags, None for lightweight
        """
        for refname in self._repo.references:
            if not refname.startswith("refs/tags/"):
                continue
            name = refname[len("refs/tags/") :]
            raw_target = self._repo.references[refname].target
            # Resolve symbolic refs
            if isinstance(raw_target, str):
                target: pygit2.Oid = self._repo.references[raw_target].target  # type: ignore[assignment]
            else:
                target = raw_target
            obj = self._repo.get(target)
            if isinstance(obj, pygit2.Tag):
                yield name, obj.target, obj
            else:
                yield name, target, None

    # =========================================================================
    # Remote Operations (centralized error handling)
    # =========================================================================

    def run_remote_operation(
        self,
        remote_name: str,
        op_name: str,
        operation: Callable[[pygit2.Remote], Any],
    ) -> Any:
        """
        Run a remote operation with centralized error mapping.

        Args:
            remote_name: Name of the remote (e.g., "origin")
            op_name: Human-readable operation name for error messages (e.g., "fetch")
            operation: Callable that takes a pygit2.Remote and performs the operation.
                       Can be a lambda, partial, or named function.

        Error mapping:
            - Authentication/credential errors → AuthenticationError(remote_name, op_name)
            - Other pygit2.GitError → RemoteError(remote_name, "{op_name} failed: {msg}")
        """
        remote = self.get_remote(remote_name)
        try:
            return operation(remote)
        except pygit2.GitError as e:
            msg = str(e).lower()
            if "authentication" in msg or "credential" in msg:
                raise AuthenticationError(remote_name, op_name) from e
            raise RemoteError(remote_name, f"{op_name} failed: {e}") from e

    # =========================================================================
    # Worktree Operations
    # =========================================================================

    def list_worktrees(self) -> list[str]:
        """List worktree names (excluding main)."""
        return list(self._repo.list_worktrees())

    def lookup_worktree(self, name: str) -> Any:
        """Get worktree by name. Returns pygit2.Worktree or raises."""
        return self._repo.lookup_worktree(name)

    def add_worktree(self, name: str, path: str, ref: pygit2.Reference | None = None) -> Any:
        """Add a new worktree. Returns pygit2.Worktree."""
        if ref is not None:
            return self._repo.add_worktree(name, path, ref)
        # For detached HEAD worktrees, we need to pass a commit reference
        return self._repo.add_worktree(name, path)

    def is_worktree(self) -> bool:
        """True if this repository is a worktree (not the main working directory)."""
        # pygit2 may not have this attribute in all versions
        return bool(getattr(self._repo, "is_worktree", False))

    @property
    def workdir(self) -> str | None:
        """Working directory path, or None for bare repos."""
        return self._repo.workdir

    def worktree_gitdir(self, name: str) -> Path:
        """Get the git admin directory for a worktree (.git/worktrees/<name>)."""
        return Path(self._repo.path) / "worktrees" / name

    def remove_worktree(self, name: str, force: bool = False) -> None:
        """Remove worktree using git subprocess for correctness."""
        import subprocess

        # Resolve actual path since git expects path, not name
        wt = self.lookup_worktree(name)
        wt_path = wt.path

        cmd = ["git", "worktree", "remove"]
        if force:
            # Need --force twice: once for dirty, once for locked
            cmd.extend(["--force", "--force"])
        cmd.append(wt_path)

        result = subprocess.run(
            cmd,
            cwd=str(self.path),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise GitError(f"Failed to remove worktree: {result.stderr.strip()}")

    # =========================================================================
    # Submodule Operations
    # =========================================================================

    def listall_submodules(self) -> list[str]:
        """List submodule names."""
        return list(self._repo.listall_submodules())

    def lookup_submodule(self, name: str) -> Any:
        """Get submodule by name."""
        # Use repo.submodules collection (pygit2 >= 1.12)
        submodules = getattr(self._repo, "submodules", None)
        if submodules is None:
            raise GitError("Submodule operations not supported in this pygit2 version")
        sm = submodules.get(name)
        if sm is None:
            raise GitError(f"Submodule not found: {name}")
        return sm

    def init_submodule(self, name: str, overwrite: bool = False) -> None:
        """Initialize a submodule."""
        submodule = self.lookup_submodule(name)
        submodule.init(overwrite)

    def open_submodule_repo(self, name: str) -> pygit2.Repository:
        """Open the repository for an initialized submodule."""
        submodule = self.lookup_submodule(name)
        return submodule.open()  # type: ignore[no-any-return]

    def submodule_name_for_path(self, path: str) -> str | None:
        """Get submodule name for a given path, or None if not found."""
        for name in self.listall_submodules():
            try:
                sm = self.lookup_submodule(name)
                if sm.path == path:
                    return name
            except pygit2.GitError:
                continue
        return None

    def lookup_submodule_by_path(self, path: str) -> Any:
        """Get submodule by path. Raises GitError if not found."""
        name = self.submodule_name_for_path(path)
        if name is None:
            raise GitError(f"Submodule not found at path: {path}")
        return self.lookup_submodule(name)

    # =========================================================================
    # Merge Base
    # =========================================================================

    def merge_base(self, oid1: pygit2.Oid, oid2: pygit2.Oid) -> pygit2.Oid | None:
        """Find merge base of two commits. Returns None if unrelated."""
        try:
            return self._repo.merge_base(oid1, oid2)
        except pygit2.GitError:
            return None
