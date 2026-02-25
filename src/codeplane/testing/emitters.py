"""Coverage emitters for test runner packs.

Coverage is treated as an invocation artifact - each test run that enables
coverage produces files in its artifact directory that agents consume directly.

Design principles:
- No merge: Each invocation writes its own coverage artifact
- No conversion: Native formats preserved, agent reads directly
- Three-state capability: unsupported | available | missing_prereq
- Explicit support: Only packs with tested emitters claim coverage support
"""

import logging
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


class CoverageCapability(Enum):
    """Three-state coverage capability."""

    UNSUPPORTED = "unsupported"  # Pack does not support coverage
    AVAILABLE = "available"  # Coverage ready to use
    MISSING_PREREQ = "missing_prereq"  # Could work but prereq missing


@dataclass
class PackRuntime:
    """Runtime context for a runner pack."""

    workspace_root: Path
    runner_available: bool  # Is the test runner installed?
    coverage_tools: dict[str, bool] = field(default_factory=dict)  # tool -> available


@dataclass
class CoverageArtifact:
    """Coverage artifact metadata."""

    format: str  # e.g., "lcov", "istanbul", "jacoco"
    path: Path  # Path to coverage file/directory
    pack_id: str  # Which pack produced this
    invocation_id: str  # Links to test invocation


class CoverageEmitter(ABC):
    """Abstract base for coverage emission.

    Each pack that supports coverage implements an emitter that:
    1. Detects whether coverage is possible (capability)
    2. Provides command-line modifications to enable coverage
    3. Describes where to find the coverage artifact
    """

    @property
    @abstractmethod
    def format_id(self) -> str:
        """Coverage format identifier (e.g., 'lcov', 'istanbul')."""
        ...

    @abstractmethod
    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        """Detect whether coverage is available."""
        ...

    @abstractmethod
    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,
        source_dirs: list[str] | None = None,
    ) -> list[str]:
        """Modify test command to enable coverage.

        Args:
            cmd: Base test command.
            output_dir: Directory for coverage artifacts.
            source_dirs: Optional list of source directories to scope coverage.
                        When provided, generates targeted ``--cov=<dir>`` args
                        instead of ``--cov=.``.
        """
        ...

    @abstractmethod
    def artifact_path(self, output_dir: Path) -> Path:
        """Path where coverage artifact will be written."""
        ...


# =============================================================================
# Python - pytest-cov (lcov output)
# =============================================================================


class PytestCovEmitter(CoverageEmitter):
    """Coverage via pytest-cov with lcov output."""

    @property
    def format_id(self) -> str:
        return "lcov"

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        # pytest-cov availability checked via import
        if not runtime.coverage_tools.get("pytest-cov", False):
            return CoverageCapability.MISSING_PREREQ
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,
        source_dirs: list[str] | None = None,
    ) -> list[str]:
        cov_path = output_dir / "coverage"
        result = [*cmd, f"--cov-report=lcov:{cov_path}/lcov.info"]
        if source_dirs:
            for d in source_dirs:
                result.append(f"--cov={d}")
        else:
            result.append("--cov=.")
        return result

    def artifact_path(self, output_dir: Path) -> Path:
        return output_dir / "coverage" / "lcov.info"


# =============================================================================
# JavaScript - Jest/Vitest (istanbul/lcov)
# =============================================================================


class JestCoverageEmitter(CoverageEmitter):
    """Coverage via Jest's built-in coverage (istanbul format)."""

    @property
    def format_id(self) -> str:
        return "istanbul"

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        # Jest has built-in coverage, no extra tool needed
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,
        source_dirs: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        cov_path = output_dir / "coverage"
        return [
            *cmd,
            "--coverage",
            f"--coverageDirectory={cov_path}",
            "--coverageReporters=json",
            "--coverageReporters=lcov",
        ]

    def artifact_path(self, output_dir: Path) -> Path:
        return output_dir / "coverage"


