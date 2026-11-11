"""MergeService class — post-completion merge-back orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import SQLAlchemyError

from backend.models.domain import GitMergeOutcome
from backend.models.events import EventKind, new_event
from backend.services.git.git_service import GitError
from backend.services.merge_service._types import (
    _NOT_MERGED,
    _PR_TITLE_MAX_PROMPT_LEN,
    _REF_PATTERN,
    MergeResult,
    MergeStatus,
    _classify_cherry_pick_failure,
    _MergeOutcome,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CompletionConfig
    from backend.models.domain import Job
    from backend.services.adapters.platform_adapter import PlatformRegistry
    from backend.services.artifacts.diff_service import DiffService
    from backend.services.events.event_bus import EventBus
    from backend.services.git.git_service import GitService

log = structlog.get_logger()


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
            result = await self._create_pr(job_id, repo_path, worktree_path, branch, base_ref, prompt)
            if result.status == MergeStatus.pr_created:
                await self._cleanup_worktree_only(job_id, repo_path, worktree_path)
            return result

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

        # Acquire per-repo lock BEFORE attempting FF — two concurrent FF
        # merges to the same base_ref would race on update_ref, and the
        # second would silently overwrite the first, losing commits.
        lock = self._repo_locks.setdefault(repo_path, asyncio.Lock())
        async with lock:
            # --- Step 1: Try fast-forward via ref update (no checkout needed) ---
            try:
                ff_result = await self._try_ff_via_ref(job_id, repo_path, branch, base_ref)
                if ff_result is not None:
                    return ff_result
            except GitError:
                log.debug("merge_ff_ref_failed", job_id=job_id, exc_info=True)

            # --- Step 2: Regular merge in worktree (lock already held) ---
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
        *,
        skip_cleanup: bool = False,
    ) -> MergeResult | None:
        """Attempt a fast-forward by updating the base ref directly.

        Returns a MergeResult on success, or None if FF is not possible.
        When *skip_cleanup* is True, the branch and worktree are preserved
        (used by imported session resolution).
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
        await self._update_merge_status(job_id, GitMergeOutcome.merged)
        await self._publish_merge_completed(job_id, branch, base_ref, "ff_only")
        if not skip_cleanup:
            await self._post_merge_cleanup(job_id, repo_path, None, branch)
        return MergeResult(status=MergeStatus.merged, strategy="ff_only")

    @contextlib.asynccontextmanager
    async def _preserved_worktree(self, repo_path: str, job_id: str, log_prefix: str = "merge") -> AsyncIterator[None]:
        """Save and restore the main worktree's branch + stash state."""
        original_branch: str | None = None
        main_stashed = False

        try:
            original_branch = await self._git.get_current_branch(cwd=repo_path)
        except GitError:
            log.debug(f"{log_prefix}_get_branch_failed", job_id=job_id, exc_info=True)

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
    ) -> tuple[_MergeOutcome, list[str], str | None]:
        """Checkout base_ref, attempt merge, and classify the outcome.

        Returns ``(outcome, conflict_files, error)`` where:

        * ``outcome is _MergeOutcome.success`` — merge succeeded cleanly.
        * ``outcome is _MergeOutcome.conflict`` — real merge conflict;
          ``conflict_files`` lists the unmerged paths.
        * ``outcome is _MergeOutcome.error`` — merge failed for a
          non-conflict reason (hooks, identity, locks, etc.) and
          ``error`` describes it.
        """
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
            await self._git.merge(
                branch,
                cwd=repo_path,
                message=f"Merge {branch} (CodePlane {job_id})",
            )
            return _MergeOutcome.success, [], None
        except GitError as exc:
            error = str(exc)

        # Merge failed — abort, then classify whether it was a real conflict.
        await self._git.merge_abort(cwd=repo_path)
        conflict_files = await self._get_conflict_file_list(repo_path, branch, base_ref)

        try:
            await self._git.checkout(base_ref, cwd=repo_path)
        except GitError:
            log.warning("checkout_base_ref_failed", job_id=job_id, exc_info=True)

        if conflict_files:
            log.info("merge_conflict_detected", job_id=job_id, branch=branch, conflict_files=conflict_files)
            return _MergeOutcome.conflict, conflict_files, None

        log.warning("merge_failed_without_conflicts", job_id=job_id, branch=branch, error=error)
        return _MergeOutcome.error, [], error

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
            result = await self._execute_merge_in_worktree(
                job_id,
                repo_path,
                worktree_path,
                branch,
                base_ref,
                prompt,
            )
        return result

    async def _execute_merge_in_worktree(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
        base_ref: str,
        prompt: str,
    ) -> MergeResult:
        """Checkout + merge in the main worktree (caller handles stash/restore)."""
        outcome, conflict_files, error = await self._checkout_and_merge(
            job_id,
            repo_path,
            branch,
            base_ref,
        )

        if outcome is _MergeOutcome.success:
            log.info("merge_succeeded", job_id=job_id, branch=branch, base_ref=base_ref)
            await self._update_merge_status(job_id, GitMergeOutcome.merged)
            await self._publish_merge_completed(job_id, branch, base_ref, "merge")
            await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
            return MergeResult(status=MergeStatus.merged, strategy="merge")

        if outcome is _MergeOutcome.error:
            await self._update_merge_status(job_id, _NOT_MERGED)
            return MergeResult(status=MergeStatus.error, error=error or "Merge failed without conflict markers")

        # outcome is _MergeOutcome.conflict
        await self._update_merge_status(job_id, GitMergeOutcome.conflict)
        await self._publish_merge_conflict(
            job_id,
            branch,
            base_ref,
            conflict_files,
            fallback="none",
        )
        return MergeResult(status=MergeStatus.conflict, conflict_files=conflict_files)

    async def _get_conflict_file_list(self, repo_path: str, branch: str, base_ref: str) -> list[str]:
        """Probe for conflicting files using a no-commit merge that never creates a commit.

        Uses ``--no-commit --no-ff`` so git identity is never required, making
        the probe robust against misconfigured environments and git hooks.

        Returns:
            A list of conflicting file paths (non-empty) if real merge
            conflicts exist, or an empty list when no conflicts are detected
            (including infrastructure failures where no conflict markers were
            produced).
        """
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
        except GitError:
            log.warning("merge_checkout_failed", base_ref=base_ref, repo_path=str(repo_path), exc_info=True)
            return []

        try:
            # --no-commit: never create a commit (no git identity required).
            # --no-ff: force a real merge attempt even when FF would suffice.
            await self._git._run_git("merge", "--no-commit", "--no-ff", branch, cwd=repo_path)  # noqa: SLF001
            # Probe succeeded — clean up the staged-but-uncommitted merge.
            await self._git.merge_abort(cwd=repo_path)
            return []
        except GitError:
            files = await self._git.get_conflict_files(cwd=repo_path)
            await self._git.merge_abort(cwd=repo_path)
            return files

    async def _get_existing_pr_url(self, job_id: str) -> str | None:
        """Return the job's already-recorded PR url, if any.

        Reads the current persisted state directly (not a cached ``Job``
        the caller may be holding) so a race between two nearly-simultaneous
        PR requests for the same job still converges on one PR.
        """
        from backend.persistence.job_repo import JobRepository

        async with self._session_factory() as session:
            job = await JobRepository(session).get(job_id)
        return job.pr_url if job is not None else None

    async def _create_pr(
        self,
        job_id: str,
        repo_path: str,
        worktree_path: str | None,
        branch: str,
        base_ref: str,
        prompt: str,
    ) -> MergeResult:
        """Push branch and create a PR via platform adapter.

        Idempotent per Job: if a PR already exists for this job (recorded via
        an earlier call — either the completion-time auto-PR path or the
        agent-initiated ``codeplane_pr`` MCP tool), returns that PR instead of
        pushing again or calling the platform adapter a second time (AD-14).
        """
        existing_pr_url = await self._get_existing_pr_url(job_id)
        if existing_pr_url:
            log.info("pr_already_exists", job_id=job_id, pr_url=existing_pr_url)
            return MergeResult(status=MergeStatus.pr_created, strategy="pr", pr_url=existing_pr_url)

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
            await self._update_merge_status(job_id, _NOT_MERGED)
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
            await self._update_merge_status(job_id, GitMergeOutcome.pr_created, pr_url=pr_result.url)
            return MergeResult(status=MergeStatus.pr_created, strategy="pr", pr_url=pr_result.url)

        log.warning("pr_creation_failed", job_id=job_id, platform=adapter.name, error=pr_result.error)
        await self._update_merge_status(job_id, _NOT_MERGED)
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
                await self._git._run_git("branch", "-d", branch, cwd=repo_path)  # noqa: SLF001
            except GitError:
                log.debug("branch_delete_after_merge_failed", job_id=job_id, branch=branch, exc_info=True)

    _MERGE_STATUS_MAX_ATTEMPTS = 3
    _MERGE_STATUS_RETRY_DELAY_S = 0.05

    async def _update_merge_status(
        self,
        job_id: str,
        merge_status: GitMergeOutcome,
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
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.merge_completed,
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
            new_event(
                session_id=job_id,
                timestamp=datetime.now(UTC),
                kind=EventKind.merge_conflict,
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
        job: Job,
        action: str,
    ) -> MergeResult:
        """Operator-initiated job resolution.

        action: "merge" | "smart_merge" | "create_pr" | "discard"
        """
        # Imported sessions use a separate resolution path that avoids
        # destructive local operations (no worktree removal, no branch
        # deletion, no checkout in the user's working directory).
        if job.source != "managed":
            return await self._resolve_imported(job, action)

        job_id = job.id
        repo_path = job.repo
        worktree_path = job.worktree_path
        branch = job.branch
        base_ref = job.base_ref
        prompt = job.prompt

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

    # ------------------------------------------------------------------
    # Agent-initiated PR request (CAP-14) — codeplane_pr MCP tool
    # ------------------------------------------------------------------

    async def request_pr_for_job(self, job: Job) -> MergeResult:
        """Create (or reuse) a PR for a still-running Job, on agent request.

        Unlike :meth:`resolve_job`'s ``create_pr`` action — which runs only
        once a Job has reached ``review`` and cleans up the worktree
        afterward — this path is called *mid-job*, while the agent may still
        be working in the worktree.  It therefore never removes the worktree
        or branch.  Invokes the exact same :meth:`_create_pr` implementation
        the completion-time auto-PR path uses (AD-14), which is idempotent
        per Job, so a subsequent automatic PR at job completion (or another
        mid-job call) reuses the same PR rather than creating a duplicate.
        """
        if job.source != "managed":
            return MergeResult(status=MergeStatus.error, error="PR requests are only supported for managed jobs")

        if not job.branch:
            return MergeResult(status=MergeStatus.error, error="No branch to create a PR for")

        if not _REF_PATTERN.match(job.branch) or not _REF_PATTERN.match(job.base_ref):
            return MergeResult(status=MergeStatus.error, error="Invalid branch or base_ref")

        return await self._create_pr(
            job.id,
            job.repo,
            job.worktree_path,
            job.branch,
            job.base_ref,
            job.prompt,
        )

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

        # Acquire per-repo lock BEFORE FF — same race protection as _auto_merge.
        lock = self._repo_locks.setdefault(repo_path, asyncio.Lock())
        async with lock:
            # --- Step 1: Try fast-forward via ref update (no checkout needed) ---
            try:
                ff_result = await self._try_ff_via_ref(job_id, repo_path, branch, base_ref)
                if ff_result is not None:
                    return ff_result
            except GitError:
                log.debug("resolve_ff_ref_failed", job_id=job_id, exc_info=True)

            # --- Step 2: Regular merge in worktree (lock already held) ---
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
            outcome, conflict_files, error = await self._checkout_and_merge(
                job_id,
                repo_path,
                branch,
                base_ref,
            )

            if outcome is _MergeOutcome.success:
                log.info("resolve_merge_succeeded", job_id=job_id, branch=branch)
                await self._update_merge_status(job_id, GitMergeOutcome.merged)
                await self._publish_merge_completed(job_id, branch, base_ref, "merge")
                await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
                return MergeResult(status=MergeStatus.merged, strategy="merge")

            if outcome is _MergeOutcome.error:
                await self._update_merge_status(job_id, _NOT_MERGED)
                return MergeResult(status=MergeStatus.error, error=error or "Merge failed without conflict markers")

            # outcome is _MergeOutcome.conflict
            await self._update_merge_status(job_id, GitMergeOutcome.conflict)
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
                await self._update_merge_status(job_id, GitMergeOutcome.conflict)
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
            await self._update_merge_status(job_id, GitMergeOutcome.merged)
            await self._publish_merge_completed(job_id, branch, base_ref, "cherry_pick")
            await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
            return MergeResult(status=MergeStatus.merged, strategy="cherry_pick")

    async def _resolve_imported(
        self,
        job: Job,
        action: str,
    ) -> MergeResult:
        """Resolve an imported CLI session — no destructive local operations.

        For imported sessions CodePlane did not create the working directory
        or the branch, so it must never delete them.
        """
        job_id = job.id
        repo_path = job.repo
        branch = job.branch
        base_ref = job.base_ref
        worktree_path = job.worktree_path
        prompt = job.prompt

        # Preserve diff snapshot before resolution
        await self._preserve_diff_snapshot(job_id, worktree_path, base_ref)

        if action == "discard":
            # Mark as discarded — do NOT remove worktree or delete branch
            return MergeResult(status=MergeStatus.skipped)

        if not branch:
            return MergeResult(status=MergeStatus.error, error="No branch to resolve")

        # If already on default branch, nothing to merge
        if branch == base_ref:
            await self._update_merge_status(job_id, GitMergeOutcome.merged)
            await self._publish_merge_completed(job_id, branch, base_ref, "already_on_target")
            return MergeResult(status=MergeStatus.merged, strategy="already_on_target")

        if action == "create_pr":
            # Push branch, create PR — do NOT clean up worktree
            try:
                await self._git.push(branch, cwd=repo_path)
            except GitError:
                log.warning("imported_push_failed", job_id=job_id, exc_info=True)
            return await self._create_pr(job_id, repo_path, worktree_path, branch, base_ref, prompt or "")

        if action in ("merge", "smart_merge"):
            # Auto-commit any uncommitted changes
            try:
                committed = await self._git.auto_commit(
                    cwd=worktree_path or repo_path,
                    message=f"CodePlane: agent changes for {job_id}",
                )
                if committed:
                    log.info("imported_auto_committed", job_id=job_id)
            except GitError:
                log.warning("imported_auto_commit_failed", job_id=job_id, exc_info=True)

            # Push the branch
            try:
                await self._git.push(branch, cwd=repo_path)
            except GitError:
                log.warning("imported_push_failed", job_id=job_id, exc_info=True)

            # Try fast-forward via ref update (no checkout needed)
            lock = self._repo_locks.setdefault(repo_path, asyncio.Lock())
            async with lock:
                try:
                    ff_result = await self._try_ff_via_ref(
                        job_id,
                        repo_path,
                        branch,
                        base_ref,
                        skip_cleanup=True,
                    )
                    if ff_result is not None:
                        # Do NOT call _post_merge_cleanup — leave worktree and branch
                        return ff_result
                except GitError:
                    log.debug("imported_ff_ref_failed", job_id=job_id, exc_info=True)

            # FF failed — fall back to PR creation
            return await self._create_pr(job_id, repo_path, worktree_path, branch, base_ref, prompt or "")

        return MergeResult(status=MergeStatus.error, error=f"Unknown action: {action}")

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
                await self._git._run_git("branch", "-D", branch, cwd=repo_path)  # noqa: SLF001
                log.info("branch_discarded", job_id=job_id, branch=branch)
            except GitError:
                log.warning("branch_discard_failed", job_id=job_id, branch=branch, exc_info=True)

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
