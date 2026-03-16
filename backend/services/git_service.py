"""Git worktree and branch operations."""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from backend.config import TowerConfig

log = structlog.get_logger()


class GitError(Exception):
    """Raised when a git subprocess fails."""

    def __init__(self, message: str, stderr: str = "") -> None:
        self.stderr = stderr
        super().__init__(message)


class GitService:
    """Manages git worktrees, branches, and workspace isolation."""

    def __init__(self, config: TowerConfig) -> None:
        self._worktrees_dirname = config.runtime.worktrees_dirname

    async def _run_git(self, *args: str, cwd: str | Path) -> str:
        """Run a git command and return stdout. Raises GitError on failure."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            raise GitError("git executable not found. Ensure Git is installed and available on PATH.") from exc
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode().strip()
        stderr = stderr_bytes.decode().strip()

        if proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed (exit {proc.returncode}): {stderr}",
                stderr=stderr,
            )
        return stdout

    async def diff(self, diff_spec: str, *, cwd: str | Path) -> str:
        """Run `git diff <diff_spec>` and return raw output."""
        return await self._run_git("diff", diff_spec, cwd=cwd)

    async def merge_base(self, ref1: str, ref2: str, *, cwd: str | Path) -> str:
        """Return the merge-base commit between two refs."""
        return (await self._run_git("merge-base", ref1, ref2, cwd=cwd)).strip()

    async def add_intent_to_add(self, *, cwd: str | Path) -> None:
        """Mark untracked files as intent-to-add so they appear in diffs."""
        with contextlib.suppress(GitError):
            await self._run_git("add", "-N", ".", cwd=cwd)

    # ------------------------------------------------------------------
    # Merge-back operations
    # ------------------------------------------------------------------

    async def checkout(self, branch: str, *, cwd: str | Path) -> None:
        """Check out a branch in the given working directory."""
        await self._run_git("checkout", branch, cwd=cwd)

    async def merge_ff_only(self, branch: str, *, cwd: str | Path) -> None:
        """Fast-forward merge only. Raises GitError if not possible."""
        await self._run_git("merge", "--ff-only", branch, cwd=cwd)

    async def merge(self, branch: str, *, cwd: str | Path, message: str | None = None) -> None:
        """Merge a branch. Raises GitError on conflict."""
        args = ["merge", "--no-edit", branch]
        if message:
            args = ["merge", "-m", message, branch]
        await self._run_git(*args, cwd=cwd)

    async def merge_abort(self, *, cwd: str | Path) -> None:
        """Abort an in-progress merge."""
        with contextlib.suppress(GitError):
            await self._run_git("merge", "--abort", cwd=cwd)

    async def cherry_pick(self, commit_range: str, *, cwd: str | Path) -> None:
        """Cherry-pick a range of commits (e.g. 'base_ref..branch'). Raises GitError on conflict."""
        await self._run_git("cherry-pick", "-x", "--allow-empty", commit_range, cwd=cwd)

    async def cherry_pick_abort(self, *, cwd: str | Path) -> None:
        """Abort an in-progress cherry-pick."""
        with contextlib.suppress(GitError):
            await self._run_git("cherry-pick", "--abort", cwd=cwd)

    async def is_ancestor(self, ancestor: str, descendant: str, *, cwd: str | Path) -> bool:
        """Return True if *ancestor* is an ancestor of *descendant*."""
        try:
            await self._run_git("merge-base", "--is-ancestor", ancestor, descendant, cwd=cwd)
            return True
        except GitError:
            return False

    async def rev_parse(self, ref: str, *, cwd: str | Path) -> str:
        """Resolve a ref to its full commit SHA."""
        return await self._run_git("rev-parse", ref, cwd=cwd)

    async def update_ref(self, ref: str, new_value: str, *, cwd: str | Path) -> None:
        """Update a ref (e.g. refs/heads/main) to point at *new_value*."""
        await self._run_git("update-ref", ref, new_value, cwd=cwd)

    async def add_all(self, *, cwd: str | Path) -> None:
        """Stage all changes (including untracked files)."""
        await self._run_git("add", "-A", cwd=cwd)

    async def commit(self, message: str, *, cwd: str | Path, allow_empty: bool = False) -> None:
        """Create a commit.  When *allow_empty* is True, commits even with no changes."""
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        await self._run_git(*args, cwd=cwd)

    async def auto_commit(self, *, cwd: str | Path, message: str = "Tower: auto-commit agent changes") -> bool:
        """Stage + commit any uncommitted changes. Returns True if a commit was created."""
        dirty = await self._is_worktree_dirty(cwd)
        if not dirty:
            return False
        await self.add_all(cwd=cwd)
        await self.commit(message, cwd=cwd)
        return True

    async def stash(self, *, cwd: str | Path) -> bool:
        """Stash uncommitted changes. Returns True if something was stashed."""
        dirty = await self._is_worktree_dirty(cwd)
        if not dirty:
            return False
        await self._run_git("stash", "push", "-u", "-m", "tower-merge-temp", cwd=cwd)
        return True

    async def stash_pop(self, *, cwd: str | Path) -> None:
        """Pop the last stash entry."""
        with contextlib.suppress(GitError):
            await self._run_git("stash", "pop", cwd=cwd)

    async def push(self, branch: str, *, cwd: str | Path, force: bool = False) -> None:
        """Push a branch to origin."""
        args = ["push", "origin", branch]
        if force:
            args = ["push", "--force-with-lease", "origin", branch]
        await self._run_git(*args, cwd=cwd)

    async def get_conflict_files(self, *, cwd: str | Path) -> list[str]:
        """Return list of unmerged (conflicting) file paths after a failed merge."""
        try:
            out = await self._run_git("diff", "--name-only", "--diff-filter=U", cwd=cwd)
            return [f for f in out.splitlines() if f.strip()]
        except GitError:
            return []

    async def get_current_branch(self, *, cwd: str | Path) -> str:
        """Return the current branch name."""
        return await self._run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)

    async def validate_repo(self, repo_path: str) -> bool:
        """Check that a path is a valid git repository."""
        path = Path(repo_path).expanduser().resolve()
        if not path.is_dir():
            return False
        try:
            await self._run_git("rev-parse", "--git-dir", cwd=path)
            return True
        except GitError:
            return False

    async def get_default_branch(self, repo_path: str) -> str:
        """Detect the default branch name (e.g. main or master)."""
        try:
            ref = await self._run_git(
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "--short",
                cwd=repo_path,
            )
            # Returns e.g. "origin/main" — strip the remote prefix
            return ref.split("/", 1)[-1] if "/" in ref else ref
        except GitError:
            # Fallback: check if main exists, else master, else current branch
            try:
                await self._run_git("rev-parse", "--verify", "main", cwd=repo_path)
                return "main"
            except GitError:
                try:
                    await self._run_git("rev-parse", "--verify", "master", cwd=repo_path)
                    return "master"
                except GitError:
                    # Last resort: current branch
                    return await self._run_git(
                        "rev-parse",
                        "--abbrev-ref",
                        "HEAD",
                        cwd=repo_path,
                    )

    async def get_origin_url(self, repo_path: str) -> str | None:
        """Get the remote origin URL, or None if not set."""
        try:
            url = await self._run_git("config", "--get", "remote.origin.url", cwd=repo_path)
            return url or None
        except GitError:
            return None

    async def has_active_worktree(self, repo_path: str) -> bool:
        """Check if any secondary worktree exists under the worktrees dir."""
        worktrees_dir = Path(repo_path) / self._worktrees_dirname
        if not worktrees_dir.exists():
            return False
        return any(worktrees_dir.iterdir())

    async def get_active_worktree_count(self, repo_path: str) -> int:
        """Count existing secondary worktrees for a repo."""
        worktrees_dir = Path(repo_path) / self._worktrees_dirname
        if not worktrees_dir.exists():
            return 0
        return sum(1 for p in worktrees_dir.iterdir() if p.is_dir())

    async def create_worktree(
        self,
        repo_path: str,
        job_id: str,
        base_ref: str,
        branch: str | None = None,
    ) -> tuple[str, str]:
        """Create a secondary worktree and branch for a job.

        Every job always gets its own isolated worktree — the main worktree
        is never used for job execution.

        Args:
            repo_path: Absolute path to the repository root.
            job_id: The job ID (used for worktree directory naming).
            base_ref: The base branch or commit to create the new branch from.
            branch: Explicit branch name, or None to auto-generate.

        Returns:
            Tuple of (worktree_path, branch_name).

        Raises:
            GitError: If git operations fail.
        """
        branch_name = branch or f"tower/{job_id}"
        resolved_base_ref = await self._resolve_ref(repo_path, base_ref)
        return await self._setup_secondary_worktree(repo_path, job_id, resolved_base_ref, branch_name)

    async def _is_worktree_dirty(self, repo_path: str | Path) -> bool:
        """Return True if the working tree has uncommitted changes."""
        try:
            result = await self._run_git("status", "--porcelain", cwd=repo_path)
            return bool(result.strip())
        except GitError:
            return False

    async def _resolve_ref(self, repo_path: str, ref: str) -> str:
        """Resolve a ref, falling back to origin/{ref} if the bare ref doesn't exist locally."""
        try:
            await self._run_git("rev-parse", "--verify", ref, cwd=repo_path)
            return ref
        except GitError:
            remote_ref = f"origin/{ref}"
            try:
                await self._run_git("rev-parse", "--verify", remote_ref, cwd=repo_path)
                log.info("ref_resolved_via_remote", ref=ref, resolved=remote_ref)
                return remote_ref
            except GitError:
                raise GitError(
                    f"Cannot resolve ref '{ref}': not found locally or as '{remote_ref}'",
                    stderr=f"unknown revision: {ref}",
                ) from None

    async def _setup_secondary_worktree(
        self,
        repo_path: str,
        job_id: str,
        base_ref: str,
        branch_name: str,
    ) -> tuple[str, str]:
        """Create a secondary worktree and branch."""
        worktrees_dir = Path(repo_path) / self._worktrees_dirname
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        worktree_path = worktrees_dir / job_id

        # Prune any stale worktree registrations whose directories no longer exist.
        # This is needed after a DB wipe where the same job IDs get reused so that
        # 'git branch -D' can succeed (it refuses to delete branches checked out in
        # a registered worktree, even if the directory is gone from disk).
        with contextlib.suppress(GitError):
            await self._run_git("worktree", "prune", cwd=repo_path)

        # If a worktree is still registered at the target path (directory exists),
        # force-remove it so both the directory and the registration are gone.
        if worktree_path.exists():
            with contextlib.suppress(GitError):
                await self._run_git("worktree", "remove", "--force", str(worktree_path), cwd=repo_path)
                log.info("stale_worktree_removed", repo=repo_path, path=str(worktree_path))

        # Now the branch should be detachable; delete it if it exists.
        with contextlib.suppress(GitError):
            await self._run_git("branch", "-D", branch_name, cwd=repo_path)
            log.info("stale_branch_deleted", repo=repo_path, branch=branch_name)

        try:
            await self._run_git(
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_path),
                base_ref,
                cwd=repo_path,
            )
        except GitError as exc:
            raise GitError(
                f"Failed to create secondary worktree for {job_id} at '{worktree_path}': {exc.stderr}",
                stderr=exc.stderr,
            ) from exc
        log.info(
            "worktree_secondary_created",
            repo=repo_path,
            job_id=job_id,
            worktree=str(worktree_path),
            branch=branch_name,
        )
        return str(worktree_path), branch_name

    async def reattach_worktree(self, repo_path: str, job_id: str, branch: str) -> str:
        """Re-add an existing branch as a secondary worktree after the directory was removed.

        Used when resuming a job whose worktree dir no longer exists but the branch is intact.
        Returns the worktree path string.
        """
        worktrees_dir = Path(repo_path) / self._worktrees_dirname
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        worktree_path = worktrees_dir / job_id

        if worktree_path.exists():
            return str(worktree_path)

        try:
            await self._run_git(
                "worktree",
                "add",
                "--checkout",
                str(worktree_path),
                branch,
                cwd=repo_path,
            )
        except GitError as exc:
            raise GitError(
                f"Failed to reattach worktree for {job_id} on branch '{branch}': {exc.stderr}",
                stderr=exc.stderr,
            ) from exc
        log.info("worktree_reattached", repo=repo_path, job_id=job_id, branch=branch)
        return str(worktree_path)

    async def remove_worktree(self, repo_path: str, worktree_path: str) -> None:
        """Remove a secondary worktree and its branch."""
        wt = Path(worktree_path)
        if not wt.exists():
            return

        # Safety: reject symlinks and paths outside the worktrees directory
        worktrees_dir = (Path(repo_path) / self._worktrees_dirname).resolve()
        resolved_wt = wt.resolve()
        if not str(resolved_wt).startswith(str(worktrees_dir) + "/"):
            log.warning("worktree_path_outside_dir", worktree=worktree_path, expected_parent=str(worktrees_dir))
            return

        # Get branch name before removing
        try:
            branch = await self._run_git(
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
                cwd=worktree_path,
            )
        except GitError:
            branch = None

        try:
            await self._run_git("worktree", "remove", str(worktree_path), "--force", cwd=repo_path)
        except GitError:
            # If git worktree remove fails, force-remove the directory
            if wt.exists():
                shutil.rmtree(wt)
            await self._run_git("worktree", "prune", cwd=repo_path)

        # Clean up the branch
        if branch and branch not in ("main", "master", "HEAD"):
            with contextlib.suppress(GitError):
                await self._run_git("branch", "-D", branch, cwd=repo_path)

        log.info("worktree_removed", repo=repo_path, worktree=worktree_path)

    async def cleanup_worktrees(self, repo_path: str) -> int:
        """Remove all secondary worktrees for a repo. Returns count removed."""
        worktrees_dir = Path(repo_path) / self._worktrees_dirname
        if not worktrees_dir.exists():
            return 0

        removed = 0
        for entry in worktrees_dir.iterdir():
            if entry.is_symlink():
                log.warning("worktree_symlink_skipped", path=str(entry))
                continue
            if entry.is_dir():
                await self.remove_worktree(repo_path, str(entry))
                removed += 1

        # Remove the worktrees directory if empty
        if worktrees_dir.exists() and not any(worktrees_dir.iterdir()):
            worktrees_dir.rmdir()

        return removed

    async def clone_repo(self, url: str, target_dir: str) -> str:
        """Clone a remote repository to target_dir. Returns the clone path."""
        target = Path(target_dir)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                url,
                str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            raise GitError("git executable not found. Ensure Git is installed and available on PATH.") from exc
        _, stderr_bytes = await proc.communicate()
        stderr = stderr_bytes.decode().strip()

        if proc.returncode != 0:
            raise GitError(f"git clone failed: {stderr}", stderr=stderr)

        log.info("repo_cloned", url=url, target=str(target))
        return str(target)

    @staticmethod
    def is_remote_url(source: str) -> bool:
        """Determine if a source string is a remote URL vs local path."""
        return bool(re.match(r"(https?://|git@|ssh://)", source))