class VitestCoverageEmitter(CoverageEmitter):
    """Coverage via Vitest's built-in coverage (v8/istanbul)."""

    @property
    def format_id(self) -> str:
        return "istanbul"  # or v8, configurable

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        # Vitest has built-in coverage with v8 or istanbul
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,
        source_dirs: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        cov_path = output_dir / "coverage"
        return [
            *cmd,
            "--coverage",
            f"--coverage.reportsDirectory={cov_path}",
            "--coverage.reporter=json",
            "--coverage.reporter=lcov",
        ]

    def artifact_path(self, output_dir: Path) -> Path:
        return output_dir / "coverage"


# =============================================================================
# Go - go test -coverprofile
# =============================================================================


class GoCoverageEmitter(CoverageEmitter):
    """Coverage via go test -coverprofile."""

    @property
    def format_id(self) -> str:
        return "gocov"

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        # go test has built-in coverage
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,
        source_dirs: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        cov_path = output_dir / "coverage" / "coverage.out"
        cov_path.parent.mkdir(parents=True, exist_ok=True)
        return [*cmd, f"-coverprofile={cov_path}"]

    def artifact_path(self, output_dir: Path) -> Path:
        return output_dir / "coverage" / "coverage.out"


# =============================================================================
# Rust - cargo-llvm-cov (lcov output)
# =============================================================================


class CargoLlvmCovEmitter(CoverageEmitter):
    """Coverage via cargo-llvm-cov with lcov output."""

    @property
    def format_id(self) -> str:
        return "lcov"

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        if not shutil.which("cargo-llvm-cov"):
            return CoverageCapability.MISSING_PREREQ
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,
        source_dirs: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        # Replace 'cargo test' with 'cargo llvm-cov'
        cov_path = output_dir / "coverage" / "lcov.info"
        cov_path.parent.mkdir(parents=True, exist_ok=True)
        # cargo llvm-cov needs different invocation
        new_cmd = ["cargo", "llvm-cov", "--lcov", f"--output-path={cov_path}"]
        # Preserve any additional args after 'cargo test'
        if len(cmd) > 2:
            new_cmd.extend(cmd[2:])
        return new_cmd

    def artifact_path(self, output_dir: Path) -> Path:
        return output_dir / "coverage" / "lcov.info"


# =============================================================================
# Java - JaCoCo (via Maven/Gradle)
# =============================================================================


class MavenJacocoEmitter(CoverageEmitter):
    """Coverage via JaCoCo Maven plugin."""

    @property
    def format_id(self) -> str:
        return "jacoco"

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        # JaCoCo typically configured in pom.xml
        # For now assume available if Maven is available
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,  # noqa: ARG002
        source_dirs: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        # JaCoCo configured via pom.xml, just ensure report generation
        return [*cmd, "jacoco:report"]

    def artifact_path(self, output_dir: Path) -> Path:
        # JaCoCo writes to target/site/jacoco
        return output_dir.parent / "target" / "site" / "jacoco"


class GradleJacocoEmitter(CoverageEmitter):
    """Coverage via JaCoCo Gradle plugin."""

    @property
    def format_id(self) -> str:
        return "jacoco"

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,  # noqa: ARG002
        source_dirs: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        # Add jacocoTestReport task
        return [*cmd, "jacocoTestReport"]

    def artifact_path(self, output_dir: Path) -> Path:
        # Gradle JaCoCo writes to build/reports/jacoco
        return output_dir.parent / "build" / "reports" / "jacoco"


# =============================================================================
# .NET - coverlet (cobertura output)
# =============================================================================


class DotnetCoverletEmitter(CoverageEmitter):
    """Coverage via coverlet for .NET."""

    @property
    def format_id(self) -> str:
        return "cobertura"

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        # coverlet.collector usually included in test project
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,
        source_dirs: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        cov_path = output_dir / "coverage"
        return [
            *cmd,
            "--collect:XPlat Code Coverage",
            f"--results-directory:{cov_path}",
        ]

    def artifact_path(self, output_dir: Path) -> Path:
        return output_dir / "coverage"


