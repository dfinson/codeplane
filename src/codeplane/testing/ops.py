"""Test operations - test_* tools implementation.

Test discovery and execution.
Per SPEC.md §23.7 test tool specification.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from codeplane.index.ops import IndexCoordinator


@dataclass
class TestTarget:
    """A discovered test target."""

    target_id: str
    path: str
    language: str
    runner: str  # pytest, jest, go test, etc.
    estimated_cost: float  # Relative execution cost
    test_count: int | None = None


@dataclass
class TestProgress:
    """Progress of a test run."""

    total: int
    completed: int
    passed: int
    failed: int
    skipped: int


@dataclass
class TestFailure:
    """A single test failure."""

    name: str
    path: str
    line: int | None
    message: str
    traceback: str | None = None


@dataclass
class TestRunStatus:
    """Status of a test run."""

    run_id: str
    status: Literal["running", "completed", "cancelled", "failed"]
    progress: TestProgress | None = None
    failures: list[TestFailure] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class TestResult:
    """Result of test operation."""

    action: Literal["discover", "run", "status", "cancel"]
    targets: list[TestTarget] | None = None  # For discover
    run_status: TestRunStatus | None = None  # For run/status


class TestOps:
    """Test discovery and execution operations.

    Discovers tests from index, executes via subprocess runners.
    """

    def __init__(
        self,
        repo_root: Path,
        coordinator: IndexCoordinator,
    ) -> None:
        """Initialize test ops.

        Args:
            repo_root: Repository root path
            coordinator: IndexCoordinator for test file discovery
        """
        self._repo_root = repo_root
        self._coordinator = coordinator
        self._active_runs: dict[str, asyncio.Task[TestRunStatus]] = {}

    async def discover(
        self,
        paths: list[str] | None = None,
    ) -> TestResult:
        """Discover test targets in the repository.

        Args:
            paths: Scope discovery to specific paths

        Returns:
            TestResult with discovered targets
        """
        # Use coordinator's map_repo to find test files
        map_result = await self._coordinator.map_repo(include=["test_layout"])

        targets: list[TestTarget] = []
        if map_result.test_layout:
            for test_path in map_result.test_layout.test_files:
                if paths and not any(test_path.startswith(p) for p in paths):
                    continue

                runner = _detect_test_runner(test_path)
                targets.append(
                    TestTarget(
                        target_id=f"test:{test_path}",
                        path=test_path,
                        language=_detect_language(test_path),
                        runner=runner,
                        estimated_cost=1.0,  # TODO: estimate from file size/history
                    )
                )

        return TestResult(action="discover", targets=targets)

    async def run(
        self,
        targets: list[str] | None = None,
        *,
        pattern: str | None = None,
        tags: list[str] | None = None,
        failed_only: bool = False,
        parallelism: int | None = None,
        timeout_sec: int | None = None,
        fail_fast: bool = False,
    ) -> TestResult:
        """Run tests.

        Args:
            targets: Specific targets to run (default all)
            pattern: Test name pattern filter
            tags: Test tags/markers filter
            failed_only: Re-run only failures
            parallelism: Worker count (default auto)
            timeout_sec: Per-target timeout
            fail_fast: Stop on first failure

        Returns:
            TestResult with run_id for status tracking
        """
        run_id = str(uuid.uuid4())[:8]

        # TODO: Implement actual test execution
        # 1. Resolve targets to files
        # 2. Group by runner (pytest, jest, etc.)
        # 3. Execute in subprocess with streaming output
        # 4. Collect results

        run_status = TestRunStatus(
            run_id=run_id,
            status="running",
            progress=TestProgress(
                total=len(targets) if targets else 0,
                completed=0,
                passed=0,
                failed=0,
                skipped=0,
            ),
        )

        return TestResult(action="run", run_status=run_status)

    async def status(self, run_id: str) -> TestResult:
        """Get status of a test run.

        Args:
            run_id: Run ID from run() result

        Returns:
            TestResult with current status
        """
        # TODO: Look up active run and return status
        return TestResult(
            action="status",
            run_status=TestRunStatus(
                run_id=run_id,
                status="completed",
            ),
        )

    async def cancel(self, run_id: str) -> TestResult:
        """Cancel a running test.

        Args:
            run_id: Run ID to cancel

        Returns:
            TestResult with cancelled status
        """
        if run_id in self._active_runs:
            self._active_runs[run_id].cancel()
            del self._active_runs[run_id]

        return TestResult(
            action="cancel",
            run_status=TestRunStatus(
                run_id=run_id,
                status="cancelled",
            ),
        )


def _detect_test_runner(path: str) -> str:
    """Detect test runner from file path."""
    if path.endswith(".py"):
        return "pytest"
    if path.endswith((".js", ".ts", ".jsx", ".tsx")):
        return "jest"
    if path.endswith(".go"):
        return "go test"
    if path.endswith(".rs"):
        return "cargo test"
    if path.endswith(".java"):
        return "junit"
    return "unknown"


def _detect_language(path: str) -> str:
    """Detect language from file path."""
    suffix = Path(path).suffix
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
    }
    return mapping.get(suffix, "unknown")
