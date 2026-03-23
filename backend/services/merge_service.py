"""Post-completion merge-back orchestration.

Attempts to merge a job's branch back into the base branch using an
escalation strategy:

1. Fast-forward merge (cleanest — no merge commit)
2. Regular merge (if base has diverged but no conflicts)
3. Fallback to PR creation (if conflicts are detected)
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from backend.models.domain import Resolution
from backend.models.events import DomainEvent, DomainEventKind
from backend.services.git_service import GitError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CompletionConfig
    from backend.services.diff_service import DiffService
    from backend.services.event_bus import EventBus
    from backend.services.git_service import GitService
    from backend.services.platform_adapter import PlatformRegistry

log = structlog.get_logger()

_REF_PATTERN = re.compile(r"^[a-zA-Z0-9/_.-]+$")
_PR_TITLE_MAX_PROMPT_LEN = 80
_CHERRY_PICK_ALREADY_APPLIED_PATTERNS = (
    "empty commit set passed",
    "the previous cherry-pick is now empty",
    "previous cherry-pick is now empty",
    "patch contents already upstream",
    "nothing to commit, working tree clean",
)


def _classify_cherry_pick_failure(exc: GitError) -> str:
    """Map cherry-pick failures without conflict markers to a user-facing error."""
    combined_message = "\n".join(part for part in (str(exc), exc.stderr) if part).lower()
    if any(pattern in combined_message for pattern in _CHERRY_PICK_ALREADY_APPLIED_PATTERNS):
        return (
            "Cherry-pick stopped because one or more branch commits are already present"
            " on the base branch; rebase the branch or create a PR"
        )
    return "Cherry-pick failed without conflict markers; check git configuration or hooks"


class MergeStatus(StrEnum):
    """Outcome status for a merge-back attempt."""

    merged = "merged"
    conflict = "conflict"
    pr_created = "pr_created"
    skipped = "skipped"
    error = "error"


@dataclass
class MergeResult:
    """Outcome of a merge-back attempt."""

    status: MergeStatus
    strategy: str | None = None  # ff_only | merge | pr
    pr_url: str | None = None
    conflict_files: list[str] | None = None
    error: str | None = None


class MergeService:
    """Orchestrates merging a job's branch back into its base branch."""

    def __init__(
        self,
        git_service: GitService,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: CompletionConfig,
        platform_registry: PlatformRegistry | None = None,
        diff_service: DiffService | None = None,
    ) -> None:
        self._git = git_service
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._config = config
        self._platform_registry = platform_registry
        self._diff_service = diff_service
        # Per-repo lock to prevent concurrent merges from corrupting the
        # main worktree state (checkout / merge / stash interleaving).
        self._repo_locks: dict[str, asyncio.Lock] = {}

    async def try_merge_back(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str | None,
        base_ref: str,
        prompt: str,
    ) -> MergeResult:
        """Attempt to merge the job's branch into base_ref.

        Escalation: fast-forward → merge → PR fallback.
        """
        if not branch:
            log.info("merge_skipped_no_branch", job_id=job_id)
            return MergeResult(status=MergeStatus.skipped, error="No branch")

        if not _REF_PATTERN.match(branch) or not _REF_PATTERN.match(base_ref):
            log.warning("merge_skipped_invalid_refs", job_id=job_id, branch=branch, base_ref=base_ref)
            return MergeResult(status=MergeStatus.skipped, error="Invalid branch or base_ref")

        strategy = self._config.strategy

        if strategy == "pr_only":
            return await self._create_pr(job_id, repo_path, worktree_path, branch, base_ref, prompt)

        # auto_merge: try ff → merge → pr fallback
        return await self._auto_merge(job_id, repo_path, worktree_path, branch, base_ref, prompt)

    async def _auto_merge(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
        base_ref: str,
        prompt: str,
    ) -> MergeResult:
        """Try fast-forward, then regular merge, then fall back to PR."""

        # Auto-commit any uncommitted changes in the job's worktree so they
        # are reachable via the branch ref during the merge.
        commit_cwd = worktree_path or repo_path
        try:
            committed = await self._git.auto_commit(
                cwd=commit_cwd,
                message=f"CodePlane: agent changes for {job_id}",
            )
            if committed:
                log.info("merge_auto_committed", job_id=job_id, cwd=commit_cwd)
        except GitError:
            log.warning("merge_auto_commit_failed", job_id=job_id, exc_info=True)

        # --- Step 1: Try fast-forward via ref update (no checkout needed) ---
        try:
            ff_result = await self._try_ff_via_ref(job_id, repo_path, branch, base_ref)
            if ff_result is not None:
                return ff_result
        except GitError:
            log.debug("merge_ff_ref_failed", job_id=job_id, exc_info=True)

        # --- Step 2: Full merge requires the main worktree. Acquire lock. ---
        lock = self._repo_locks.setdefault(repo_path, asyncio.Lock())
        async with lock:
            return await self._merge_in_worktree(
                job_id,
                repo_path,
                worktree_path,
                branch,
                base_ref,
                prompt,
            )

    async def _try_ff_via_ref(
        self,
        job_id: str,
        repo_path: str,
        branch: str,
        base_ref: str,
    ) -> MergeResult | None:
        """Attempt a fast-forward by updating the base ref directly.

        Returns a MergeResult on success, or None if FF is not possible.
        """
        # Check if base_ref is an ancestor of branch (i.e. FF-able)
        if not await self._git.is_ancestor(base_ref, branch, cwd=repo_path):
            return None

        branch_sha = await self._git.rev_parse(branch, cwd=repo_path)
        await self._git.update_ref(f"refs/heads/{base_ref}", branch_sha, cwd=repo_path)

        # If the working tree is on the base branch, sync it to the new HEAD
        current = await self._git.get_current_branch(cwd=repo_path)
        if current == base_ref:
            await self._git._run_git("reset", "--hard", "HEAD", cwd=repo_path)  # noqa: SLF001

        log.info("merge_ff_ref_succeeded", job_id=job_id, branch=branch, base_ref=base_ref)

        # Persist before publishing so the frontend never sees an event
        # that the DB hasn't committed.
        await self._update_merge_status(job_id, Resolution.merged)
        await self._publish_merge_completed(job_id, branch, base_ref, "ff_only")
        await self._post_merge_cleanup(job_id, repo_path, None, branch)
        return MergeResult(status=MergeStatus.merged, strategy="ff_only")

    @contextlib.asynccontextmanager
    async def _preserved_worktree(self, repo_path: str, job_id: str, log_prefix: str = "merge") -> AsyncIterator[None]:
        """Save and restore the main worktree's branch + stash state."""
        original_branch: str | None = None
        main_stashed = False

        with contextlib.suppress(GitError):
            original_branch = await self._git.get_current_branch(cwd=repo_path)

        try:
            main_stashed = await self._git.stash(cwd=repo_path)
        except GitError:
            log.warning(f"{log_prefix}_main_stash_failed", job_id=job_id, exc_info=True)

        try:
            yield
        finally:
            if original_branch:
                try:
                    await self._git.checkout(original_branch, cwd=repo_path)
                except GitError:
                    log.warning(f"{log_prefix}_restore_branch_failed", job_id=job_id, exc_info=True)
            if main_stashed:
                try:
                    await self._git.stash_pop(cwd=repo_path)
                except GitError:
                    log.warning(f"{log_prefix}_main_stash_pop_failed", job_id=job_id, exc_info=True)

    async def _checkout_and_merge(
        self,
        job_id: str,
        repo_path: str,
        branch: str,
        base_ref: str,
    ) -> tuple[bool | None, list[str], str | None]:
        """Checkout base_ref, attempt merge, and classify the outcome.

        Returns ``(merge_ok, conflict_files, error)`` where:

        * ``merge_ok is True`` means the merge succeeded.
        * ``merge_ok is False`` means git reported a real merge conflict and
          ``conflict_files`` lists the unmerged files.
        * ``merge_ok is None`` means git merge failed for a non-conflict
          reason (hooks, identity, locks, etc.) and ``error`` describes it.
        """
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
            await self._git.merge(
                branch,
                cwd=repo_path,
                message=f"Merge {branch} (CodePlane {job_id})",
            )
            return True, [], None
        except GitError as exc:
            error = str(exc)

        # Merge failed — abort, then classify whether it was a real conflict.
        await self._git.merge_abort(cwd=repo_path)
        conflict_files = await self._get_conflict_file_list(repo_path, branch, base_ref)

        try:
            await self._git.checkout(base_ref, cwd=repo_path)
        except GitError:
            log.warning("checkout_base_ref_failed", job_id=job_id, exc_info=True)

        if conflict_files is not None:
            log.info("merge_conflict_detected", job_id=job_id, branch=branch, conflict_files=conflict_files)
            return False, conflict_files, None

        log.warning("merge_failed_without_conflicts", job_id=job_id, branch=branch, error=error)
        return None, [], error

    async def _merge_in_worktree(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
        base_ref: str,
        prompt: str,
    ) -> MergeResult:
        """Merge using checkout in the main worktree (lock must be held).

        Saves and restores the original branch + stash to avoid corrupting
        any other job that may be using the main worktree.
        """
        async with self._preserved_worktree(repo_path, job_id, "merge"):
            result = await self._do_merge_steps(
                job_id,
                repo_path,
                worktree_path,
                branch,
                base_ref,
                prompt,
            )
        return result

    async def _do_merge_steps(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
        base_ref: str,
        prompt: str,
    ) -> MergeResult:
        """Checkout + merge in the main worktree (caller handles stash/restore)."""
        merge_ok, conflict_files, error = await self._checkout_and_merge(
            job_id,
            repo_path,
            branch,
            base_ref,
        )

        if merge_ok:
            log.info("merge_succeeded", job_id=job_id, branch=branch, base_ref=base_ref)
            await self._update_merge_status(job_id, Resolution.merged)
            await self._publish_merge_completed(job_id, branch, base_ref, "merge")
            await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
            return MergeResult(status=MergeStatus.merged, strategy="merge")

        if merge_ok is None:
            await self._update_merge_status(job_id, "not_merged")
            return MergeResult(status=MergeStatus.error, error=error or "Merge failed without conflict markers")

        await self._update_merge_status(job_id, Resolution.conflict)
        await self._publish_merge_conflict(
            job_id,
            branch,
            base_ref,
            conflict_files,
            fallback="none",
        )
        return MergeResult(status=MergeStatus.conflict, conflict_files=conflict_files)

    async def _get_conflict_file_list(self, repo_path: str, branch: str, base_ref: str) -> list[str] | None:
        """Probe for conflicting files using a no-commit merge that never creates a commit.

        Uses ``--no-commit --no-ff`` so git identity is never required, making
        the probe robust against misconfigured environments and git hooks.

        Returns:
            - ``None`` if the probe merge succeeded (no real conflicts exist).
            - A list of conflicting file paths if the merge produced actual
              conflict markers.  The list is always non-empty in this case.
            - ``None`` (not ``[]``) when the merge failed for a non-conflict
              reason (no conflict markers found), so callers don't misclassify
              infrastructure errors as merge conflicts.
        """
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
        except GitError:
            return []

        try:
            # --no-commit: never create a commit (no git identity required).
            # --no-ff: force a real merge attempt even when FF would suffice.
            await self._git._run_git("merge", "--no-commit", "--no-ff", branch, cwd=repo_path)  # noqa: SLF001
            # Probe succeeded — clean up the staged-but-uncommitted merge.
            await self._git.merge_abort(cwd=repo_path)
            return None
        except GitError:
            files = await self._git.get_conflict_files(cwd=repo_path)
            await self._git.merge_abort(cwd=repo_path)
            # If git failed but left no conflict markers the failure was caused
            # by something other than a real merge conflict (e.g. a commit hook,
            # missing identity, or a transient lock).  Treat as "no conflict".
            return files if files else None

    async def _create_pr(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
        base_ref: str,
        prompt: str,
    ) -> MergeResult:
        """Push branch and create a PR via platform adapter."""
        cwd = worktree_path or repo_path

        # Push branch to origin first
        if self._config.auto_push:
            try:
                await self._git.push(branch, cwd=cwd)
                log.info("branch_pushed", job_id=job_id, branch=branch)
            except GitError:
                log.warning("branch_push_failed", job_id=job_id, exc_info=True)
                # Continue — agent may have already pushed

        if self._platform_registry is None:
            log.info("pr_creation_skipped_no_registry", job_id=job_id)
            await self._update_merge_status(job_id, "not_merged")
            return MergeResult(status=MergeStatus.skipped, error="No platform registry")

        adapter = await self._platform_registry.get_adapter(repo_path)
        pr_result = await adapter.create_pr(
            cwd=cwd,
            head=branch,
            base=base_ref,
            title=f"[CodePlane] {prompt[:_PR_TITLE_MAX_PROMPT_LEN]}",
            body=f"Automated PR created by CodePlane for job `{job_id}`.",
        )

        if pr_result.ok:
            log.info("pr_created", job_id=job_id, pr_url=pr_result.url, platform=adapter.name)
            await self._update_merge_status(job_id, Resolution.pr_created, pr_url=pr_result.url)
            return MergeResult(status=MergeStatus.pr_created, strategy="pr", pr_url=pr_result.url)

        log.warning("pr_creation_failed", job_id=job_id, platform=adapter.name, error=pr_result.error)
        await self._update_merge_status(job_id, "not_merged")
        return MergeResult(status=MergeStatus.error, error=pr_result.error or "PR creation failed")

    async def _post_merge_cleanup(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
    ) -> None:
        """Clean up worktree and branch after a successful merge."""
        if self._config.cleanup_worktree and worktree_path:
            try:
                await self._git.remove_worktree(repo_path, worktree_path)
                log.info("worktree_cleaned_after_merge", job_id=job_id, worktree=worktree_path)
            except (GitError, OSError):
                log.warning("worktree_cleanup_failed", job_id=job_id, exc_info=True)

        if self._config.delete_branch_after_merge:
            try:
                with contextlib.suppress(GitError):
                    await self._git._run_git("branch", "-d", branch, cwd=repo_path)  # noqa: SLF001
            except GitError:
                log.warning("branch_cleanup_failed", job_id=job_id, exc_info=True)

    _MERGE_STATUS_MAX_ATTEMPTS = 3
    _MERGE_STATUS_RETRY_DELAY_S = 0.05

    async def _update_merge_status(
        self,
        job_id: str,
        merge_status: str,
        pr_url: str | None = None,
    ) -> None:
        """Persist merge status to the database with retry on SQLite lock.

        This must succeed for the UI to stay consistent — a silently-dropped
        update leaves the card showing a stale state.  Retry on transient
        SQLite lock errors (same strategy as event persistence).
        """
        from sqlalchemy.exc import OperationalError

        from backend.persistence.job_repo import JobRepository

        for attempt in range(self._MERGE_STATUS_MAX_ATTEMPTS):
            try:
                async with self._session_factory() as session:
                    repo = JobRepository(session)
                    await repo.update_merge_status(job_id, merge_status, pr_url=pr_url)
                    await session.commit()
                    return
            except OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempt == self._MERGE_STATUS_MAX_ATTEMPTS - 1:
                    log.error("merge_status_update_failed", job_id=job_id, merge_status=merge_status, exc_info=True)
                    raise
                log.warning(
                    "merge_status_retrying_after_lock",
                    job_id=job_id,
                    attempt=attempt + 1,
                )
                await asyncio.sleep(self._MERGE_STATUS_RETRY_DELAY_S * (attempt + 1))
            except SQLAlchemyError:
                log.error("merge_status_update_failed", job_id=job_id, merge_status=merge_status, exc_info=True)
                raise

    async def _publish_merge_completed(
        self,
        job_id: str,
        branch: str,
        base_ref: str,
        strategy: str,
    ) -> None:
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.merge_completed,
                payload={
                    "branch": branch,
                    "base_ref": base_ref,
                    "strategy": strategy,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        )

    async def _publish_merge_conflict(
        self,
        job_id: str,
        branch: str,
        base_ref: str,
        conflict_files: list[str],
        fallback: str,
        pr_url: str | None = None,
    ) -> None:
        await self._event_bus.publish(
            DomainEvent(
                event_id=DomainEvent.make_event_id(),
                job_id=job_id,
                timestamp=datetime.now(UTC),
                kind=DomainEventKind.merge_conflict,
                payload={
                    "branch": branch,
                    "base_ref": base_ref,
                    "conflict_files": conflict_files,
                    "fallback": fallback,
                    "pr_url": pr_url,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        )

    # ------------------------------------------------------------------
    # Operator-initiated resolution
    # ------------------------------------------------------------------

    async def resolve_job(
        self,
        job_id: str,
        action: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str | None,
        base_ref: str,
        prompt: str,
    ) -> MergeResult:
        """Operator-initiated job resolution.

        action: "merge" | "smart_merge" | "create_pr" | "discard"
        """
        # Capture a final diff snapshot before any resolution that removes the
        # worktree.  This guarantees that archived jobs always have a viewable
        # diff even after the branch and worktree have been deleted.
        await self._preserve_diff_snapshot(job_id, worktree_path, base_ref)

        if action == "discard":
            return await self._discard(job_id, repo_path, worktree_path, branch)

        if not branch:
            return MergeResult(status=MergeStatus.error, error="No branch to resolve")

        if not _REF_PATTERN.match(branch) or not _REF_PATTERN.match(base_ref):
            return MergeResult(status=MergeStatus.error, error="Invalid branch or base_ref")

        if action == "create_pr":
            result = await self._create_pr(job_id, repo_path, worktree_path, branch, base_ref, prompt)
            if result.status == MergeStatus.pr_created:
                await self._cleanup_worktree_only(job_id, repo_path, worktree_path)
            return result

        if action == "merge":
            return await self._operator_merge(job_id, repo_path, worktree_path, branch, base_ref, prompt)

        if action == "smart_merge":
            return await self._operator_smart_merge(job_id, repo_path, worktree_path, branch, base_ref)

        return MergeResult(status=MergeStatus.error, error=f"Unknown action: {action}")

    async def _preserve_diff_snapshot(
        self,
        job_id: str,
        worktree_path: str | None,
        base_ref: str,
    ) -> None:
        """Publish a final diff_updated event before the worktree is removed.

        This ensures that archived jobs retain a viewable diff snapshot even
        after the branch has been deleted and the worktree cleaned up.
        The event is published regardless of whether an earlier snapshot
        already exists — the diff endpoint reads the *last* event, so this
        becomes the canonical preserved diff for the job.
        """
        if self._diff_service is None or not worktree_path or not base_ref:
            return
        try:
            await self._diff_service.finalize(job_id, worktree_path, base_ref)
            log.info("diff_snapshot_preserved", job_id=job_id, worktree=worktree_path)
        except (GitError, OSError, ValueError):
            log.warning("diff_snapshot_failed", job_id=job_id, exc_info=True)

    async def _operator_merge(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
        base_ref: str,
        prompt: str,
    ) -> MergeResult:
        """Operator-initiated merge: FF → merge → conflict (NO PR fallback)."""

        # Auto-commit any uncommitted changes in the job's worktree
        commit_cwd = worktree_path or repo_path
        try:
            committed = await self._git.auto_commit(
                cwd=commit_cwd,
                message=f"CodePlane: agent changes for {job_id}",
            )
            if committed:
                log.info("resolve_auto_committed", job_id=job_id, cwd=commit_cwd)
        except GitError:
            log.warning("resolve_auto_commit_failed", job_id=job_id, exc_info=True)

        # --- Step 1: Try fast-forward via ref update (no checkout needed) ---
        try:
            ff_result = await self._try_ff_via_ref(job_id, repo_path, branch, base_ref)
            if ff_result is not None:
                return ff_result
        except GitError:
            log.debug("resolve_ff_ref_failed", job_id=job_id, exc_info=True)

        # --- Step 2: Full merge requires the main worktree. Acquire lock. ---
        lock = self._repo_locks.setdefault(repo_path, asyncio.Lock())
        async with lock:
            return await self._operator_merge_in_worktree(
                job_id,
                repo_path,
                worktree_path,
                branch,
                base_ref,
            )

    async def _operator_merge_in_worktree(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
        base_ref: str,
    ) -> MergeResult:
        """Operator merge using checkout (lock must be held)."""
        async with self._preserved_worktree(repo_path, job_id, "resolve"):
            merge_ok, conflict_files, error = await self._checkout_and_merge(
                job_id,
                repo_path,
                branch,
                base_ref,
            )

            if merge_ok:
                log.info("resolve_merge_succeeded", job_id=job_id, branch=branch)
                await self._update_merge_status(job_id, Resolution.merged)
                await self._publish_merge_completed(job_id, branch, base_ref, "merge")
                await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
                return MergeResult(status=MergeStatus.merged, strategy="merge")

            if merge_ok is None:
                await self._update_merge_status(job_id, "not_merged")
                return MergeResult(status=MergeStatus.error, error=error or "Merge failed without conflict markers")

            await self._update_merge_status(job_id, Resolution.conflict)
            await self._publish_merge_conflict(
                job_id,
                branch,
                base_ref,
                conflict_files,
                fallback="none",
                pr_url=None,
            )
            return MergeResult(status=MergeStatus.conflict, conflict_files=conflict_files)

    async def _operator_smart_merge(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
        base_ref: str,
    ) -> MergeResult:
        """Cherry-pick the job branch's commits onto base_ref (no merge commit).

        Strategy:
        1. Auto-commit any uncommitted agent changes in the worktree.
        2. Checkout base_ref in the main worktree.
        3. Cherry-pick the range base_ref..branch onto HEAD.
        4. On success: cleanup worktree/branch → merged.
        5. On conflict: abort, collect conflict files → conflict (no PR fallback).
        """
        # Auto-commit any uncommitted changes so cherry-pick sees them
        commit_cwd = worktree_path or repo_path
        try:
            committed = await self._git.auto_commit(
                cwd=commit_cwd,
                message=f"CodePlane: agent changes for {job_id}",
            )
            if committed:
                log.info("smart_merge_auto_committed", job_id=job_id, cwd=commit_cwd)
        except GitError:
            log.warning("smart_merge_auto_commit_failed", job_id=job_id, exc_info=True)

        lock = self._repo_locks.setdefault(repo_path, asyncio.Lock())
        async with lock:
            return await self._operator_smart_merge_locked(
                job_id,
                repo_path,
                worktree_path,
                branch,
                base_ref,
            )

    async def _operator_smart_merge_locked(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
        base_ref: str,
    ) -> MergeResult:
        """Cherry-pick onto base_ref (lock must be held)."""
        async with self._preserved_worktree(repo_path, job_id, "smart_merge"):
            try:
                await self._git.checkout(base_ref, cwd=repo_path)
            except GitError:
                log.warning("smart_merge_checkout_failed", job_id=job_id, base_ref=base_ref)
                return MergeResult(status=MergeStatus.error, error=f"Failed to checkout {base_ref}")

            commit_range = f"{base_ref}..{branch}"
            try:
                await self._git.cherry_pick(commit_range, cwd=repo_path)
            except GitError as exc:
                # Check for actual conflict markers BEFORE aborting — abort removes them.
                conflict_files = await self._git.get_conflict_files(cwd=repo_path)
                await self._git.cherry_pick_abort(cwd=repo_path)

                if not conflict_files:
                    # Cherry-pick failed but left no conflict markers → not a real
                    # merge conflict (e.g. hook failure, missing git identity, or
                    # the commit range was already applied).  Return an error so the
                    # job stays in "unresolved" and the user can retry or create a PR.
                    log.warning(
                        "smart_merge_failed_no_conflict_markers",
                        job_id=job_id,
                        branch=branch,
                        commit_range=commit_range,
                        stderr=exc.stderr,
                    )
                    return MergeResult(
                        status=MergeStatus.error,
                        error=_classify_cherry_pick_failure(exc),
                    )

                log.info("smart_merge_conflict_detected", job_id=job_id, branch=branch)
                try:
                    await self._git.checkout(base_ref, cwd=repo_path)
                except GitError:
                    log.warning("smart_merge_checkout_base_failed", job_id=job_id, exc_info=True)
                await self._update_merge_status(job_id, Resolution.conflict)
                await self._publish_merge_conflict(
                    job_id,
                    branch,
                    base_ref,
                    conflict_files,
                    fallback="none",
                    pr_url=None,
                )
                return MergeResult(status=MergeStatus.conflict, conflict_files=conflict_files)

            log.info("smart_merge_succeeded", job_id=job_id, branch=branch, base_ref=base_ref)
            await self._update_merge_status(job_id, Resolution.merged)
            await self._publish_merge_completed(job_id, branch, base_ref, "cherry_pick")
            await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
            return MergeResult(status=MergeStatus.merged, strategy="cherry_pick")

    async def _discard(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str | None,
    ) -> MergeResult:
        """Discard all changes: remove worktree and delete branch."""
        if worktree_path:
            try:
                await self._git.remove_worktree(repo_path, worktree_path)
                log.info("worktree_discarded", job_id=job_id, worktree=worktree_path)
            except (GitError, OSError):
                log.warning("worktree_discard_failed", job_id=job_id, exc_info=True)

        if branch and branch not in ("main", "master"):
            try:
                with contextlib.suppress(GitError):
                    await self._git._run_git("branch", "-D", branch, cwd=repo_path)  # noqa: SLF001
                log.info("branch_discarded", job_id=job_id, branch=branch)
            except GitError:
                log.warning("branch_discard_failed", job_id=job_id, exc_info=True)

        return MergeResult(status=MergeStatus.skipped)

    async def _cleanup_worktree_only(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
    ) -> None:
        """Remove worktree without deleting the branch."""
        if not worktree_path or worktree_path == repo_path:
            return
        try:
            from pathlib import Path

            wt = Path(worktree_path)
            if not wt.exists():
                return
            try:
                await self._git._run_git("worktree", "remove", str(worktree_path), "--force", cwd=repo_path)  # noqa: SLF001
            except GitError:
                if wt.exists():
                    shutil.rmtree(wt)
                await self._git._run_git("worktree", "prune", cwd=repo_path)  # noqa: SLF001
            log.info("worktree_cleaned_after_pr", job_id=job_id, worktree=worktree_path)
        except (GitError, OSError):
            log.warning("worktree_cleanup_after_pr_failed", job_id=job_id, exc_info=True)