# =============================================================================
# Ruby - SimpleCov
# =============================================================================


class SimpleCovEmitter(CoverageEmitter):
    """Coverage via SimpleCov for Ruby."""

    @property
    def format_id(self) -> str:
        return "simplecov"

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        if not runtime.coverage_tools.get("simplecov", False):
            return CoverageCapability.MISSING_PREREQ
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,
        source_dirs: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        # SimpleCov typically configured in spec_helper.rb
        # Set environment variable to specify output directory
        cov_path = output_dir / "coverage"
        # This requires SimpleCov to read COVERAGE_DIR env var
        return [f"COVERAGE_DIR={cov_path}", *cmd]

    def artifact_path(self, output_dir: Path) -> Path:
        return output_dir / "coverage"


# =============================================================================
# PHP - PHPUnit coverage
# =============================================================================


class PHPUnitCoverageEmitter(CoverageEmitter):
    """Coverage via PHPUnit with clover output."""

    @property
    def format_id(self) -> str:
        return "clover"

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        # PHPUnit requires xdebug or pcov for coverage
        if not runtime.coverage_tools.get("xdebug", False) and not runtime.coverage_tools.get(
            "pcov", False
        ):
            return CoverageCapability.MISSING_PREREQ
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,
        source_dirs: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        cov_path = output_dir / "coverage" / "clover.xml"
        return [*cmd, f"--coverage-clover={cov_path}"]

    def artifact_path(self, output_dir: Path) -> Path:
        return output_dir / "coverage" / "clover.xml"


# =============================================================================
# Dart/Flutter
# =============================================================================


class DartCoverageEmitter(CoverageEmitter):
    """Coverage via dart test --coverage."""

    @property
    def format_id(self) -> str:
        return "dart"

    def capability(self, runtime: PackRuntime) -> CoverageCapability:
        if not runtime.runner_available:
            return CoverageCapability.UNSUPPORTED
        return CoverageCapability.AVAILABLE

    def modify_command(
        self,
        cmd: list[str],
        output_dir: Path,
        source_dirs: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        cov_path = output_dir / "coverage"
        return [*cmd, f"--coverage={cov_path}"]

    def artifact_path(self, output_dir: Path) -> Path:
        return output_dir / "coverage"


# =============================================================================
# Emitter Registry
# =============================================================================

# Map pack_id -> emitter class
EMITTER_REGISTRY: dict[str, type[CoverageEmitter]] = {
    "python.pytest": PytestCovEmitter,
    "js.jest": JestCoverageEmitter,
    "js.vitest": VitestCoverageEmitter,
    "go.gotest": GoCoverageEmitter,
    "rust.nextest": CargoLlvmCovEmitter,
    "rust.cargotest": CargoLlvmCovEmitter,
    "java.maven": MavenJacocoEmitter,
    "java.gradle": GradleJacocoEmitter,
    "dotnet.dotnettest": DotnetCoverletEmitter,
    "ruby.rspec": SimpleCovEmitter,
    "php.phpunit": PHPUnitCoverageEmitter,
    "dart.darttest": DartCoverageEmitter,
    "dart.fluttertest": DartCoverageEmitter,
}

# Packs that explicitly do not support coverage
NO_COVERAGE_PACKS: frozenset[str] = frozenset(
    {
        "kotlin.gradle",  # Use Java JaCoCo
        "swift.xctest",  # Xcode coverage is complex
        "scala.sbt",  # Use Java JaCoCo
        "bash.bats",  # No coverage support
        "powershell.pester",  # Coverage via different mechanism
        "lua.busted",  # No standard coverage
    }
)


def get_emitter(pack_id: str) -> CoverageEmitter | None:
    """Get coverage emitter for a pack."""
    emitter_class = EMITTER_REGISTRY.get(pack_id)
    if emitter_class is None:
        return None
    return emitter_class()


def supports_coverage(pack_id: str) -> bool:
    """Check if a pack has coverage support."""
    return pack_id in EMITTER_REGISTRY
