"""Test operations - test_* tools implementation.

Test discovery and execution using runner packs.
Per SPEC.md §23.7 test tool specification.
"""

from __future__ import annotations

import asyncio
import json
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


def detect_node_package_manager(workspace_root: Path) -> str:
    """Detect which Node package manager to use."""
    if (workspace_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (workspace_root / "yarn.lock").exists():
        return "yarn"
    if (workspace_root / "bun.lockb").exists():
        return "bun"
    return "npm"


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

        Uses index contexts for workspace detection, then runner packs for
        accurate test discovery per workspace. Falls back to agentic hints
        when no runners are detected.
        """
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
        pattern: str | None = None,
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
            pattern: Test name pattern filter
            tags: Test tag filters
            failed_only: Only run previously failed tests
            parallelism: Max concurrent test invocations
            timeout_sec: Per-target timeout
            fail_fast: Stop on first failure
            coverage: Enable coverage collection if supported
        """
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
        pattern: str | None,
        tags: list[str] | None,
        parallelism: int,
        timeout_sec: int,
        fail_fast: bool,
        coverage: bool,
    ) -> TestRunStatus:
        """Execute tests grouped by runner pack."""
        start_time = time.time()
        diagnostics: list[ExecutionDiagnostic] = []
        coverage_artifacts: list[CoverageArtifact] = []

        # Group targets by (workspace_root, runner_pack_id)
        groups: dict[tuple[str, str], list[TestTarget]] = {}
        for target in targets:
            key = (target.workspace_root, target.runner_pack_id)
            groups.setdefault(key, []).append(target)

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
                    pattern=pattern,
                    tags=tags,
                    timeout_sec=timeout_sec,
                    coverage=coverage,
                )
                return (target, result, cov_artifact)

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

                target, result, cov_artifact = await coro
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
        )

        # Persist result to artifacts for later retrieval
        self._persist_result(artifact_dir, final_status)

        return final_status

    async def _run_single_target(
        self,
        target: TestTarget,
        artifact_dir: Path,
        pattern: str | None,
        tags: list[str] | None,
        timeout_sec: int,
        coverage: bool,
    ) -> tuple[ParsedTestSuite, CoverageArtifact | None]:
        """Run a single test target using its runner pack.

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

        # Handle coverage
        cov_artifact: CoverageArtifact | None = None
        emitter = get_emitter(target.runner_pack_id) if coverage else None
        if emitter:
            # Check capability
            runtime = PackRuntime(
                workspace_root=Path(target.workspace_root),
                runner_available=True,
                coverage_tools={},  # TODO: detect coverage tools
            )
            capability = emitter.capability(runtime)
            if capability == CoverageCapability.AVAILABLE:
                cmd = emitter.modify_command(cmd, artifact_dir)
                cov_artifact = CoverageArtifact(
                    format=emitter.format_id,
                    path=emitter.artifact_path(artifact_dir),
                    pack_id=target.runner_pack_id,
                    invocation_id=target.target_id,
                )

        # Verify executable exists
        executable = cmd[0]
        resolved_executable = shutil.which(executable)
        if not resolved_executable:
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
