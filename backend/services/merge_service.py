"""Post-completion merge-back orchestration.

Attempts to merge a job's branch back into the base branch using an
escalation strategy:

1. Fast-forward merge (cleanest — no merge commit)
2. Regular merge (if base has diverged but no conflicts)
3. Fallback to PR creation (if conflicts are detected)
"""

from __future__ import annotations

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
    ) -> None:
        self._git = git_service
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._config = config
        self._platform_registry = platform_registry

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
        from backend.services.git_service import GitError

        # Step 1: Try fast-forward merge in the main worktree
        ff_ok = False
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
            await self._git.merge_ff_only(branch, cwd=repo_path)
            ff_ok = True
        except GitError:
            log.debug("merge_ff_failed_trying_merge", job_id=job_id)

        if ff_ok:
            log.info("merge_ff_succeeded", job_id=job_id, branch=branch, base_ref=base_ref)
            await self._publish_merge_completed(job_id, branch, base_ref, "ff_only")
            await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
            await self._update_merge_status(job_id, "merged")
            return MergeResult(status="merged", strategy="ff_only")

        # Step 2: Try regular merge
        merge_ok = False
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
            await self._git.merge(
                branch,
                cwd=repo_path,
                message=f"Merge {branch} (Tower {job_id})",
            )
            merge_ok = True
        except GitError:
            log.info("merge_conflict_detected", job_id=job_id, branch=branch)

        if merge_ok:
            log.info("merge_succeeded", job_id=job_id, branch=branch, base_ref=base_ref)
            await self._publish_merge_completed(job_id, branch, base_ref, "merge")
            await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
            await self._update_merge_status(job_id, "merged")
            return MergeResult(status="merged", strategy="merge")

        # Step 3: Abort the failed merge and collect conflict info
        await self._git.merge_abort(cwd=repo_path)
        conflict_files = await self._detect_conflicts(repo_path, branch, base_ref)

        # Step 4: Restore main worktree to base_ref
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
        except Exception:
            log.warning("checkout_base_ref_failed", job_id=job_id, exc_info=True)

        # Step 5: Fall back to PR
        log.info("merge_falling_back_to_pr", job_id=job_id, conflict_files=conflict_files)
        pr_result = await self._create_pr(job_id, repo_path, worktree_path, branch, base_ref, prompt)

        await self._publish_merge_conflict(
            job_id,
            branch,
            base_ref,
            conflict_files,
            fallback="pr_created" if pr_result.pr_url else "none",
            pr_url=pr_result.pr_url,
        )
        await self._update_merge_status(
            job_id,
            "conflict",
            pr_url=pr_result.pr_url,
        )
        return MergeResult(
            status="conflict",
            conflict_files=conflict_files,
            pr_url=pr_result.pr_url,
        )

    async def _detect_conflicts(self, repo_path: str, branch: str, base_ref: str) -> list[str]:
        """Dry-run a merge to detect conflicting files without leaving state."""
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
        except Exception:
            return []

        try:
            await self._git.merge(branch, cwd=repo_path)
            # If this succeeds, no conflicts — undo the merge
            await self._git.merge_abort(cwd=repo_path)
            return []
        except Exception:
            files = await self._git.get_conflict_files(cwd=repo_path)
            await self._git.merge_abort(cwd=repo_path)
            return files

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
            title=f"[Tower] {prompt[:80]}",
            body=f"Automated PR created by Tower for job `{job_id}`.",
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
        pr_url: str | None,
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

        action: "merge" | "create_pr" | "discard"
        """
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

        return MergeResult(status="error", error=f"Unknown action: {action}")

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
        from backend.services.git_service import GitError

        # Step 1: Try fast-forward
        ff_ok = False
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
            await self._git.merge_ff_only(branch, cwd=repo_path)
            ff_ok = True
        except GitError:
            log.debug("resolve_merge_ff_failed", job_id=job_id)

        if ff_ok:
            log.info("resolve_merge_ff_succeeded", job_id=job_id, branch=branch)
            await self._publish_merge_completed(job_id, branch, base_ref, "ff_only")
            await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
            await self._update_merge_status(job_id, "merged")
            return MergeResult(status="merged", strategy="ff_only")

        # Step 2: Try regular merge
        merge_ok = False
        try:
            await self._git.checkout(base_ref, cwd=repo_path)
            await self._git.merge(
                branch,
                cwd=repo_path,
                message=f"Merge {branch} (Tower {job_id})",
            )
            merge_ok = True
        except GitError:
            log.info("resolve_merge_conflict", job_id=job_id, branch=branch)

        if merge_ok:
            log.info("resolve_merge_succeeded", job_id=job_id, branch=branch)
            await self._publish_merge_completed(job_id, branch, base_ref, "merge")
            await self._post_merge_cleanup(job_id, repo_path, worktree_path, branch)
            await self._update_merge_status(job_id, "merged")
            return MergeResult(status="merged", strategy="merge")

        # Step 3: Conflict — abort merge, report conflict files, do NOT create PR
        await self._git.merge_abort(cwd=repo_path)
        conflict_files = await self._detect_conflicts(repo_path, branch, base_ref)

        # Restore main worktree to base_ref
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
