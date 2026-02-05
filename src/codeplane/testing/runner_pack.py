"""Runner pack base classes and registry.

Runner packs are first-class plugins that define how to detect, discover,
run, and parse tests for a specific language/framework combination.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from codeplane.testing.models import ParsedTestSuite, TargetKind, TestTarget
    from codeplane.testing.runtime import RuntimeExecutionContext


# =============================================================================
# Runner Pack Configuration
# =============================================================================


@dataclass
class MarkerRule:
    """A marker file/pattern that indicates a runner."""

    pattern: str  # File pattern (glob supported)
    content_match: str | None = None  # Optional content to check
    confidence: Literal["high", "medium", "low"] = "medium"


@dataclass
class OutputStrategy:
    """How the runner produces machine-readable output."""

    format: Literal["junit_xml", "json", "ndjson", "tap", "coarse"]
    file_based: bool = True  # Output to file vs stdout
    file_pattern: str | None = None  # e.g., "junit.xml" or "*.xml"


@dataclass
class RunnerCapabilities:
    """What the runner pack supports."""

    supported_kinds: list[TargetKind] = field(default_factory=lambda: ["file"])
    supports_pattern_filter: bool = True
    supports_tag_filter: bool = False
    supports_parallel: bool = True
    supports_junit_output: bool = True


# =============================================================================
# Runner Pack Base Class
# =============================================================================


class RunnerPack(abc.ABC):
    """Base class for runner packs.

    Each runner pack defines:
    - How to detect if this runner is used in a workspace
    - How to discover test targets
    - How to run tests and produce machine-readable output
    - How to parse the output
    """

    # Pack identity
    pack_id: str  # e.g., "python.pytest", "js.jest"
    language: str
    runner_name: str
    markers: list[MarkerRule]
    output_strategy: OutputStrategy
    capabilities: RunnerCapabilities

    @abc.abstractmethod
    def detect(self, workspace_root: Path) -> float:
        """Check if this runner is used in the workspace.

        Returns confidence score 0.0-1.0 (0 = not detected).
        """

    @abc.abstractmethod
    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        """Discover test targets in the workspace."""

    @abc.abstractmethod
    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
        exec_ctx: RuntimeExecutionContext | None = None,
    ) -> list[str]:
        """Build command to run tests for a target.

        Args:
            target: The test target to run
            output_path: Path to write machine-readable output
            pattern: Test name pattern filter
            tags: Test tags/markers filter
            exec_ctx: Execution context with runtime info (Python executable, etc.)
                      If None, falls back to PATH-based detection.

        Returns:
            Command as list of strings
        """

    @abc.abstractmethod
    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:
        """Parse test output into canonical format.

        Args:
            output_path: Path where machine-readable output was written
            stdout: Raw stdout from the test run

        Returns:
            Parsed test suite
        """

    def get_cwd(self, target: TestTarget) -> Path:
        """Get working directory for running tests."""
        return Path(target.workspace_root)


# =============================================================================
# Runner Pack Registry
# =============================================================================


class RunnerPackRegistry:
    """Registry of available runner packs."""

    def __init__(self) -> None:
        self._packs: dict[str, type[RunnerPack]] = {}

    def register(self, pack_class: type[RunnerPack]) -> type[RunnerPack]:
        """Register a runner pack class."""
        # Create instance to get pack_id
        # We use class attributes, so we can access them without instantiation
        pack_id = pack_class.pack_id
        self._packs[pack_id] = pack_class
        return pack_class

    def get(self, pack_id: str) -> type[RunnerPack] | None:
        """Get a runner pack class by ID."""
        return self._packs.get(pack_id)

    def all(self) -> list[type[RunnerPack]]:
        """Get all registered runner packs."""
        return list(self._packs.values())

    def for_language(self, language: str) -> list[type[RunnerPack]]:
        """Get all runner packs for a language."""
        return [p for p in self._packs.values() if p.language == language]

    def detect_all(self, workspace_root: Path) -> list[tuple[type[RunnerPack], float]]:
        """Detect which packs apply to a workspace.

        Returns list of (pack_class, confidence) sorted by confidence descending.
        """
        results: list[tuple[type[RunnerPack], float]] = []
        for pack_class in self._packs.values():
            pack = pack_class()
            confidence = pack.detect(workspace_root)
            if confidence > 0:
                results.append((pack_class, confidence))
        return sorted(results, key=lambda x: -x[1])


# Global registry instance
runner_registry = RunnerPackRegistry()
