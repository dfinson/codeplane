"""Testing subsystem core models.

Canonical data structures for test discovery, execution, and results.
All runner packs produce output conforming to these models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# =============================================================================
# Target Kinds - Per ecosystem semantics
# =============================================================================

TargetKind = Literal["file", "package", "project"]
"""Test target granularity.

- file: Single test file (pytest, jest, rspec)
- package: Module/package (Go packages, Rust crates, Java packages)
- project: Project root (dotnet solution, Maven module, Gradle project)
"""


# =============================================================================
# Test Targets
# =============================================================================


@dataclass
class TestTarget:
    """A discovered test target."""

    target_id: str
    selector: str  # Path, package name, or project identifier
    kind: TargetKind
    language: str
    runner_pack_id: str  # e.g., "python.pytest", "js.jest"
    workspace_root: str  # Absolute path to workspace/project root
    estimated_cost: float = 1.0
    test_count: int | None = None

    # Compatibility shim for legacy code
    @property
    def path(self) -> str:
        return self.selector

    @property
    def runner(self) -> str:
        return (
            self.runner_pack_id.split(".")[-1]
            if "." in self.runner_pack_id
            else self.runner_pack_id
        )


# =============================================================================
# Progress Tracking - Separate target vs testcase counters
# =============================================================================


@dataclass
class TargetProgress:
    """Progress at target (file/package/project) level."""

    total: int = 0
    completed: int = 0
    running: int = 0
    failed: int = 0  # Targets that had failures


@dataclass
class TestCaseProgress:
    """Progress at individual test case level."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass
class TestProgress:
    """Combined progress tracking."""

    targets: TargetProgress = field(default_factory=TargetProgress)
    cases: TestCaseProgress = field(default_factory=TestCaseProgress)

    # Legacy compatibility properties
    @property
    def total(self) -> int:
        return self.targets.total

    @property
    def completed(self) -> int:
        return self.targets.completed

    @property
    def passed(self) -> int:
        return self.cases.passed

    @property
    def failed(self) -> int:
        return self.cases.failed

    @property
    def skipped(self) -> int:
        return self.cases.skipped


# =============================================================================
# Test Failures
# =============================================================================


@dataclass
class TestFailure:
    """A single test failure."""

    name: str
    path: str
    line: int | None
    message: str
    traceback: str | None = None
    classname: str | None = None
    duration_seconds: float = 0.0


# =============================================================================
# Test Results - Parsed output from runners
# =============================================================================


@dataclass
class ParsedTestCase:
    """A single test case result."""

    name: str
    classname: str | None
    status: Literal["passed", "failed", "skipped", "error"]
    duration_seconds: float
    message: str | None = None
    traceback: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    file_path: str | None = None
    line_number: int | None = None


@dataclass
class ParsedTestSuite:
    """Parsed test suite result (from a single target)."""

    name: str
    tests: list[ParsedTestCase] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    target_selector: str | None = None
    workspace_root: str | None = None


@dataclass
class ParsedTestRun:
    """Aggregated results from a complete test run (multiple targets)."""

    run_id: str
    suites: list[ParsedTestSuite] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration_seconds: float = 0.0

    def add_suite(self, suite: ParsedTestSuite) -> None:
        self.suites.append(suite)
        self.total += suite.total
        self.passed += suite.passed
        self.failed += suite.failed
        self.skipped += suite.skipped
        self.errors += suite.errors
        self.duration_seconds += suite.duration_seconds


# =============================================================================
# Run Status
# =============================================================================


@dataclass
class TestRunStatus:
    """Status of a test run."""

    run_id: str
    status: Literal["running", "completed", "cancelled", "failed"]
    progress: TestProgress | None = None
    failures: list[TestFailure] = field(default_factory=list)
    duration_seconds: float = 0.0
    artifact_dir: str | None = None  # .codeplane/artifacts/tests/<run_id>/


@dataclass
class TestResult:
    """Result of test operation (discover/run/status/cancel)."""

    action: Literal["discover", "run", "status", "cancel"]
    targets: list[TestTarget] | None = None
    run_status: TestRunStatus | None = None
