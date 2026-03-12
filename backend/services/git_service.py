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
        use_main: bool = True,
    ) -> tuple[str, str]:
        """Create a worktree and branch for a job.

        Args:
            repo_path: Absolute path to the repository root.
            job_id: The job ID (used for secondary worktree directory naming).
            base_ref: The base branch or commit to create the new branch from.
            branch: Explicit branch name, or None to auto-generate.
            use_main: If True, use the main worktree; otherwise create secondary.

        Returns:
            Tuple of (worktree_path, branch_name).

        Raises:
            GitError: If git operations fail.
        """
        branch_name = branch or f"tower/{job_id}"

        if use_main:
            return await self._setup_main_worktree(repo_path, base_ref, branch_name)
        return await self._setup_secondary_worktree(repo_path, job_id, base_ref, branch_name)

    async def _setup_main_worktree(
        self,
        repo_path: str,
        base_ref: str,
        branch_name: str,
    ) -> tuple[str, str]:
        """Create a branch and check it out in the main worktree."""
        try:
            await self._run_git("checkout", "-B", branch_name, base_ref, cwd=repo_path)
        except GitError as exc:
            raise GitError(
                f"Failed to set up main worktree branch '{branch_name}' from '{base_ref}': {exc.stderr}",
                stderr=exc.stderr,
            ) from exc
        log.info("worktree_main_setup", repo=repo_path, branch=branch_name)
        return repo_path, branch_name

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
    def derive_clone_dir(url: str, repos_base_dir: str) -> str:
        """Derive a local directory path from a remote URL.

        Example: https://github.com/org/repo.git → ~/tower-repos/org/repo

        Raises GitError if the derived path would escape repos_base_dir.
        """
        base = Path(repos_base_dir).expanduser().resolve()
        # Strip protocol and .git suffix
        cleaned = re.sub(r"^(https?://|git@|ssh://)", "", url)
        cleaned = re.sub(r"\.git$", "", cleaned)
        # For git@host:org/repo format
        cleaned = cleaned.replace(":", "/")
        # Take the last two path segments (org/repo)
        parts = cleaned.split("/")
        rel = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        result = (base / rel).resolve()
        # Prevent path traversal — result must stay within base
        if not str(result).startswith(str(base) + "/") and result != base:
            raise GitError(f"Derived clone path escapes repos base directory: {url}")
        return str(result)

    @staticmethod
    def is_remote_url(source: str) -> bool:
        """Determine if a source string is a remote URL vs local path."""
        return bool(re.match(r"(https?://|git@|ssh://)", source))
