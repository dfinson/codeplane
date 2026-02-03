"""Test operations - test_* tools implementation.

Test discovery and execution using runner packs.
Per SPEC.md §23.7 test tool specification.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

# Import packs to trigger registration
from codeplane.testing import packs as _packs  # noqa: F401
from codeplane.testing.models import (
    ParsedTestSuite,
    TargetProgress,
    TestCaseProgress,
    TestFailure,
    TestProgress,
    TestResult,
    TestRunStatus,
    TestTarget,
)
from codeplane.testing.runner_pack import RunnerPack, runner_registry

if TYPE_CHECKING:
    from codeplane.index.ops import IndexCoordinator


# =============================================================================
# Active Run Tracking
# =============================================================================


@dataclass
class ActiveRun:
    """Tracks an active test run."""

    run_id: str
    task: asyncio.Task[TestRunStatus]
    start_time: float
    progress: TestProgress
    failures: list[TestFailure]
    cancel_event: asyncio.Event
    artifact_dir: Path


# =============================================================================
# Workspace Detection
# =============================================================================


@dataclass
class DetectedWorkspace:
    """A detected workspace with its runner pack."""

    root: Path
    pack: RunnerPack
    confidence: float


def detect_workspaces(repo_root: Path) -> list[DetectedWorkspace]:
    """Detect all workspaces and their runners in a repo.

    Supports monorepos by finding nested workspace roots.
    """
    workspaces: list[DetectedWorkspace] = []

    # First check repo root
    for pack_class, confidence in runner_registry.detect_all(repo_root):
        workspaces.append(
            DetectedWorkspace(
                root=repo_root,
                pack=pack_class(),
                confidence=confidence,
            )
        )

    # Check for JS monorepo workspaces
    for pkg_json in repo_root.glob("packages/*/package.json"):
        ws_root = pkg_json.parent
        for pack_class, confidence in runner_registry.detect_all(ws_root):
            workspaces.append(
                DetectedWorkspace(
                    root=ws_root,
                    pack=pack_class(),
                    confidence=confidence,
                )
            )

    # Check for pnpm workspaces
    pnpm_ws = repo_root / "pnpm-workspace.yaml"
    if pnpm_ws.exists():
        try:
            import yaml

            data = yaml.safe_load(pnpm_ws.read_text()) or {}
            for pattern in data.get("packages", []):
                for ws_path in repo_root.glob(pattern.replace("/*", "/*")):
                    if ws_path.is_dir() and (ws_path / "package.json").exists():
                        for pack_class, confidence in runner_registry.detect_all(ws_path):
                            workspaces.append(
                                DetectedWorkspace(
                                    root=ws_path,
                                    pack=pack_class(),
                                    confidence=confidence,
                                )
                            )
        except Exception:
            pass

    # Deduplicate by (root, pack_id), keeping highest confidence
    seen: dict[tuple[Path, str], DetectedWorkspace] = {}
    for ws in workspaces:
        key = (ws.root, ws.pack.pack_id)
        if key not in seen or ws.confidence > seen[key].confidence:
            seen[key] = ws

    return list(seen.values())


# =============================================================================
# TestOps - Main Implementation
# =============================================================================


class TestOps:
    """Test discovery and execution operations.

    Uses runner packs for detection-driven execution.
    """

    def __init__(
        self,
        repo_root: Path,
        coordinator: IndexCoordinator,
    ) -> None:
        """Initialize test ops."""
        self._repo_root = repo_root
        self._coordinator = coordinator
        self._active_runs: dict[str, ActiveRun] = {}
        self._artifacts_base = repo_root / ".codeplane" / "artifacts" / "tests"

    async def discover(
        self,
        paths: list[str] | None = None,
    ) -> TestResult:
        """Discover test targets in the repository.

        Uses runner packs for accurate detection per workspace.
        """
        all_targets: list[TestTarget] = []
        workspaces = detect_workspaces(self._repo_root)

        for ws in workspaces:
            try:
                targets = await ws.pack.discover(ws.root)
                # Filter by paths if specified
                if paths:
                    targets = [
                        t
                        for t in targets
                        if any(t.selector.startswith(p) or p.startswith(t.selector) for p in paths)
                    ]
                all_targets.extend(targets)
            except Exception:
                # Pack discovery failed, skip
                continue

        # Deduplicate targets
        seen: set[str] = set()
        unique_targets: list[TestTarget] = []
        for t in all_targets:
            if t.target_id not in seen:
                seen.add(t.target_id)
                unique_targets.append(t)

        return TestResult(action="discover", targets=unique_targets)

    async def run(
        self,
        targets: list[str] | None = None,
        *,
        pattern: str | None = None,
        tags: list[str] | None = None,
        failed_only: bool = False,  # noqa: ARG002
        parallelism: int | None = None,
        timeout_sec: int | None = None,
        fail_fast: bool = False,
    ) -> TestResult:
        """Run tests using runner packs."""
        run_id = str(uuid.uuid4())[:8]
        cancel_event = asyncio.Event()

        # Create artifact directory
        artifact_dir = self._artifacts_base / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        progress = TestProgress(
            targets=TargetProgress(),
            cases=TestCaseProgress(),
        )
        failures: list[TestFailure] = []

        # Resolve targets
        if targets:
            # Parse target IDs to get actual targets
            discover_result = await self.discover()
            target_map = {t.target_id: t for t in (discover_result.targets or [])}
            resolved_targets = [target_map[tid] for tid in targets if tid in target_map]
        else:
            discover_result = await self.discover()
            resolved_targets = discover_result.targets or []

        progress.targets.total = len(resolved_targets)

        # Create task for execution
        task = asyncio.create_task(
            self._execute_tests(
                run_id=run_id,
                targets=resolved_targets,
                progress=progress,
                failures=failures,
                cancel_event=cancel_event,
                artifact_dir=artifact_dir,
                pattern=pattern,
                tags=tags,
                parallelism=parallelism or 4,
                timeout_sec=timeout_sec or 300,
                fail_fast=fail_fast,
            )
        )

        self._active_runs[run_id] = ActiveRun(
            run_id=run_id,
            task=task,
            start_time=time.time(),
            progress=progress,
            failures=failures,
            cancel_event=cancel_event,
            artifact_dir=artifact_dir,
        )

        return TestResult(
            action="run",
            run_status=TestRunStatus(
                run_id=run_id,
                status="running",
                progress=progress,
                artifact_dir=str(artifact_dir),
            ),
        )

    async def _execute_tests(
        self,
        run_id: str,
        targets: list[TestTarget],
        progress: TestProgress,
        failures: list[TestFailure],
        cancel_event: asyncio.Event,
        artifact_dir: Path,
        pattern: str | None,
        tags: list[str] | None,
        parallelism: int,
        timeout_sec: int,
        fail_fast: bool,
    ) -> TestRunStatus:
        """Execute tests grouped by runner pack."""
        start_time = time.time()

        # Group targets by (workspace_root, runner_pack_id)
        groups: dict[tuple[str, str], list[TestTarget]] = {}
        for target in targets:
            key = (target.workspace_root, target.runner_pack_id)
            groups.setdefault(key, []).append(target)

        # Create semaphore for parallelism
        sem = asyncio.Semaphore(parallelism)

        async def run_target(target: TestTarget) -> ParsedTestSuite | None:
            if cancel_event.is_set():
                return None
            async with sem:
                return await self._run_single_target(
                    target=target,
                    artifact_dir=artifact_dir,
                    pattern=pattern,
                    tags=tags,
                    timeout_sec=timeout_sec,
                )

        # Run each group
        for (_ws_root, _pack_id), group_targets in groups.items():
            if cancel_event.is_set():
                break

            tasks = [asyncio.create_task(run_target(t)) for t in group_targets]

            for coro in asyncio.as_completed(tasks):
                if cancel_event.is_set() or (fail_fast and progress.cases.failed > 0):
                    for t in tasks:
                        t.cancel()
                    break

                result = await coro
                if result:
                    progress.targets.completed += 1
                    progress.cases.passed += result.passed
                    progress.cases.failed += result.failed
                    progress.cases.skipped += result.skipped
                    progress.cases.errors += result.errors
                    progress.cases.total += result.total

                    if result.failed > 0 or result.errors > 0:
                        progress.targets.failed += 1

                    for test in result.tests:
                        if test.status in ("failed", "error"):
                            failures.append(
                                TestFailure(
                                    name=test.name,
                                    path=test.file_path or test.classname or "",
                                    line=test.line_number,
                                    message=test.message or "Test failed",
                                    traceback=test.traceback,
                                    classname=test.classname,
                                    duration_seconds=test.duration_seconds,
                                )
                            )

        duration = time.time() - start_time
        status: Literal["running", "completed", "cancelled", "failed"] = (
            "cancelled" if cancel_event.is_set() else "completed"
        )

        if run_id in self._active_runs:
            del self._active_runs[run_id]

        return TestRunStatus(
            run_id=run_id,
            status=status,
            progress=progress,
            failures=failures,
            duration_seconds=duration,
            artifact_dir=str(artifact_dir),
        )

    async def _run_single_target(
        self,
        target: TestTarget,
        artifact_dir: Path,
        pattern: str | None,
        tags: list[str] | None,
        timeout_sec: int,
    ) -> ParsedTestSuite:
        """Run a single test target using its runner pack."""
        pack_class = runner_registry.get(target.runner_pack_id)
        if not pack_class:
            return ParsedTestSuite(name=target.selector, errors=1)

        pack = pack_class()

        # Create output file path
        safe_name = target.target_id.replace("/", "_").replace(":", "_")
        output_path = artifact_dir / f"{safe_name}.xml"

        # Build command
        cmd = pack.build_command(
            target,
            output_path=output_path,
            pattern=pattern,
            tags=tags,
        )

        if not cmd:
            return ParsedTestSuite(name=target.selector, errors=1)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=pack.get_cwd(target),
            )
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            stdout = stdout_bytes.decode(errors="replace")

            # Write stdout to artifact
            stdout_path = artifact_dir / f"{safe_name}.stdout.txt"
            stdout_path.write_text(stdout)

            # Parse output
            result = pack.parse_output(output_path, stdout)
            result.target_selector = target.selector
            result.workspace_root = target.workspace_root
            return result

        except TimeoutError:
            return ParsedTestSuite(
                name=target.selector,
                errors=1,
                target_selector=target.selector,
                workspace_root=target.workspace_root,
            )
        except OSError:
            return ParsedTestSuite(
                name=target.selector,
                errors=1,
                target_selector=target.selector,
                workspace_root=target.workspace_root,
            )

    async def status(self, run_id: str) -> TestResult:
        """Get status of a test run."""
        if run_id in self._active_runs:
            active = self._active_runs[run_id]
            duration = time.time() - active.start_time

            if active.task.done():
                try:
                    return TestResult(action="status", run_status=active.task.result())
                except Exception:
                    return TestResult(
                        action="status",
                        run_status=TestRunStatus(run_id=run_id, status="failed"),
                    )

            return TestResult(
                action="status",
                run_status=TestRunStatus(
                    run_id=run_id,
                    status="running",
                    progress=active.progress,
                    failures=active.failures,
                    duration_seconds=duration,
                    artifact_dir=str(active.artifact_dir),
                ),
            )

        return TestResult(
            action="status",
            run_status=TestRunStatus(run_id=run_id, status="completed"),
        )

    async def cancel(self, run_id: str) -> TestResult:
        """Cancel a running test."""
        if run_id in self._active_runs:
            active = self._active_runs[run_id]
            active.cancel_event.set()
            active.task.cancel()

            duration = time.time() - active.start_time
            del self._active_runs[run_id]

            return TestResult(
                action="cancel",
                run_status=TestRunStatus(
                    run_id=run_id,
                    status="cancelled",
                    progress=active.progress,
                    failures=active.failures,
                    duration_seconds=duration,
                    artifact_dir=str(active.artifact_dir),
                ),
            )

        return TestResult(
            action="cancel",
            run_status=TestRunStatus(run_id=run_id, status="cancelled"),
        )
