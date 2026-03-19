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
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from backend.models.events import DomainEvent, DomainEventKind

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.config import CompletionConfig
    from backend.services.diff_service import DiffService
    from backend.services.event_bus import EventBus
    from backend.services.git_service import GitService
    from backend.services.platform_adapter import PlatformRegistry

log = structlog.get_logger()

_REF_PATTERN = re.compile(r"^[a-zA-Z0-9/_.-]+$")


def _make_event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


@dataclass
class MergeResult:
    """Outcome of a merge-back attempt."""

    status: str  # merged | conflict | pr_created | skipped | error
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
            return MergeResult(status="skipped", error="No branch")

        if not _REF_PATTERN.match(branch) or not _REF_PATTERN.match(base_ref):
            log.warning("merge_skipped_invalid_refs", job_id=job_id, branch=branch, base_ref=base_ref)
            return MergeResult(status="skipped", error="Invalid branch or base_ref")

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
        except Exception:
            log.warning("merge_auto_commit_failed", job_id=job_id, exc_info=True)

        # --- Step 1: Try fast-forward via ref update (no checkout needed) ---
        try:
            ff_result = await self._try_ff_via_ref(job_id, repo_path, branch, base_ref)
            if ff_result is not None:
                return ff_result
        except Exception:
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

        await self._publish_merge_completed(job_id, branch, base_ref, "ff_only")
        await self._post_merge_cleanup(job_id, repo_path, None, branch)
        await self._update_merge_status(job_id, "merged")
        return MergeResult(status="merged", strategy="ff_only")

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

        # Remember what the main worktree was doing so we can restore it.
        original_branch: str | None = None
        main_stashed = False

        with contextlib.suppress(Exception):
            original_branch = await self._git.get_current_branch(cwd=repo_path)

        try:
            main_stashed = await self._git.stash(cwd=repo_path)
        except Exception:
            log.warning("merge_main_stash_failed", job_id=job_id, exc_info=True)

        try:
            result = await self._do_merge_steps(
                job_id,
                repo_path,
                worktree_path,
                branch,
                base_ref,
                prompt,
            )
        finally:
            # Restore the main worktree to its original branch, then pop stash.
            if original_branch:
                try:
                    await self._git.checkout(original_branch, cwd=repo_path)
                except Exception:
                    log.warning("merge_restore_branch_failed", job_id=job_id, exc_info=True)
            if main_stashed:
                try:
                    await self._git.stash_pop(cwd=repo_path)
                except Exception:
                    log.warning("merge_main_stash_pop_failed", job_id=job_id, exc_info=True)

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
        from backend.services.git_service import GitError

        # Step 1: checkout the base branch.  Treat checkout failures as hard
        # errors, NOT as merge conflicts — they have different root causes and
        # different remediation paths.
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
        except GitError as exc:
            log.warning("merge_checkout_failed", job_id=job_id, base_ref=base_ref, error=str(exc))
            return MergeResult(status="error", error=f"Failed to checkout {base_ref}: {exc}")

        # Step 2: attempt the merge.
        try:
            await self._git.merge(
                branch,
                cwd=repo_path,
                message=f"Merge {branch} (CodePlane {job_id})",
            )
        except GitError:
            # Merge produced conflict markers — fall through to conflict handling.
            pass
        else:
            log.info("merge_succeeded", job_id=job_id, branch=branch, base_ref=base_ref)
            await self._publish_merge_completed(job_id, branch, base_ref, "merge")
            await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
            await self._update_merge_status(job_id, "merged")
            return MergeResult(status="merged", strategy="merge")

        # Merge failed — abort and probe for the conflicting files.
        await self._git.merge_abort(cwd=repo_path)
        conflict_files = await self._get_conflict_file_list(repo_path, branch, base_ref)

        # If the probe merge succeeded (conflict_files is None — meaning the
        # probe reported "actually clean"), the first attempt failed for a
        # transient reason (dirty index, lock file, etc.).  Retry once.
        if conflict_files is None:
            log.info("merge_retrying_after_clean_probe", job_id=job_id, branch=branch)
            try:
                await self._git.checkout(base_ref, cwd=repo_path)
                await self._git.merge(
                    branch,
                    cwd=repo_path,
                    message=f"Merge {branch} (CodePlane {job_id})",
                )
            except GitError:
                pass
            else:
                log.info("merge_succeeded_on_retry", job_id=job_id, branch=branch, base_ref=base_ref)
                await self._publish_merge_completed(job_id, branch, base_ref, "merge")
                await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
                await self._update_merge_status(job_id, "merged")
                return MergeResult(status="merged", strategy="merge")

            # Retry also failed after a clean probe — this is a git-level
            # error (hook, identity, lock, etc.), not a real merge conflict.
            await self._git.merge_abort(cwd=repo_path)
            log.warning("merge_retry_failed_after_clean_probe", job_id=job_id, branch=branch)
            return MergeResult(status="error", error="Merge failed for a non-conflict reason; check git configuration")

        log.info("merge_conflict_detected", job_id=job_id, conflict_files=conflict_files)

        try:
            await self._git.checkout(base_ref, cwd=repo_path)
        except Exception:
            log.warning("checkout_base_ref_failed", job_id=job_id, exc_info=True)

        await self._publish_merge_conflict(
            job_id,
            branch,
            base_ref,
            conflict_files,
            fallback="none",
        )
        await self._update_merge_status(job_id, "conflict")
        return MergeResult(status="conflict", conflict_files=conflict_files)

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
        except Exception:
            return []

        try:
            # --no-commit: never create a commit (no git identity required).
            # --no-ff: force a real merge attempt even when FF would suffice.
            await self._git._run_git("merge", "--no-commit", "--no-ff", branch, cwd=repo_path)  # noqa: SLF001
            # Probe succeeded — clean up the staged-but-uncommitted merge.
            await self._git.merge_abort(cwd=repo_path)
            return None
        except Exception:
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
            except Exception:
                log.warning("branch_push_failed", job_id=job_id, exc_info=True)
                # Continue — agent may have already pushed

        if self._platform_registry is None:
            log.info("pr_creation_skipped_no_registry", job_id=job_id)
            await self._update_merge_status(job_id, "not_merged")
            return MergeResult(status="skipped", error="No platform registry")

        adapter = await self._platform_registry.get_adapter(repo_path)
        pr_result = await adapter.create_pr(
            cwd=cwd,
            head=branch,
            base=base_ref,
            title=f"[CodePlane] {prompt[:80]}",
            body=f"Automated PR created by CodePlane for job `{job_id}`.",
        )

        if pr_result.ok:
            log.info("pr_created", job_id=job_id, pr_url=pr_result.url, platform=adapter.name)
            await self._update_merge_status(job_id, "pr_created", pr_url=pr_result.url)
            return MergeResult(status="pr_created", strategy="pr", pr_url=pr_result.url)

        log.warning("pr_creation_failed", job_id=job_id, platform=adapter.name, error=pr_result.error)
        await self._update_merge_status(job_id, "not_merged")
        return MergeResult(status="error", error=pr_result.error or "PR creation failed")

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
            except Exception:
                log.warning("worktree_cleanup_failed", job_id=job_id, exc_info=True)

        if self._config.delete_branch_after_merge:
            try:
                from backend.services.git_service import GitError

                with contextlib.suppress(GitError):
                    await self._git._run_git("branch", "-d", branch, cwd=repo_path)  # noqa: SLF001
            except Exception:
                log.warning("branch_cleanup_failed", job_id=job_id, exc_info=True)

    async def _update_merge_status(
        self,
        job_id: str,
        merge_status: str,
        pr_url: str | None = None,
    ) -> None:
        """Persist merge status to the database."""
        from backend.persistence.job_repo import JobRepository

        try:
            async with self._session_factory() as session:
                repo = JobRepository(session)
                await repo.update_merge_status(job_id, merge_status, pr_url=pr_url)
                await session.commit()
        except Exception:
            log.warning("merge_status_update_failed", job_id=job_id, exc_info=True)

    async def _publish_merge_completed(
        self,
        job_id: str,
        branch: str,
        base_ref: str,
        strategy: str,
    ) -> None:
        await self._event_bus.publish(
            DomainEvent(
                event_id=_make_event_id(),
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
                event_id=_make_event_id(),
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
            return MergeResult(status="error", error="No branch to resolve")

        if not _REF_PATTERN.match(branch) or not _REF_PATTERN.match(base_ref):
            return MergeResult(status="error", error="Invalid branch or base_ref")

        if action == "create_pr":
            result = await self._create_pr(job_id, repo_path, worktree_path, branch, base_ref, prompt)
            if result.status == "pr_created":
                await self._cleanup_worktree_only(job_id, repo_path, worktree_path)
            return result

        if action == "merge":
            return await self._operator_merge(job_id, repo_path, worktree_path, branch, base_ref, prompt)

        if action == "smart_merge":
            return await self._operator_smart_merge(job_id, repo_path, worktree_path, branch, base_ref)

        return MergeResult(status="error", error=f"Unknown action: {action}")

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
        except Exception:
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
        except Exception:
            log.warning("resolve_auto_commit_failed", job_id=job_id, exc_info=True)

        # --- Step 1: Try fast-forward via ref update (no checkout needed) ---
        try:
            ff_result = await self._try_ff_via_ref(job_id, repo_path, branch, base_ref)
            if ff_result is not None:
                return ff_result
        except Exception:
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
        from backend.services.git_service import GitError

        original_branch: str | None = None
        main_stashed = False

        with contextlib.suppress(Exception):
            original_branch = await self._git.get_current_branch(cwd=repo_path)

        try:
            main_stashed = await self._git.stash(cwd=repo_path)
        except Exception:
            log.warning("resolve_main_stash_failed", job_id=job_id, exc_info=True)

        try:
            # Step 1: checkout — treat failures as hard errors, NOT conflicts.
            try:
                await self._git.checkout(base_ref, cwd=repo_path)
            except GitError as exc:
                log.warning("resolve_checkout_failed", job_id=job_id, base_ref=base_ref, error=str(exc))
                return MergeResult(status="error", error=f"Failed to checkout {base_ref}: {exc}")

            # Step 2: attempt the merge.
            try:
                await self._git.merge(
                    branch,
                    cwd=repo_path,
                    message=f"Merge {branch} (CodePlane {job_id})",
                )
            except GitError:
                pass
            else:
                log.info("resolve_merge_succeeded", job_id=job_id, branch=branch)
                await self._publish_merge_completed(job_id, branch, base_ref, "merge")
                await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
                await self._update_merge_status(job_id, "merged")
                return MergeResult(status="merged", strategy="merge")

            # Merge failed — probe for actual conflict files.
            await self._git.merge_abort(cwd=repo_path)
            conflict_files = await self._get_conflict_file_list(repo_path, branch, base_ref)

            # If the probe merge succeeded (None return), the first attempt failed
            # for a transient reason (dirty index, lock file, etc.). Retry once.
            if conflict_files is None:
                log.info("resolve_retrying_after_clean_probe", job_id=job_id, branch=branch)
                try:
                    await self._git.checkout(base_ref, cwd=repo_path)
                    await self._git.merge(
                        branch,
                        cwd=repo_path,
                        message=f"Merge {branch} (CodePlane {job_id})",
                    )
                except GitError:
                    pass
                else:
                    log.info("resolve_merge_succeeded_on_retry", job_id=job_id, branch=branch)
                    await self._publish_merge_completed(job_id, branch, base_ref, "merge")
                    await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
                    await self._update_merge_status(job_id, "merged")
                    return MergeResult(status="merged", strategy="merge")

                # Retry also failed after a clean probe — this is a git-level
                # error (hook, identity, lock, etc.), not a real merge conflict.
                await self._git.merge_abort(cwd=repo_path)
                log.warning("resolve_retry_failed_after_clean_probe", job_id=job_id, branch=branch)
                return MergeResult(status="error", error="Merge failed for a non-conflict reason; check git configuration")

            log.info("resolve_merge_conflict", job_id=job_id, conflict_files=conflict_files)

            try:
                await self._git.checkout(base_ref, cwd=repo_path)
            except Exception:
                log.warning("checkout_base_ref_failed", job_id=job_id, exc_info=True)

            await self._publish_merge_conflict(
                job_id,
                branch,
                base_ref,
                conflict_files,
                fallback="none",
                pr_url=None,
            )
            return MergeResult(status="conflict", conflict_files=conflict_files)
        finally:
            if original_branch:
                try:
                    await self._git.checkout(original_branch, cwd=repo_path)
                except Exception:
                    log.warning("resolve_restore_branch_failed", job_id=job_id, exc_info=True)
            if main_stashed:
                try:
                    await self._git.stash_pop(cwd=repo_path)
                except Exception:
                    log.warning("resolve_main_stash_pop_failed", job_id=job_id, exc_info=True)

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
        1. Checkout base_ref in the main worktree.
        2. Cherry-pick the range base_ref..branch onto HEAD.
        3. On success: cleanup worktree/branch → merged.
        4. On conflict: abort, collect conflict files → conflict (no PR fallback).
        """
        from backend.services.git_service import GitError

        try:
            await self._git.checkout(base_ref, cwd=repo_path)
        except GitError:
            log.warning("smart_merge_checkout_failed", job_id=job_id, base_ref=base_ref)
            return MergeResult(status="error", error=f"Failed to checkout {base_ref}")

        commit_range = f"{base_ref}..{branch}"
        try:
            await self._git.cherry_pick(commit_range, cwd=repo_path)
        except GitError:
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
                )
                return MergeResult(status="error", error="Cherry-pick failed without conflict markers; check git configuration or hooks")

            log.info("smart_merge_conflict_detected", job_id=job_id, branch=branch)
            try:
                await self._git.checkout(base_ref, cwd=repo_path)
            except Exception:
                log.warning("smart_merge_checkout_base_failed", job_id=job_id, exc_info=True)
            await self._publish_merge_conflict(
                job_id,
                branch,
                base_ref,
                conflict_files,
                fallback="none",
                pr_url=None,
            )
            return MergeResult(status="conflict", conflict_files=conflict_files)

        log.info("smart_merge_succeeded", job_id=job_id, branch=branch, base_ref=base_ref)
        await self._publish_merge_completed(job_id, branch, base_ref, "cherry_pick")
        await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
        await self._update_merge_status(job_id, "merged")
        return MergeResult(status="merged", strategy="cherry_pick")

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
            except Exception:
                log.warning("worktree_discard_failed", job_id=job_id, exc_info=True)

        if branch and branch not in ("main", "master"):
            try:
                from backend.services.git_service import GitError

                with contextlib.suppress(GitError):
                    await self._git._run_git("branch", "-D", branch, cwd=repo_path)  # noqa: SLF001
                log.info("branch_discarded", job_id=job_id, branch=branch)
            except Exception:
                log.warning("branch_discard_failed", job_id=job_id, exc_info=True)

        return MergeResult(status="discarded")

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
            except Exception:
                if wt.exists():
                    shutil.rmtree(wt)
                await self._git._run_git("worktree", "prune", cwd=repo_path)  # noqa: SLF001
            log.info("worktree_cleaned_after_pr", job_id=job_id, worktree=worktree_path)
        except Exception:
            log.warning("worktree_cleanup_after_pr_failed", job_id=job_id, exc_info=True)
