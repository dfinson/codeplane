"""Git worktree and branch operations."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import signal
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from backend.models.domain import CodePlaneError
from backend.services.auth.workspace_paths import workspace_target

if TYPE_CHECKING:
    from backend.config import CPLConfig

log = structlog.get_logger()


class GitError(CodePlaneError):
    """Raised when a git subprocess fails."""

    def __init__(self, message: str, stderr: str = "", *, returncode: int | None = None) -> None:
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GitRecoveryTarget:
    path: str
    relative_path: str
    blob: str | None


@dataclass(frozen=True, slots=True)
class GitRecoveryWitness:
    workspace: str
    commit: str
    targets: tuple[GitRecoveryTarget, ...]
    reference: str = "HEAD"


class GitService:
    """Manages git worktrees, branches, and workspace isolation."""

    def __init__(self, config: CPLConfig) -> None:
        self._worktrees_dirname = config.runtime.worktrees_dirname

    @staticmethod
    def strip_url_credentials(url: str) -> str:
        """Remove embedded credentials from a git remote URL."""
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        if parsed.username or parsed.password:
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            cleaned = parsed._replace(netloc=host)
            return urlunparse(cleaned)
        return url

    async def _run_git(
        self,
        *args: str,
        cwd: str | Path,
        env_overrides: dict[str, str] | None = None,
        input_bytes: bytes | None = None,
    ) -> str:
        """Run a git command and return stdout. Raises GitError on failure."""
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env_overrides or {})}
        cwd_path = Path(cwd)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
                env=env,
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError as exc:
            missing_cwd = exc.filename is not None and Path(exc.filename) == cwd_path
            if not missing_cwd and exc.filename is None and not cwd_path.exists():
                missing_cwd = True
            if missing_cwd:
                raise GitError(f"git working directory does not exist: {cwd_path}") from exc
            raise GitError("git executable not found. Ensure Git is installed and available on PATH.") from exc
        except OSError as exc:
            raise GitError(f"git failed to start: {exc}") from exc
        communication = asyncio.create_task(
            proc.communicate(input_bytes) if input_bytes is not None else proc.communicate()
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.shield(communication)
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                elif proc.returncode is None:
                    proc.kill()
            while not communication.done():
                try:
                    await asyncio.shield(communication)
                except asyncio.CancelledError:
                    continue
            communication.result()
            raise
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed (exit {proc.returncode}): {stderr}",
                stderr=stderr,
                returncode=proc.returncode,
            )
        return stdout

    async def run_git(self, *args: str, cwd: str | Path) -> str:
        """Public wrapper for running arbitrary git commands. Raises GitError on failure."""
        return await self._run_git(*args, cwd=cwd)

    async def diff(self, diff_spec: str, *, cwd: str | Path) -> str:
        """Run `git diff <diff_spec>` and return raw output."""
        return await self._run_git("diff", diff_spec, cwd=cwd)

    async def diff_range(self, ref1: str, ref2: str, *, cwd: str | Path) -> str:
        """Run `git diff <ref1> <ref2>` comparing two commits (no working tree)."""
        return await self._run_git("diff", ref1, ref2, cwd=cwd)

    async def diff_numstat(self, ref1: str, ref2: str, *, cwd: str | Path) -> tuple[int, int]:
        """Return (additions, deletions) between two commits via --numstat."""
        raw = await self._run_git("diff", "--numstat", ref1, ref2, cwd=cwd)
        additions = 0
        deletions = 0
        for line in raw.strip().splitlines():
            parts = line.split("\t", 2)
            if len(parts) >= 2:
                # Binary files show "-" instead of numbers
                with contextlib.suppress(ValueError):
                    additions += int(parts[0])
                with contextlib.suppress(ValueError):
                    deletions += int(parts[1])
        return additions, deletions

    async def merge_base(self, ref1: str, ref2: str, *, cwd: str | Path) -> str:
        """Return the merge-base commit between two refs."""
        return (await self._run_git("merge-base", ref1, ref2, cwd=cwd)).strip()

    async def is_merge_in_progress(self, *, cwd: str | Path) -> bool:
        """Return True if a merge is currently in progress in the worktree.

        Detects the presence of MERGE_HEAD, which git sets after a merge has
        been started but the resulting commit has not yet been created.  During
        this window the working tree contains the merged-in content from the
        other branch, which would pollute a working-tree diff with unrelated
        changes.
        """
        try:
            await self._run_git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=cwd)
            return True
        except GitError:
            return False

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
        args = ["merge", "--no-edit"]
        if message:
            args.extend(["-m", message])
        args.append(branch)
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

    async def recovery_witness(
        self,
        *,
        cwd: str | Path,
        paths: tuple[str, ...],
        deadline: float,
        max_input_bytes: int,
    ) -> GitRecoveryWitness | None:
        """Prove current regular-file bytes or new-target absence without writing Git state."""

        def unavailable(reason: str) -> GitRecoveryWitness | None:
            log.warning("git_recovery_unavailable", reason=reason, target_count=len(paths))
            return None

        def file_version(value: os.stat_result) -> tuple[int, ...]:
            return (value.st_ino, value.st_dev, value.st_size, value.st_mtime_ns, value.st_ctime_ns)

        environment = {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_LITERAL_PATHSPECS": "1",
        }
        try:
            remaining_bytes = max_input_bytes - sum(len(path.encode("utf-8")) for path in paths)
            if not paths or remaining_bytes < 0:
                return unavailable("missing_or_oversized_target_set")
            if os.name != "nt" and any("\\" in path for path in paths):
                return unavailable("ambiguous_posix_target")
            async with asyncio.timeout_at(deadline):
                supplied_root = Path(cwd).absolute()
                root = supplied_root.resolve(strict=True)
                try:
                    await self._run_git(
                        "config",
                        "--get-regexp",
                        r"^(remote\..*\.promisor|extensions\.partialclone)$",
                        cwd=root,
                        env_overrides=environment,
                    )
                except GitError as exc:
                    if exc.returncode != 1:
                        raise
                else:
                    return unavailable("partial_repository")
                head = await self._run_git(
                    "rev-parse", "--verify", "HEAD^{commit}", cwd=root, env_overrides=environment
                )
                targets: list[GitRecoveryTarget] = []
                versions: list[tuple[str, Path, os.stat_result | None]] = []
                for declared in sorted(set(paths)):
                    lexical = Path(declared.replace("\\", "/"))
                    if not lexical.is_absolute():
                        lexical = supplied_root / lexical
                    target = workspace_target(str(root), declared)
                    relative = target.relative_to(root).as_posix()
                    if (
                        not lexical.is_relative_to(supplied_root)
                        or ".git" in (part.lower() for part in target.relative_to(root).parts)
                        or ":" in relative
                        or not lexical.parent.is_dir()
                    ):
                        return unavailable("opaque_or_administrative_target")
                    cursor = lexical
                    while cursor != supplied_root:
                        if cursor.is_symlink():
                            return unavailable("symlink_target")
                        cursor = cursor.parent
                    owner = await self._run_git(
                        "rev-parse", "--show-toplevel", cwd=target.parent, env_overrides=environment
                    )
                    if Path(owner).resolve() != root:
                        return unavailable("different_repository")
                    entry = await self._run_git(
                        "ls-tree", "-z", "--full-tree", head, "--", relative, cwd=root, env_overrides=environment
                    )
                    try:
                        observed = target.lstat()
                    except FileNotFoundError:
                        observed = None
                    if observed is None:
                        if entry:
                            return unavailable("missing_previously_retained_target")
                        try:
                            await self._run_git(
                                "check-ignore",
                                "--",
                                relative,
                                cwd=root,
                                env_overrides={**environment, "GIT_LITERAL_PATHSPECS": "0"},
                            )
                        except GitError as exc:
                            if exc.returncode != 1:
                                raise
                        else:
                            return unavailable("ignored_new_target")
                        targets.append(GitRecoveryTarget(declared, relative, None))
                        versions.append((declared, target, None))
                        continue
                    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or not entry:
                        return unavailable("current_bytes_not_retained")
                    if observed.st_size > remaining_bytes:
                        return unavailable("current_byte_bound_exceeded")
                    records = entry.split("\0")
                    if len(records) != 2 or records[1]:
                        return unavailable("ambiguous_retained_target")
                    description, retained_path = records[0].split("\t", 1)
                    retained_mode, kind, blob = description.split()
                    if retained_path != relative or kind != "blob" or retained_mode not in {"100644", "100755"}:
                        return unavailable("nonregular_retained_target")
                    flags = (
                        os.O_RDONLY
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_NONBLOCK", 0)
                        | getattr(os, "O_BINARY", 0)
                    )
                    with os.fdopen(os.open(target, flags), "rb") as stream:
                        opened = os.fstat(stream.fileno())
                        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                            observed.st_dev,
                            observed.st_ino,
                        ):
                            return unavailable("target_changed_during_inspection")
                        content = stream.read(remaining_bytes + 1)
                    if len(content) > remaining_bytes:
                        return unavailable("current_byte_bound_exceeded")
                    remaining_bytes -= len(content)
                    current_blob = await self._run_git(
                        "hash-object",
                        "--no-filters",
                        "--stdin",
                        cwd=root,
                        env_overrides=environment,
                        input_bytes=content,
                    )
                    after = target.lstat()
                    if file_version(after) != file_version(opened):
                        return unavailable("target_changed_during_inspection")
                    if current_blob != blob:
                        return unavailable("current_bytes_not_retained")
                    await self._run_git("cat-file", "-e", blob, cwd=root, env_overrides=environment)
                    targets.append(GitRecoveryTarget(declared, relative, blob))
                    versions.append((declared, target, after))
                if (
                    await self._run_git("rev-parse", "--verify", "HEAD^{commit}", cwd=root, env_overrides=environment)
                    != head
                ):
                    return unavailable("retained_reference_changed")
                for declared, target, before in versions:
                    if workspace_target(str(root), declared) != target:
                        return unavailable("target_changed_during_inspection")
                    try:
                        final = target.lstat()
                    except FileNotFoundError:
                        final = None
                    if (None if final is None else file_version(final)) != (
                        None if before is None else file_version(before)
                    ):
                        return unavailable("target_changed_during_inspection")
                return GitRecoveryWitness(str(root), head, tuple(targets))
        except (GitError, OSError, ValueError, TimeoutError) as exc:
            log.warning("git_recovery_unavailable", reason=type(exc).__name__, detail=str(exc), target_count=len(paths))
            return None

    async def tag(self, name: str, *, message: str = "", cwd: str | Path, force: bool = False) -> None:
        """Create an annotated git tag."""
        force_flag = ["-f"] if force else []
        if message:
            await self._run_git("tag", "-a", *force_flag, name, "-m", message, cwd=cwd)
        else:
            await self._run_git("tag", *force_flag, name, cwd=cwd)

    async def reset_hard(self, sha: str, *, cwd: str | Path) -> None:
        await self._run_git("reset", "--hard", sha, cwd=cwd)

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

    async def auto_commit(self, *, cwd: str | Path, message: str = "CodePlane: auto-commit agent changes") -> bool:
        """Stage + commit any uncommitted changes. Returns True if a commit was created."""
        dirty = await self._is_worktree_dirty(cwd)
        if not dirty:
            return False
        await self.add_all(cwd=cwd)
        await self.commit(message, cwd=cwd)
        return True

    async def capture_worktree_commit(self, *, cwd: str | Path, snapshot_ref: str) -> str:
        """Retain current code without changing the user's index, HEAD or files."""
        import tempfile

        if not snapshot_ref.startswith("refs/codeplane/recipe-inputs/"):
            raise GitError("Recipe snapshots require an internal reference namespace")
        await self._run_git("check-ref-format", snapshot_ref, cwd=cwd)
        existing = await self._read_exact_ref(snapshot_ref, cwd=cwd)
        if existing:
            return existing
        head = await self.rev_parse("HEAD", cwd=cwd)
        if await self._run_git("status", "--porcelain", cwd=cwd):
            with tempfile.TemporaryDirectory(prefix="codeplane-recipe-index-") as temporary:
                environment = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
                await self._run_git("read-tree", head, cwd=cwd, env_overrides=environment)
                await self._run_git("add", "-A", cwd=cwd, env_overrides=environment)
                tree = await self._run_git("write-tree", cwd=cwd, env_overrides=environment)
                captured = await self._run_git(
                    "-c",
                    "user.name=CodePlane",
                    "-c",
                    "user.email=codeplane@localhost",
                    "commit-tree",
                    tree,
                    "-p",
                    head,
                    "-m",
                    "CodePlane recipe input snapshot",
                    cwd=cwd,
                )
        else:
            captured = head
        try:
            await self._run_git("update-ref", snapshot_ref, captured, "0" * len(captured), cwd=cwd)
        except GitError:
            winner = await self._read_exact_ref(snapshot_ref, cwd=cwd)
            if not winner:
                raise
            if await self.rev_parse(f"{winner}^{{tree}}", cwd=cwd) != await self.rev_parse(
                f"{captured}^{{tree}}", cwd=cwd
            ):
                raise GitError("A concurrent input snapshot captured different code") from None
            return winner
        return captured

    async def _read_exact_ref(self, ref: str, *, cwd: str | Path) -> str | None:
        listing = await self._run_git("for-each-ref", "--format=%(refname)\t%(objectname)", ref, cwd=cwd)
        for line in listing.splitlines():
            name, object_id = line.split("\t", 1)
            if name == ref:
                return object_id
        return None

    def recipe_worktree_path(self, repo_path: str, name: str) -> str:
        self._validate_worktree_name(name)
        parent = (Path(repo_path) / self._worktrees_dirname).resolve()
        target = (parent / name).resolve()
        if target.parent != parent:
            raise GitError("Recipe workspace escapes the configured worktree directory")
        return str(target)

    async def stash(self, *, cwd: str | Path) -> bool:
        """Stash uncommitted changes. Returns True if something was stashed."""
        dirty = await self._is_worktree_dirty(cwd)
        if not dirty:
            return False
        await self._run_git("stash", "push", "-u", "-m", "cpl-merge-temp", cwd=cwd)
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
        # 1. Try the symbolic ref (most reliable when remote is configured)
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
            log.debug("symbolic_ref_unavailable", repo_path=repo_path)

        # 2. Probe well-known branch names
        for candidate in ("main", "master"):
            try:
                await self._run_git("rev-parse", "--verify", candidate, cwd=repo_path)
                return candidate
            except GitError:
                log.debug("branch_candidate_not_found", candidate=candidate, repo_path=repo_path)
                continue

        # 3. Last resort: current branch
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

    def has_active_worktree(self, repo_path: str) -> bool:
        """Check if any secondary worktree exists under the worktrees dir."""
        worktrees_dir = Path(repo_path) / self._worktrees_dirname
        if not worktrees_dir.exists():
            return False
        return any(worktrees_dir.iterdir())

    def get_active_worktree_count(self, repo_path: str) -> int:
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
        self._validate_worktree_name(job_id)
        branch_name = branch or f"cpl/{job_id}"
        return await self._setup_secondary_worktree(repo_path, job_id, base_ref, branch_name)

    @staticmethod
    def _validate_worktree_name(name: str) -> None:
        from pathlib import PureWindowsPath

        if (
            not name
            or name in (".", "..")
            or "/" in name
            or "\\" in name
            or PureWindowsPath(name).drive
            or "\x00" in name
            or name.rstrip(" .") != name
        ):
            raise GitError("Worktree name must be a single directory name", stderr="invalid worktree name")

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
        self._validate_worktree_name(job_id)
        worktrees_dir = Path(repo_path) / self._worktrees_dirname
        worktree_path = worktrees_dir / job_id
        if worktree_path.exists() or worktree_path.is_symlink():
            raise GitError(
                f"Worktree destination already exists: {worktree_path}",
                stderr="existing workspace preserved",
            )
        if worktree_path.resolve().parent != worktrees_dir.resolve():
            raise GitError("Worktree destination escapes configured root", stderr="invalid worktree destination")
        resolved_base_ref = await self._resolve_ref(repo_path, base_ref)
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        try:
            await self._run_git(
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_path),
                resolved_base_ref,
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
        self._validate_worktree_name(job_id)
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
        if not str(resolved_wt).startswith(str(worktrees_dir) + os.sep):
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
            if entry.is_symlink() or entry.is_junction():
                log.warning("worktree_symlink_skipped", path=str(entry))
                continue
            if entry.is_dir():
                await self.remove_worktree(repo_path, str(entry))
                removed += 1

        # Remove the worktrees directory if empty
        if worktrees_dir.exists() and not any(worktrees_dir.iterdir()):
            worktrees_dir.rmdir()

        return removed

    async def init_repo(self, path: str) -> str:
        """Initialise a new git repository with an initial empty commit.

        Returns the resolved path of the new repository.
        """
        target = Path(path).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)

        await self._run_git("init", cwd=target)
        await self._run_git("commit", "--allow-empty", "-m", "Initial commit", cwd=target)

        log.info("repo_initialised", path=str(target))
        return str(target)

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
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            raise GitError(f"git clone failed: {stderr}", stderr=stderr)

        log.info("repo_cloned", url=url, target=str(target))
        return str(target)

    @staticmethod
    def is_remote_url(source: str) -> bool:
        """Determine if a source string is a remote URL vs local path."""
        return bool(re.match(r"(https?://|git://|file://|git@|ssh://)", source))

    async def list_branches(self, repo_path: str) -> set[str]:
        """Return a set of all branch names (local + remote, without remote prefix)."""
        branches: set[str] = set()
        try:
            local = await self._run_git("branch", "--format=%(refname:short)", cwd=repo_path)
            for line in local.splitlines():
                b = line.strip()
                if b:
                    branches.add(b)
        except GitError:
            log.debug("list_local_branches_failed", repo_path=repo_path, exc_info=True)
        try:
            remote = await self._run_git("branch", "-r", "--format=%(refname:short)", cwd=repo_path)
            for line in remote.splitlines():
                b = line.strip()
                if b:
                    # Strip "origin/" prefix
                    if "/" in b:
                        branches.add(b.split("/", 1)[1])
                    branches.add(b)
        except GitError:
            log.debug("list_remote_branches_failed", repo_path=repo_path, exc_info=True)
        return branches

    def list_worktree_names(self, repo_path: str) -> set[str]:
        """Return a set of existing worktree directory names."""
        worktrees_dir = Path(repo_path) / self._worktrees_dirname
        if not worktrees_dir.exists():
            return set()
        return {p.name for p in worktrees_dir.iterdir() if p.is_dir()}
