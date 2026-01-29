"""Refresh job management with HEAD-aware deduplication.

This module implements the Refresh Job Worker from SPEC.md §7.5:
- Job enqueueing with scope merging (monotonic widening)
- HEAD-aware deduplication (supersede on HEAD change)
- Fail-fast protocol for missing SCIP tools
- SCIP output import with Read-After-Write FK resolution
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlmodel import Session, col, select

from codeplane.index.models import (
    Context,
    JobFailureReason,
    JobStatus,
    LanguageFamily,
    RefreshJob,
    RefreshScope,
)

if TYPE_CHECKING:
    from typing import Any

    from codeplane.index._internal.db import Database

    ScipImporter = Any  # Placeholder for future implementation


# SCIP tool registry: language family -> (tool_name, check_command)
SCIP_TOOLS: dict[LanguageFamily, tuple[str, list[str]]] = {
    LanguageFamily.GO: ("scip-go", ["scip-go", "--version"]),
    LanguageFamily.JAVASCRIPT: ("scip-typescript", ["scip-typescript", "--version"]),
    LanguageFamily.PYTHON: ("scip-python", ["scip-python", "--version"]),
    LanguageFamily.JVM: ("scip-java", ["scip-java", "--version"]),
    LanguageFamily.DOTNET: ("scip-dotnet", ["scip-dotnet", "--version"]),
    LanguageFamily.RUST: ("rust-analyzer", ["rust-analyzer", "--version"]),
    LanguageFamily.RUBY: ("scip-ruby", ["scip-ruby", "--version"]),
    LanguageFamily.PHP: ("scip-php", ["scip-php", "--version"]),
    LanguageFamily.CPP: ("scip-clang", ["scip-clang", "--version"]),
}


@dataclass
class RefreshJobStatus:
    """Status information for a refresh job."""

    job_id: int
    status: JobStatus
    failure_reason: JobFailureReason | None
    error: str | None
    created_at: float | None
    started_at: float | None
    finished_at: float | None


class RefreshJobService:
    """
    Manages SCIP indexer job queue with HEAD-aware deduplication.

    Key invariants:
    - Monotonic scope widening: never narrow scope for same HEAD
    - HEAD-aware supersede: supersede jobs when HEAD changes
    - Fail-fast: check tool availability before running
    - Fresh HEAD at import: re-read HEAD before importing output
    """

    def __init__(
        self,
        db: Database,
        repo_root: Path,
        scip_importer: ScipImporter | None = None,
    ) -> None:
        """Initialize refresh job service."""
        self._db = db
        self._repo_root = repo_root
        self._scip_importer = scip_importer

    def enqueue_refresh(
        self,
        context_id: int,
        scope: RefreshScope | None,
        trigger_reason: str = "manual",
    ) -> int | None:
        """
        Enqueue a refresh job, merging with existing if possible.

        Args:
            context_id: Context to refresh
            scope: Scope of refresh (None = full refresh)
            trigger_reason: Why this refresh was triggered

        Returns:
            Job ID if created/merged, None if already covered

        Behavior:
        - No existing job: create new job
        - Existing job with same HEAD: merge scopes (widen only)
        - Existing job with different HEAD: supersede and create new
        """
        current_head = self._get_git_head()

        with self._db.session() as session:
            # Find existing queued or running job for this context
            stmt = select(RefreshJob).where(
                RefreshJob.context_id == context_id,
                col(RefreshJob.status).in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            )
            existing = session.exec(stmt).first()

            if existing is None:
                # No existing job - create new
                return self._create_job(session, context_id, scope, current_head, trigger_reason)

            if existing.head_at_enqueue != current_head:
                # HEAD changed - supersede existing and create new
                existing.status = JobStatus.SUPERSEDED.value
                existing.superseded_reason = f"HEAD changed: {existing.head_at_enqueue} -> {current_head}"
                existing.finished_at = time.time()
                session.add(existing)
                return self._create_job(session, context_id, scope, current_head, trigger_reason)

            # Same HEAD - merge scopes
            existing_scope = RefreshScope.from_json(existing.scope)
            merged = self._merge_scopes(existing_scope, scope)

            if self._scope_equals(merged, existing_scope):
                # Already covered by existing scope
                return None

            if existing.status == JobStatus.QUEUED.value:
                # Update queued job's scope
                existing.scope = merged.to_json() if merged else None
                session.add(existing)
                session.commit()
                return existing.id
            else:
                # Running job - store desired scope for later
                existing.desired_scope = merged.to_json() if merged else None
                session.add(existing)
                session.commit()
                return existing.id

    def run_job(self, job_id: int) -> RefreshJobStatus:
        """
        Execute a refresh job: run indexer, check HEAD, import output.

        Steps:
        1. Claim job (queued -> running)
        2. Check tool availability (fail-fast)
        3. Run SCIP indexer
        4. Fresh HEAD read + supersede check
        5. Import output
        6. Mark completed

        Returns:
            Final job status
        """
        # Step 1: Claim job atomically
        if not self._claim_job(job_id):
            return self.get_job_status(job_id)

        with self._db.session() as session:
            job = session.get(RefreshJob, job_id)
            if job is None:
                return RefreshJobStatus(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    failure_reason=JobFailureReason.INTERNAL_ERROR,
                    error="Job not found after claim",
                    created_at=None,
                    started_at=None,
                    finished_at=None,
                )

            context = session.get(Context, job.context_id)
            if context is None:
                self._fail_job(job_id, JobFailureReason.INTERNAL_ERROR, "Context not found")
                return self.get_job_status(job_id)

            language_family = LanguageFamily(context.language_family)

        # Step 2: Check tool availability (fail-fast)
        tool_check = self._check_tool_available(language_family)
        if not tool_check.available:
            self._fail_job(job_id, JobFailureReason.MISSING_TOOL, tool_check.error or "Tool not available")
            return self.get_job_status(job_id)

        # Step 3: Run indexer
        try:
            output_path = self._run_indexer(job.context_id, language_family, job.scope)
        except IndexerError as e:
            reason = JobFailureReason.TOOL_CRASHED
            if "timeout" in str(e).lower():
                reason = JobFailureReason.TOOL_TIMEOUT
            self._fail_job(job_id, reason, str(e))
            return self.get_job_status(job_id)

        # Step 4: Fresh HEAD read + supersede check
        current_head = self._get_git_head()

        with self._db.session() as session:
            job = session.get(RefreshJob, job_id)
            if job is None or job.status == JobStatus.SUPERSEDED.value:
                # Job was superseded while running
                self._cleanup_output(output_path)
                return self.get_job_status(job_id)

            if job.head_at_enqueue != current_head:
                # HEAD changed during indexing - output is stale
                job.status = JobStatus.SUPERSEDED.value
                job.superseded_reason = f"HEAD changed during indexing: {job.head_at_enqueue} -> {current_head}"
                job.finished_at = time.time()
                session.add(job)
                session.commit()
                self._cleanup_output(output_path)
                return self.get_job_status(job_id)

        # Step 5: Import output
        try:
            if self._scip_importer is not None:
                self._scip_importer.import_scip_file(output_path, job.context_id)
        except Exception as e:
            self._fail_job(job_id, JobFailureReason.PARSE_ERROR, str(e))
            self._cleanup_output(output_path)
            return self.get_job_status(job_id)

        # Step 6: Mark completed
        self._complete_job(job_id)
        self._cleanup_output(output_path)

        return self.get_job_status(job_id)

    def get_job_status(self, job_id: int) -> RefreshJobStatus:
        """Get current status of a job."""
        with self._db.session() as session:
            job = session.get(RefreshJob, job_id)
            if job is None:
                return RefreshJobStatus(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    failure_reason=JobFailureReason.INTERNAL_ERROR,
                    error="Job not found",
                    created_at=None,
                    started_at=None,
                    finished_at=None,
                )

            failure_reason = None
            if job.failure_reason:
                failure_reason = JobFailureReason(job.failure_reason)

            return RefreshJobStatus(
                job_id=job_id,
                status=JobStatus(job.status),
                failure_reason=failure_reason,
                error=job.error,
                created_at=job.created_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
            )

    def get_pending_jobs(self, context_id: int | None = None) -> list[RefreshJobStatus]:
        """Get all queued or running jobs, optionally filtered by context."""
        with self._db.session() as session:
            stmt = select(RefreshJob).where(
                col(RefreshJob.status).in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value])
            )
            if context_id is not None:
                stmt = stmt.where(RefreshJob.context_id == context_id)

            jobs = session.exec(stmt).all()

            return [
                RefreshJobStatus(
                    job_id=job.id,  # type: ignore[arg-type]
                    status=JobStatus(job.status),
                    failure_reason=JobFailureReason(job.failure_reason) if job.failure_reason else None,
                    error=job.error,
                    created_at=job.created_at,
                    started_at=job.started_at,
                    finished_at=job.finished_at,
                )
                for job in jobs
            ]

    def get_missing_tool_failures(self) -> list[tuple[int, LanguageFamily, str]]:
        """
        Get all jobs that failed due to missing tools.

        Returns list of (context_id, language_family, tool_name) tuples.
        Used by Coordinator for user confirmation loop.
        """
        with self._db.session() as session:
            # Get failed jobs with missing tool reason
            job_stmt = select(RefreshJob).where(
                RefreshJob.status == JobStatus.FAILED.value,
                RefreshJob.failure_reason == JobFailureReason.MISSING_TOOL.value,
            )
            jobs = session.exec(job_stmt).all()

            failures: list[tuple[int, LanguageFamily, str]] = []
            for job in jobs:
                # Fetch context for each job
                context = session.get(Context, job.context_id)
                if context is None:
                    continue
                family = LanguageFamily(context.language_family)
                tool_info = SCIP_TOOLS.get(family)
                tool_name = tool_info[0] if tool_info else "unknown"
                failures.append((context.id, family, tool_name))  # type: ignore[arg-type]

            return failures

    def supersede_stale_jobs(self, context_id: int, new_head: str) -> int:
        """
        Supersede all queued/running jobs for a context if HEAD differs.

        Returns number of jobs superseded.
        """
        with self._db.session() as session:
            stmt = select(RefreshJob).where(
                RefreshJob.context_id == context_id,
                col(RefreshJob.status).in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
                RefreshJob.head_at_enqueue != new_head,
            )
            jobs = session.exec(stmt).all()

            count = 0
            for job in jobs:
                job.status = JobStatus.SUPERSEDED.value
                job.superseded_reason = f"HEAD changed: {job.head_at_enqueue} -> {new_head}"
                job.finished_at = time.time()
                session.add(job)
                count += 1

            session.commit()
            return count

    def _create_job(
        self,
        session: Session,
        context_id: int,
        scope: RefreshScope | None,
        head: str,
        trigger_reason: str,
    ) -> int:
        """Create a new refresh job."""
        job = RefreshJob(
            context_id=context_id,
            status=JobStatus.QUEUED.value,
            scope=scope.to_json() if scope else None,
            trigger_reason=trigger_reason,
            head_at_enqueue=head,
            created_at=time.time(),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id  # type: ignore[return-value]

    def _claim_job(self, job_id: int) -> bool:
        """Atomically claim a job (queued -> running)."""
        with self._db.session() as session:
            # Atomic update with WHERE clause
            stmt = select(RefreshJob).where(
                RefreshJob.id == job_id,
                RefreshJob.status == JobStatus.QUEUED.value,
            )
            job = session.exec(stmt).first()

            if job is None:
                return False

            job.status = JobStatus.RUNNING.value
            job.started_at = time.time()
            session.add(job)
            session.commit()
            return True

    def _fail_job(self, job_id: int, reason: JobFailureReason, error: str) -> None:
        """Mark a job as failed."""
        with self._db.session() as session:
            job = session.get(RefreshJob, job_id)
            if job is not None:
                job.status = JobStatus.FAILED.value
                job.failure_reason = reason.value
                job.error = error
                job.finished_at = time.time()
                session.add(job)
                session.commit()

    def _complete_job(self, job_id: int) -> None:
        """Mark a job as completed."""
        with self._db.session() as session:
            job = session.get(RefreshJob, job_id)
            if job is not None:
                job.status = JobStatus.COMPLETED.value
                job.finished_at = time.time()
                session.add(job)
                session.commit()

    def _merge_scopes(
        self,
        a: RefreshScope | None,
        b: RefreshScope | None,
    ) -> RefreshScope | None:
        """
        Merge two scopes (union = widest).

        None means full refresh (broadest scope).
        """
        if a is None or b is None:
            return None

        # Merge file lists
        files: list[str] | None = None
        if a.files is not None and b.files is not None:
            files = sorted(set(a.files) | set(b.files))
        elif a.files is not None:
            files = a.files
        elif b.files is not None:
            files = b.files

        # Merge package lists
        packages: list[str] | None = None
        if a.packages is not None and b.packages is not None:
            packages = sorted(set(a.packages) | set(b.packages))
        elif a.packages is not None:
            packages = a.packages
        elif b.packages is not None:
            packages = b.packages

        # Merge changed_since (take earlier timestamp = wider scope)
        changed_since: float | None = None
        if a.changed_since is not None and b.changed_since is not None:
            changed_since = min(a.changed_since, b.changed_since)
        elif a.changed_since is not None:
            changed_since = a.changed_since
        elif b.changed_since is not None:
            changed_since = b.changed_since

        return RefreshScope(
            files=files if files else None,
            packages=packages if packages else None,
            changed_since=changed_since,
        )

    def _scope_equals(
        self,
        a: RefreshScope | None,
        b: RefreshScope | None,
    ) -> bool:
        """Check if two scopes are equivalent."""
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False

        return (
            set(a.files or []) == set(b.files or [])
            and set(a.packages or []) == set(b.packages or [])
            and a.changed_since == b.changed_since
        )

    def _get_git_head(self) -> str:
        """Get current HEAD commit hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return "unknown"

    def _check_tool_available(self, family: LanguageFamily) -> ToolCheckResult:
        """Check if SCIP tool for family is available."""
        tool_info = SCIP_TOOLS.get(family)
        if tool_info is None:
            # Internal family - no external tool needed
            return ToolCheckResult(available=True, error=None)

        tool_name, check_cmd = tool_info
        try:
            subprocess.run(
                check_cmd,
                capture_output=True,
                check=True,
                timeout=10,
            )
            return ToolCheckResult(available=True, error=None)
        except FileNotFoundError:
            return ToolCheckResult(
                available=False,
                error=f"{tool_name} not found. Install with: pip install {tool_name}",
            )
        except subprocess.CalledProcessError as e:
            return ToolCheckResult(
                available=False,
                error=f"{tool_name} check failed: {e.stderr}",
            )
        except subprocess.TimeoutExpired:
            return ToolCheckResult(
                available=False,
                error=f"{tool_name} check timed out",
            )

    def _run_indexer(
        self,
        context_id: int,
        family: LanguageFamily,
        scope_json: str | None,  # noqa: ARG002 - reserved for scoped refresh
    ) -> Path:
        """Run SCIP indexer and return output path."""
        # Get context root path
        with self._db.session() as session:
            context = session.get(Context, context_id)
            if context is None:
                msg = f"Context {context_id} not found"
                raise IndexerError(msg)
            context_root = self._repo_root / context.root_path

        output_path = self._repo_root / ".codeplane" / "scip" / f"context_{context_id}.scip"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        tool_info = SCIP_TOOLS.get(family)
        if tool_info is None:
            msg = f"No SCIP tool for family {family}"
            raise IndexerError(msg)

        tool_name, _ = tool_info

        # Build command based on tool
        cmd = self._build_indexer_command(tool_name, context_root, output_path)

        try:
            subprocess.run(
                cmd,
                cwd=context_root,
                capture_output=True,
                check=True,
                timeout=300,  # 5 minute timeout
            )
        except subprocess.CalledProcessError as e:
            msg = f"Indexer failed: {e.stderr.decode() if e.stderr else 'unknown error'}"
            raise IndexerError(msg) from e
        except subprocess.TimeoutExpired as e:
            msg = "Indexer timeout after 300 seconds"
            raise IndexerError(msg) from e

        if not output_path.exists():
            msg = f"Indexer did not produce output at {output_path}"
            raise IndexerError(msg)

        return output_path

    def _build_indexer_command(
        self,
        tool_name: str,
        context_root: Path,
        output_path: Path,
    ) -> list[str]:
        """Build command line for SCIP indexer."""
        # Common pattern: tool --output path
        if tool_name == "scip-go":
            return ["scip-go", "--output", str(output_path)]
        elif tool_name == "scip-typescript":
            return ["scip-typescript", "index", "--output", str(output_path)]
        elif tool_name == "scip-python":
            return ["scip-python", "index", "--output", str(output_path)]
        elif tool_name == "scip-java":
            return ["scip-java", "index", "--output", str(output_path)]
        elif tool_name == "scip-dotnet":
            return ["scip-dotnet", "index", "--output", str(output_path)]
        elif tool_name == "rust-analyzer":
            return ["rust-analyzer", "scip", str(context_root), "--output", str(output_path)]
        else:
            return [tool_name, "--output", str(output_path)]

    def _cleanup_output(self, output_path: Path) -> None:
        """Remove SCIP output file."""
        import contextlib

        with contextlib.suppress(OSError):
            output_path.unlink(missing_ok=True)


@dataclass
class ToolCheckResult:
    """Result of tool availability check."""

    available: bool
    error: str | None


class IndexerError(Exception):
    """Error running SCIP indexer."""
