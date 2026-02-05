"""Test operations - test_* tools implementation.

Test discovery and execution using runner packs.
Per SPEC.md §23.7 test tool specification.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from codeplane.index._internal.ignore import PRUNABLE_DIRS

# Import packs to trigger registration
from codeplane.testing import packs as _packs  # noqa: F401
from codeplane.testing.coverage import (
    CoverageArtifact,
    CoverageCapability,
    PackRuntime,
    get_emitter,
)
from codeplane.testing.models import (
    ExecutionContext,
    ExecutionDiagnostic,
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
from codeplane.testing.runtime import (
    ExecutionContextBuilder,
    RuntimeExecutionContext,
)
from codeplane.testing.safe_execution import SafeExecutionConfig, SafeExecutionContext

if TYPE_CHECKING:
    from codeplane.index.ops import IndexCoordinator


# =============================================================================
# Environment Detection
# =============================================================================


def detect_python_venv(workspace_root: Path) -> Path | None:
    """Detect Python virtual environment in workspace."""
    # Check common venv locations
    for venv_name in [".venv", "venv", ".env", "env"]:
        venv_path = workspace_root / venv_name
        if venv_path.is_dir():
            # Verify it's a venv by checking for pyvenv.cfg or activate script
            if (venv_path / "pyvenv.cfg").exists():
                return venv_path
            # Windows style
            if (venv_path / "Scripts" / "activate").exists():
                return venv_path
            # Unix style
            if (venv_path / "bin" / "activate").exists():
                return venv_path
    return None


def get_python_executable(workspace_root: Path) -> str:
    """Get Python executable, preferring venv if present."""
    venv = detect_python_venv(workspace_root)
    if venv:
        # Check for Windows first
        win_python = venv / "Scripts" / "python.exe"
        if win_python.exists():
            return str(win_python)
        # Unix
        unix_python = venv / "bin" / "python"
        if unix_python.exists():
            return str(unix_python)
    return "python"


# Cache for coverage tool detection - keyed by (workspace_root, runner_pack_id)
_coverage_tools_cache: dict[tuple[Path, str], dict[str, bool]] = {}


def clear_coverage_tools_cache() -> None:
    """Clear the coverage tools cache. Useful for testing."""
    _coverage_tools_cache.clear()


def detect_coverage_tools(
    workspace_root: Path,
    runner_pack_id: str,
    exec_ctx: RuntimeExecutionContext | None = None,
) -> dict[str, bool]:
    """Detect available coverage tools for a runner pack.

    Returns a dict of tool_name -> is_available.

    Results are cached per (workspace_root, runner_pack_id) to avoid
    spawning subprocess for every test target.
    """
    cache_key = (workspace_root, runner_pack_id)
    if cache_key in _coverage_tools_cache:
        return _coverage_tools_cache[cache_key]

    tools: dict[str, bool] = {}

    if runner_pack_id == "python.pytest":
        # Check if pytest-cov is installed
        # Use RuntimeExecutionContext if available, otherwise fallback to venv detection
        if exec_ctx and exec_ctx.runtime.python_executable:
            python_exe = exec_ctx.runtime.python_executable
        else:
            python_exe = get_python_executable(workspace_root)

        try:
            import subprocess

            result = subprocess.run(
                [python_exe, "-c", "import pytest_cov"],
                capture_output=True,
                timeout=5,
                cwd=workspace_root,
            )
            tools["pytest-cov"] = result.returncode == 0
        except Exception:
            tools["pytest-cov"] = False

    elif runner_pack_id in ("js.jest", "js.vitest"):
        # Jest and Vitest have built-in coverage
        tools["built-in"] = True

    elif runner_pack_id == "go.gotest":
        # Go has built-in coverage
        tools["built-in"] = True

    elif runner_pack_id in ("rust.nextest", "rust.cargotest"):
        # Check for cargo-llvm-cov
        tools["cargo-llvm-cov"] = shutil.which("cargo-llvm-cov") is not None

    elif runner_pack_id == "ruby.rspec":
        # Check for simplecov in Gemfile
        gemfile = workspace_root / "Gemfile"
        if gemfile.exists():
            tools["simplecov"] = "simplecov" in gemfile.read_text()

    elif runner_pack_id == "php.phpunit":
        # Check for xdebug or pcov
        tools["xdebug"] = shutil.which("php") is not None  # Simplified check
        tools["pcov"] = False  # Would need PHP extension check

    # Cache the result
    _coverage_tools_cache[cache_key] = tools
    return tools


def detect_node_package_manager(workspace_root: Path) -> str:
    """Detect which Node package manager to use."""
    if (workspace_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (workspace_root / "yarn.lock").exists():
        return "yarn"
    if (workspace_root / "bun.lockb").exists():
        return "bun"
    return "npm"


def _default_parallelism() -> int:
    """Compute default parallelism based on CPU count."""
    cpu_count = os.cpu_count() or 4
    # Use 2x CPU count for I/O-bound test execution, capped at reasonable max
    return min(cpu_count * 2, 16)


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


def _is_prunable_path(rel_path: Path) -> bool:
    """Check if relative path contains any prunable directory components.

    Note: 'packages' is in PRUNABLE_DIRS for .NET, but is also a common JS
    monorepo pattern. We only consider a path prunable if it has nested
    prunable dirs or is clearly not a project directory.
    """
    parts = rel_path.parts
    for part in parts:
        # Skip 'packages' at root level since it's commonly used in JS monorepos
        if part == "packages" and parts.index(part) == 0:
            continue
        if part in PRUNABLE_DIRS:
            return True
    return False


def detect_workspaces(repo_root: Path) -> list[DetectedWorkspace]:
    """Detect all workspaces and their runners in a repo.

    Supports monorepos by finding nested workspace roots.
    Respects PRUNABLE_DIRS to avoid scanning .venv, node_modules, etc.
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

    # Collect workspace directories from various monorepo tools
    workspace_dirs: set[Path] = set()

    # Check for yarn/npm workspaces in package.json
    root_pkg = repo_root / "package.json"
    if root_pkg.exists():
        try:
            data = json.loads(root_pkg.read_text())
            workspaces_field = data.get("workspaces", [])
            # Handle both array and object format
            if isinstance(workspaces_field, dict):
                patterns = workspaces_field.get("packages", [])
            else:
                patterns = workspaces_field
            for pattern in patterns:
                # Expand glob patterns
                for ws_path in repo_root.glob(pattern):
                    if (
                        ws_path.is_dir()
                        and not _is_prunable_path(ws_path.relative_to(repo_root))
                        and (ws_path / "package.json").exists()
                    ):
                        workspace_dirs.add(ws_path)
        except Exception:
            pass

    # Check for pnpm workspaces
    pnpm_ws = repo_root / "pnpm-workspace.yaml"
    if pnpm_ws.exists():
        try:
            import yaml

            data = yaml.safe_load(pnpm_ws.read_text()) or {}
            for pattern in data.get("packages", []):
                for ws_path in repo_root.glob(pattern):
                    if (
                        ws_path.is_dir()
                        and not _is_prunable_path(ws_path.relative_to(repo_root))
                        and (ws_path / "package.json").exists()
                    ):
                        workspace_dirs.add(ws_path)
        except Exception:
            pass

    # Check for Nx workspaces
    nx_json = repo_root / "nx.json"
    if nx_json.exists():
        # Nx projects can be in apps/, libs/, packages/
        for subdir in ["apps", "libs", "packages", "projects"]:
            for project_dir in (repo_root / subdir).glob("*"):
                if (
                    project_dir.is_dir()
                    and not _is_prunable_path(project_dir.relative_to(repo_root))
                    and (
                        (project_dir / "package.json").exists()
                        or (project_dir / "project.json").exists()
                    )
                ):
                    workspace_dirs.add(project_dir)

    # Check for Turborepo
    turbo_json = repo_root / "turbo.json"
    if turbo_json.exists():
        # Turbo uses package.json workspaces, already handled above
        # But also check common patterns
        for subdir in ["apps", "packages"]:
            for project_dir in (repo_root / subdir).glob("*"):
                if (
                    project_dir.is_dir()
                    and not _is_prunable_path(project_dir.relative_to(repo_root))
                    and (project_dir / "package.json").exists()
                ):
                    workspace_dirs.add(project_dir)

    # Check for Lerna
    lerna_json = repo_root / "lerna.json"
    if lerna_json.exists():
        try:
            data = json.loads(lerna_json.read_text())
            for pattern in data.get("packages", ["packages/*"]):
                for ws_path in repo_root.glob(pattern):
                    if (
                        ws_path.is_dir()
                        and not _is_prunable_path(ws_path.relative_to(repo_root))
                        and (ws_path / "package.json").exists()
                    ):
                        workspace_dirs.add(ws_path)
        except Exception:
            pass

    # Check for Rush
    rush_json = repo_root / "rush.json"
    if rush_json.exists():
        try:
            data = json.loads(rush_json.read_text())
            for project in data.get("projects", []):
                project_folder = project.get("projectFolder")
                if project_folder:
                    ws_path = repo_root / project_folder
                    if ws_path.is_dir():
                        workspace_dirs.add(ws_path)
        except Exception:
            pass

    # Legacy: Check for packages/* pattern (fallback)
    for pkg_json in repo_root.glob("packages/*/package.json"):
        if not _is_prunable_path(pkg_json.parent.relative_to(repo_root)):
            workspace_dirs.add(pkg_json.parent)

    # Detect runners in each workspace
    # Note: workspace_dirs comes from intentional workspace detection (package.json workspaces,
    # monorepo configs, etc.) so we don't re-filter them. The prunable path check was already
    # applied during collection where appropriate.
    for ws_root in workspace_dirs:
        for pack_class, confidence in runner_registry.detect_all(ws_root):
            workspaces.append(
                DetectedWorkspace(
                    root=ws_root,
                    pack=pack_class(),
                    confidence=confidence,
                )
            )

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
    Leverages the index for context-aware workspace detection.
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

        Index-first approach: Always queries the index. The index waits for
        freshness internally (via coordinator.wait_for_freshness). No filesystem
        fallback - if index isn't ready, we block until it is.

        Args:
            paths: Optional list of path prefixes to filter targets

        Returns:
            TestResult with discovered targets
        """
        from typing import cast

        from codeplane.testing.models import TargetKind

        # Query index - coordinator.get_test_targets waits for freshness internally
        indexed_targets = await self._coordinator.get_test_targets()

        all_targets = [
            TestTarget(
                target_id=t.target_id,
                selector=t.selector,
                kind=cast(TargetKind, t.kind),
                language=t.language,
                runner_pack_id=t.runner_pack_id,
                workspace_root=t.workspace_root,
                estimated_cost=1.0,
                test_count=t.test_count,
            )
            for t in indexed_targets
        ]

        # Filter by paths if specified
        if paths:
            all_targets = [
                t
                for t in all_targets
                if any(t.selector.startswith(p) or p.startswith(t.selector) for p in paths)
            ]

        # Generate agentic hint if no targets found
        agentic_hint = None
        if not all_targets:
            agentic_hint = await self._generate_agentic_hint()

        return TestResult(action="discover", targets=all_targets, agentic_hint=agentic_hint)

    async def _discover_from_filesystem(
        self,
        paths: list[str] | None = None,
    ) -> TestResult:
        """Fallback filesystem-based discovery."""
        all_targets: list[TestTarget] = []

        # Use index contexts to find workspaces (leverages already-indexed data)
        workspaces = await self._detect_workspaces_from_index()

        # If index doesn't have contexts yet, fall back to filesystem detection
        if not workspaces:
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

        # If no targets found, provide agentic fallback
        agentic_hint = None
        if not unique_targets:
            agentic_hint = await self._generate_agentic_hint()

        return TestResult(action="discover", targets=unique_targets, agentic_hint=agentic_hint)

    async def _detect_workspaces_from_index(self) -> list[DetectedWorkspace]:
        """Detect workspaces using index contexts.

        The index already knows about project contexts (Python packages,
        JS projects, Go modules, etc.) - leverage that instead of re-scanning.
        """
        workspaces: list[DetectedWorkspace] = []

        try:
            contexts = await self._coordinator.get_contexts()
        except Exception:
            # Index not ready, return empty to trigger filesystem fallback
            return []

        # Group contexts by root path to find workspaces
        roots_seen: set[str] = set()

        for ctx in contexts:
            root_path = ctx.root_path or ""
            if root_path in roots_seen:
                continue
            roots_seen.add(root_path)

            # Resolve workspace path
            ws_root = self._repo_root / root_path if root_path else self._repo_root

            # Detect runners for this workspace
            for pack_class, confidence in runner_registry.detect_all(ws_root):
                workspaces.append(
                    DetectedWorkspace(
                        root=ws_root,
                        pack=pack_class(),
                        confidence=confidence,
                    )
                )

        # Deduplicate by (root, pack_id), keeping highest confidence
        seen: dict[tuple[Path, str], DetectedWorkspace] = {}
        for ws in workspaces:
            key = (ws.root, ws.pack.pack_id)
            if key not in seen or ws.confidence > seen[key].confidence:
                seen[key] = ws

        return list(seen.values())

    async def _get_targets_by_id(self, target_ids: list[str]) -> list[TestTarget]:
        """Get test targets by ID from the index.

        Index-first: waits for index freshness, does not fallback.
        """
        from typing import cast

        from codeplane.testing.models import TargetKind

        # Query index - coordinator.get_test_targets waits for freshness internally
        indexed_targets = await self._coordinator.get_test_targets(target_ids=target_ids)

        return [
            TestTarget(
                target_id=t.target_id,
                selector=t.selector,
                kind=cast(TargetKind, t.kind),
                language=t.language,
                runner_pack_id=t.runner_pack_id,
                workspace_root=t.workspace_root,
                estimated_cost=1.0,
                test_count=t.test_count,
            )
            for t in indexed_targets
        ]

    async def _get_all_targets_from_index(self) -> list[TestTarget]:
        """Get ALL test targets from the index.

        Index-first approach: This always queries the index. If index is not ready,
        we wait for it (via coordinator.wait_for_freshness). No filesystem fallback.

        Returns:
            List of TestTarget objects from the index
        """
        from typing import cast

        from codeplane.testing.models import TargetKind

        # Query index - coordinator.get_test_targets calls wait_for_freshness internally
        indexed_targets = await self._coordinator.get_test_targets()

        return [
            TestTarget(
                target_id=t.target_id,
                selector=t.selector,
                kind=cast(TargetKind, t.kind),
                language=t.language,
                runner_pack_id=t.runner_pack_id,
                workspace_root=t.workspace_root,
                estimated_cost=1.0,
                test_count=t.test_count,
            )
            for t in indexed_targets
        ]

    async def _generate_agentic_hint(self) -> str:
        """Generate agentic hint for running tests when no targets detected."""
        hints: list[str] = []

        # Get languages from index
        try:
            file_stats = await self._coordinator.get_file_stats()
            languages = set(file_stats.keys())
        except Exception:
            languages = set()

        if "python" in languages:
            hints.append("Python: Run `pytest` or `python -m pytest`")
        if "javascript" in languages:
            hints.append("JavaScript: Run `npm test`, `yarn test`, or `jest`")
        if "go" in languages:
            hints.append("Go: Run `go test ./...`")
        if "rust" in languages:
            hints.append("Rust: Run `cargo test`")
        if "jvm" in languages:
            hints.append("Java/Kotlin: Run `./gradlew test` or `mvn test`")
        if "ruby" in languages:
            hints.append("Ruby: Run `bundle exec rspec` or `rake test`")
        if "dotnet" in languages:
            hints.append("C#/.NET: Run `dotnet test`")
        if "php" in languages:
            hints.append("PHP: Run `phpunit` or `./vendor/bin/phpunit`")
        if "elixir" in languages:
            hints.append("Elixir: Run `mix test`")

        if not hints:
            hints.append(
                "No test framework detected. Check for test files and install "
                "appropriate test runner (pytest, jest, go test, cargo test, etc.)"
            )

        return "No test targets detected automatically. Manual test commands:\n\n" + "\n".join(
            f"  - {h}" for h in hints
        )

    async def run(
        self,
        targets: list[str] | None = None,
        *,
        target_filter: str | None = None,
        test_filter: str | None = None,
        tags: list[str] | None = None,
        failed_only: bool = False,  # noqa: ARG002
        parallelism: int | None = None,
        timeout_sec: int | None = None,
        fail_fast: bool = False,
        coverage: bool = False,
    ) -> TestResult:
        """Run tests using runner packs.

        Args:
            targets: Specific target IDs to run, or None for all
            target_filter: Substring to filter which TARGETS to run by path.
                          Fails explicitly if no targets match.
            test_filter: Filter test NAMES within targets (pytest -k, jest --testNamePattern).
                        Does NOT reduce which targets are executed.
            tags: Test tag filters (pytest markers, etc.)
            failed_only: Only run previously failed tests
            parallelism: Max concurrent test invocations
            timeout_sec: Per-target timeout
            fail_fast: Stop on first failure
            coverage: Enable coverage collection if supported
        """
        # Validate that targets is not an empty list
        if targets is not None and len(targets) == 0:
            return TestResult(
                action="run",
                run_status=TestRunStatus(
                    run_id="",
                    status="failed",
                ),
                agentic_hint="Empty targets list provided. Either omit targets "
                "to run all tests, or specify at least one target. "
                "Use discover_test_targets to find available targets.",
            )

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

        # Resolve targets - query index directly when IDs are provided
        agentic_hint_for_empty: str | None = None
        if targets:
            # Direct index lookup by ID - no filesystem scan
            resolved_targets = await self._get_targets_by_id(targets)
        else:
            # Get all targets from index
            resolved_targets = await self._get_all_targets_from_index()

        # Apply target_filter if provided - FAIL if no matches
        if target_filter and resolved_targets:
            before_count = len(resolved_targets)
            resolved_targets = [
                t
                for t in resolved_targets
                if target_filter in t.selector or target_filter in t.target_id
            ]
            if not resolved_targets:
                return TestResult(
                    action="run",
                    run_status=TestRunStatus(
                        run_id=run_id,
                        status="failed",
                    ),
                    agentic_hint=f"target_filter='{target_filter}' matched 0 of {before_count} targets. "
                    f"Use discover_test_targets to see available target paths. "
                    f"To filter test NAMES within targets, use test_filter instead.",
                )

        # Check if we have any targets to run
        if not resolved_targets:
            return TestResult(
                action="run",
                run_status=TestRunStatus(
                    run_id=run_id,
                    status="completed",
                    progress=progress,
                    artifact_dir=str(artifact_dir),
                ),
                agentic_hint="No test targets found to run. "
                + (
                    agentic_hint_for_empty
                    or "Use discover_test_targets to check available targets."
                ),
            )

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
                test_filter=test_filter,
                tags=tags,
                parallelism=parallelism or _default_parallelism(),
                timeout_sec=timeout_sec or 300,
                fail_fast=fail_fast,
                coverage=coverage,
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
        test_filter: str | None,
        tags: list[str] | None,
        parallelism: int,
        timeout_sec: int,
        fail_fast: bool,
        coverage: bool,
    ) -> TestRunStatus:
        """Execute tests concurrently with semaphore-limited parallelism."""
        start_time = time.time()
        diagnostics: list[ExecutionDiagnostic] = []
        coverage_artifacts: list[CoverageArtifact] = []

        # Create semaphore for parallelism
        sem = asyncio.Semaphore(parallelism)

        async def run_target(
            target: TestTarget,
        ) -> tuple[TestTarget, ParsedTestSuite | None, CoverageArtifact | None]:
            if cancel_event.is_set():
                return (target, None, None)
            async with sem:
                result, cov_artifact = await self._run_single_target(
                    target=target,
                    artifact_dir=artifact_dir,
                    test_filter=test_filter,
                    tags=tags,
                    timeout_sec=timeout_sec,
                    coverage=coverage,
                )
                return (target, result, cov_artifact)

        # Run ALL targets concurrently (semaphore limits parallelism)
        all_tasks = [asyncio.create_task(run_target(t)) for t in targets]

        # Properly drain all coroutines from as_completed to avoid "coroutine never awaited"
        for coro in asyncio.as_completed(all_tasks):
            if cancel_event.is_set() or (fail_fast and progress.cases.failed > 0):
                # Cancel remaining tasks and await them to avoid leaked coroutines
                for t in all_tasks:
                    t.cancel()
                # Drain remaining coroutines from the iterator
                # Each coroutine from as_completed must be awaited even after cancellation
                with contextlib.suppress(asyncio.CancelledError):
                    await coro  # Await current one
                # Continue to drain remaining
                continue

            try:
                target, result, cov_artifact = await coro
            except asyncio.CancelledError:
                # Task was cancelled, skip processing
                continue

            if cov_artifact:
                coverage_artifacts.append(cov_artifact)
            if result:
                progress.targets.completed += 1
                progress.cases.passed += result.passed
                progress.cases.failed += result.failed
                progress.cases.skipped += result.skipped
                progress.cases.errors += result.errors
                progress.cases.total += result.total

                if result.failed > 0 or result.errors > 0:
                    progress.targets.failed += 1

                # Collect execution-level diagnostics (non-test errors)
                if result.error_type != "none":
                    diagnostics.append(
                        ExecutionDiagnostic(
                            target_id=target.target_id,
                            error_type=result.error_type,
                            error_detail=result.error_detail,
                            suggested_action=result.suggested_action,
                            command=result.execution.command if result.execution else None,
                            working_directory=(
                                result.execution.working_directory if result.execution else None
                            ),
                            exit_code=result.execution.exit_code if result.execution else None,
                        )
                    )

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

        # Convert coverage artifacts to serializable dicts
        coverage_dicts = [
            {"format": c.format, "path": str(c.path), "pack_id": c.pack_id}
            for c in coverage_artifacts
        ]

        final_status = TestRunStatus(
            run_id=run_id,
            status=status,
            progress=progress,
            failures=failures,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_dir=str(artifact_dir),
            coverage=coverage_dicts,
            target_selectors=[t.selector for t in targets],
        )

        # Persist result to artifacts for later retrieval
        self._persist_result(artifact_dir, final_status)

        return final_status

    async def _get_execution_context(
        self,
        target: TestTarget,
    ) -> RuntimeExecutionContext | None:
        """Get pre-indexed execution context for a test target.

        Index-first approach: Runtime is captured at discovery time and stored
        in ContextRuntime table. This provides O(1) lookup instead of re-detecting
        venvs/runtimes for every test execution.

        Falls back to PATH-based resolution only if index lookup fails.

        Returns None if resolution fails, allowing graceful fallback to
        PATH-based execution.
        """
        try:
            workspace_root = Path(target.workspace_root)

            # Query indexed runtime (captured at discovery time)
            indexed_runtime = await self._coordinator.get_context_runtime(str(workspace_root))

            if indexed_runtime:
                # Build execution context from indexed runtime
                exec_ctx = ExecutionContextBuilder.build(
                    context_root=workspace_root,
                    runtime=indexed_runtime,
                )
                return exec_ctx

            # Index lookup failed - fall back to PATH-based execution
            # This should rarely happen if indexing is working correctly
            return None

        except Exception:
            # Resolution failed - return None to trigger PATH fallback
            return None

    async def _run_single_target(
        self,
        target: TestTarget,
        artifact_dir: Path,
        test_filter: str | None,
        tags: list[str] | None,
        timeout_sec: int,
        coverage: bool,
    ) -> tuple[ParsedTestSuite, CoverageArtifact | None]:
        """Run a single test target using its runner pack.

        Uses SafeExecutionContext to protect against misconfigurations in
        target repositories (coverage DB corruption, hanging tests, etc.).

        Args:
            target: Test target to run
            artifact_dir: Directory for output files
            test_filter: Filter test names within target (pytest -k, jest --testNamePattern)
            tags: Test tags/markers filter
            timeout_sec: Timeout for the test run
            coverage: Whether to collect coverage

        Returns:
            Tuple of (test results, coverage artifact if collected)
        """
        pack_class = runner_registry.get(target.runner_pack_id)
        if not pack_class:
            return (
                ParsedTestSuite(
                    name=target.selector,
                    errors=1,
                    error_type="unknown",
                    error_detail=f"Runner pack not found: {target.runner_pack_id}",
                    suggested_action="Check that the runner pack is registered",
                    target_selector=target.selector,
                    workspace_root=target.workspace_root,
                ),
                None,
            )

        pack = pack_class()

        # Get pre-indexed execution context (runtime captured at discovery time)
        exec_ctx = await self._get_execution_context(target)

        # Create output file path
        safe_name = target.target_id.replace("/", "_").replace(":", "_")
        output_path = artifact_dir / f"{safe_name}.xml"

        # Build command with execution context (uses correct Python/Node/etc. if available)
        cmd = pack.build_command(
            target,
            output_path=output_path,
            pattern=test_filter,
            tags=tags,
            exec_ctx=exec_ctx,
        )

        if not cmd:
            return (
                ParsedTestSuite(
                    name=target.selector,
                    errors=1,
                    error_type="unknown",
                    error_detail="Runner pack returned empty command",
                    suggested_action="Check target configuration",
                    target_selector=target.selector,
                    workspace_root=target.workspace_root,
                ),
                None,
            )

        # Handle coverage - use pre-indexed capability instead of detecting at runtime
        cov_artifact: CoverageArtifact | None = None
        emitter = get_emitter(target.runner_pack_id) if coverage else None
        coverage_available = False
        if emitter:
            # Get pre-indexed coverage tools from index (O(1) lookup)
            coverage_tools = await self._coordinator.get_coverage_capability(
                target.workspace_root, target.runner_pack_id
            )
            runtime = PackRuntime(
                workspace_root=Path(target.workspace_root),
                runner_available=True,
                coverage_tools=coverage_tools,
            )
            capability = emitter.capability(runtime)
            coverage_available = capability == CoverageCapability.AVAILABLE

        # Create safe execution context to protect against repo misconfigurations
        # strip_coverage_flags=True removes existing coverage flags from the command
        # BEFORE we add our own (so project configs don't interfere)
        safe_ctx = SafeExecutionContext(
            SafeExecutionConfig(
                artifact_dir=artifact_dir,
                workspace_root=Path(target.workspace_root),
                timeout_sec=timeout_sec,
                strip_coverage_flags=coverage_available,
            )
        )

        # Sanitize command FIRST (removes dangerous flags including existing coverage flags)
        cmd = safe_ctx.sanitize_command(cmd, target.runner_pack_id)

        # NOW add our coverage flags after sanitization
        if coverage_available and emitter:
            cmd = emitter.modify_command(cmd, artifact_dir)
            cov_artifact = CoverageArtifact(
                format=emitter.format_id,
                path=emitter.artifact_path(artifact_dir),
                pack_id=target.runner_pack_id,
                invocation_id=target.target_id,
            )

        # Prepare safe environment (overrides project configs to prevent corruption)
        safe_env = safe_ctx.prepare_environment(target.runner_pack_id)

        # Merge execution context environment overrides (from runtime resolution)
        # This includes venv PATH adjustments and any tool-specific env vars
        if exec_ctx:
            runtime_env = exec_ctx.build_env()
            safe_env.update(runtime_env)

        # Verify executable exists
        executable = cmd[0]
        resolved_executable = shutil.which(executable)
        if not resolved_executable:
            safe_ctx.cleanup()
            return (
                ParsedTestSuite(
                    name=target.selector,
                    errors=1,
                    error_type="command_not_found",
                    error_detail=f"Executable not found: {executable}",
                    suggested_action=f"Install {executable} or activate the correct environment",
                    execution=ExecutionContext(
                        command=cmd,
                        working_directory=str(pack.get_cwd(target)),
                    ),
                    target_selector=target.selector,
                    workspace_root=target.workspace_root,
                ),
                None,
            )

        cwd = pack.get_cwd(target)
        stdout = ""
        stderr = ""
        exit_code: int | None = None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=safe_env,  # Use safe environment with defensive overrides
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")
            exit_code = proc.returncode

            # Write artifacts
            stdout_path = artifact_dir / f"{safe_name}.stdout.txt"
            stdout_path.write_text(stdout)
            if stderr:
                stderr_path = artifact_dir / f"{safe_name}.stderr.txt"
                stderr_path.write_text(stderr)

            # Create execution context
            execution = ExecutionContext(
                command=cmd,
                working_directory=str(cwd),
                exit_code=exit_code,
                raw_stdout=stdout,
                raw_stderr=stderr if stderr else None,
            )

            # Parse output
            result = pack.parse_output(output_path, stdout)
            result.target_selector = target.selector
            result.workspace_root = target.workspace_root
            result.execution = execution

            # Classify error type based on result
            if result.errors > 0 and result.total == 0:
                # Parser returned errors with no tests - likely parse failure
                if not output_path.exists() and not stdout.strip():
                    result.error_type = "output_missing"
                    result.error_detail = "No output file or stdout from test runner"
                    result.suggested_action = "Check that the test command produces output"
                elif result.error_type == "none":  # Only set if not already set by parser
                    result.error_type = "parse_failed"
                    result.error_detail = "Could not parse test output"
                    result.suggested_action = "Check the raw output in artifacts"
            elif exit_code and exit_code != 0 and result.failed == 0 and result.errors == 0:
                # Non-zero exit but no failures detected - command crashed
                result.error_type = "command_failed"
                result.error_detail = f"Command exited with code {exit_code}"
                result.suggested_action = "Check stderr for error messages"
                result.errors = 1

            return (result, cov_artifact)

        except TimeoutError:
            safe_ctx.cleanup()
            return (
                ParsedTestSuite(
                    name=target.selector,
                    errors=1,
                    error_type="timeout",
                    error_detail=f"Command timed out after {timeout_sec} seconds",
                    suggested_action="Increase timeout or run fewer tests",
                    execution=ExecutionContext(
                        command=cmd,
                        working_directory=str(cwd),
                        raw_stdout=stdout if stdout else None,
                        raw_stderr=stderr if stderr else None,
                    ),
                    target_selector=target.selector,
                    workspace_root=target.workspace_root,
                ),
                None,
            )
        except OSError as e:
            safe_ctx.cleanup()
            return (
                ParsedTestSuite(
                    name=target.selector,
                    errors=1,
                    error_type="command_failed",
                    error_detail=f"OS error executing command: {e}",
                    suggested_action="Check that the command and working directory are valid",
                    execution=ExecutionContext(
                        command=cmd,
                        working_directory=str(cwd),
                    ),
                    target_selector=target.selector,
                    workspace_root=target.workspace_root,
                ),
                None,
            )
        finally:
            # Always cleanup safe execution context
            safe_ctx.cleanup()

    def _persist_result(self, artifact_dir: Path, status: TestRunStatus) -> None:
        """Persist test run result to artifact directory."""
        result_path = artifact_dir / "result.json"
        result_data = {
            "run_id": status.run_id,
            "status": status.status,
            "duration_seconds": status.duration_seconds,
            "artifact_dir": status.artifact_dir,
            "progress": {
                "targets": {
                    "total": status.progress.targets.total if status.progress else 0,
                    "completed": status.progress.targets.completed if status.progress else 0,
                    "running": status.progress.targets.running if status.progress else 0,
                    "failed": status.progress.targets.failed if status.progress else 0,
                },
                "cases": {
                    "total": status.progress.cases.total if status.progress else 0,
                    "passed": status.progress.cases.passed if status.progress else 0,
                    "failed": status.progress.cases.failed if status.progress else 0,
                    "skipped": status.progress.cases.skipped if status.progress else 0,
                    "errors": status.progress.cases.errors if status.progress else 0,
                },
            }
            if status.progress
            else None,
            "failures": [
                {
                    "name": f.name,
                    "path": f.path,
                    "line": f.line,
                    "message": f.message,
                    "traceback": f.traceback,
                    "classname": f.classname,
                    "duration_seconds": f.duration_seconds,
                }
                for f in (status.failures or [])
            ],
            "diagnostics": [
                {
                    "target_id": d.target_id,
                    "error_type": d.error_type,
                    "error_detail": d.error_detail,
                    "suggested_action": d.suggested_action,
                    "command": d.command,
                    "working_directory": d.working_directory,
                    "exit_code": d.exit_code,
                }
                for d in (status.diagnostics or [])
            ],
            "coverage": status.coverage,
            "target_selectors": status.target_selectors,
        }
        result_path.write_text(json.dumps(result_data, indent=2))

    def _load_result(self, artifact_dir: Path) -> TestRunStatus | None:
        """Load test run result from artifact directory."""
        result_path = artifact_dir / "result.json"
        if not result_path.exists():
            return None

        try:
            data = json.loads(result_path.read_text())
            progress = None
            if data.get("progress"):
                p = data["progress"]
                progress = TestProgress(
                    targets=TargetProgress(
                        total=p["targets"]["total"],
                        completed=p["targets"]["completed"],
                        running=p["targets"]["running"],
                        failed=p["targets"]["failed"],
                    ),
                    cases=TestCaseProgress(
                        total=p["cases"]["total"],
                        passed=p["cases"]["passed"],
                        failed=p["cases"]["failed"],
                        skipped=p["cases"]["skipped"],
                        errors=p["cases"]["errors"],
                    ),
                )

            failures = [
                TestFailure(
                    name=f["name"],
                    path=f["path"],
                    line=f.get("line"),
                    message=f["message"],
                    traceback=f.get("traceback"),
                    classname=f.get("classname"),
                    duration_seconds=f.get("duration_seconds"),
                )
                for f in data.get("failures", [])
            ]

            diagnostics = [
                ExecutionDiagnostic(
                    target_id=d["target_id"],
                    error_type=d["error_type"],
                    error_detail=d.get("error_detail"),
                    suggested_action=d.get("suggested_action"),
                    command=d.get("command"),
                    working_directory=d.get("working_directory"),
                    exit_code=d.get("exit_code"),
                )
                for d in data.get("diagnostics", [])
            ]

            return TestRunStatus(
                run_id=data["run_id"],
                status=data["status"],
                progress=progress,
                failures=failures,
                diagnostics=diagnostics,
                duration_seconds=data.get("duration_seconds"),
                artifact_dir=data.get("artifact_dir"),
                coverage=data.get("coverage", []),
                target_selectors=data.get("target_selectors", []),
            )
        except (json.JSONDecodeError, KeyError):
            return None

    async def status(self, run_id: str) -> TestResult:
        """Get status of a test run."""
        # Check active runs first
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

        # Check for persisted result in artifacts
        artifact_dir = self._artifacts_base / run_id
        if artifact_dir.exists():
            loaded_status = self._load_result(artifact_dir)
            if loaded_status:
                return TestResult(action="status", run_status=loaded_status)

        # Run not found
        return TestResult(
            action="status",
            run_status=TestRunStatus(run_id=run_id, status="not_found"),
            agentic_hint=f"No test run found with ID '{run_id}'. "
            "Use testing_run to start a new test run.",
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
