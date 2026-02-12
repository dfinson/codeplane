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
from typing import TYPE_CHECKING, Any

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


# =============================================================================
# Coverage Summary - Structured Stats for Agent Consumption
# =============================================================================


@dataclass
class CoverageSummary:
    """Structured coverage statistics.

    Provides parsed coverage data instead of text hints, enabling agents
    to act on coverage numbers directly.
    """

    # Line coverage (most common metric)
    lines_covered: int = 0
    lines_total: int = 0
    lines_percent: float = 0.0

    # Branch coverage (optional, not all formats support this)
    branches_covered: int | None = None
    branches_total: int | None = None
    branches_percent: float | None = None

    # Function/method coverage (optional)
    functions_covered: int | None = None
    functions_total: int | None = None
    functions_percent: float | None = None

    # File counts
    files_with_coverage: int = 0
    files_total: int = 0

    # Format info
    format_id: str = "unknown"
    pack_id: str = ""

    @property
    def is_valid(self) -> bool:
        """Check if coverage data was successfully parsed."""
        return self.lines_total > 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization.

        Percentages are rounded to 1 decimal place for cleaner output.
        """
        result: dict[str, Any] = {
            "lines": {
                "covered": self.lines_covered,
                "total": self.lines_total,
                "percent": round(self.lines_percent, 1),
            },
            "files": {
                "with_coverage": self.files_with_coverage,
                "total": self.files_total,
            },
            "format": self.format_id,
            "pack_id": self.pack_id,
        }
        if self.branches_total is not None:
            result["branches"] = {
                "covered": self.branches_covered,
                "total": self.branches_total,
                "percent": round(self.branches_percent or 0, 1),
            }
        if self.functions_total is not None:
            result["functions"] = {
                "covered": self.functions_covered,
                "total": self.functions_total,
                "percent": round(self.functions_percent or 0, 1),
            }
        return result


def parse_coverage_summary(artifact: CoverageArtifact) -> CoverageSummary | None:
    """Parse coverage artifact into structured summary.

    Supports LCOV, Istanbul JSON, Go coverage, JaCoCo, and Cobertura formats.
    Returns None if parsing fails.
    """
    import json

    if not artifact.path.exists():
        logger.debug("Coverage artifact not found: %s", artifact.path)
        return None

    try:
        if artifact.format == "lcov":
            return _parse_lcov(artifact.path, artifact)
        elif artifact.format == "istanbul":
            return _parse_istanbul(artifact.path, artifact)
        elif artifact.format == "gocov":
            return _parse_gocov(artifact.path, artifact)
        elif artifact.format == "jacoco":
            return _parse_jacoco(artifact.path, artifact)
        elif artifact.format == "cobertura":
            return _parse_cobertura(artifact.path, artifact)
        else:
            logger.debug("Unknown coverage format: %s", artifact.format)
            return None
    except (OSError, ValueError, KeyError) as e:
        # OSError: file read errors
        # ValueError: numeric parsing errors in coverage data
        # KeyError: missing expected fields in JSON formats
        logger.debug("Failed to parse %s coverage at %s: %s", artifact.format, artifact.path, e)
        return None
    except json.JSONDecodeError as e:
        # Malformed JSON in istanbul format
        logger.debug("Invalid JSON in coverage file %s: %s", artifact.path, e)
        return None


def _parse_lcov(path: Path, artifact: CoverageArtifact) -> CoverageSummary:
    """Parse LCOV format coverage file.

    LCOV format:
    - SF: source file path
    - DA:line_number,execution_count - line data
    - LF: lines found (total lines in file)
    - LH: lines hit (covered lines in file)
    - BRF: branches found
    - BRH: branches hit
    - FNF: functions found
    - FNH: functions hit
    """
    content = path.read_text()

    files_count = 0
    total_lf = 0  # Lines found
    total_lh = 0  # Lines hit
    total_brf = 0  # Branches found
    total_brh = 0  # Branches hit
    total_fnf = 0  # Functions found
    total_fnh = 0  # Functions hit

    for line in content.splitlines():
        line = line.strip()
        if line.startswith("SF:"):
            files_count += 1
        elif line.startswith("LF:"):
            total_lf += int(line[3:])
        elif line.startswith("LH:"):
            total_lh += int(line[3:])
        elif line.startswith("BRF:"):
            total_brf += int(line[4:])
        elif line.startswith("BRH:"):
            total_brh += int(line[4:])
        elif line.startswith("FNF:"):
            total_fnf += int(line[4:])
        elif line.startswith("FNH:"):
            total_fnh += int(line[4:])

    summary = CoverageSummary(
        lines_covered=total_lh,
        lines_total=total_lf,
        lines_percent=(total_lh / total_lf * 100) if total_lf > 0 else 0.0,
        files_with_coverage=files_count,
        files_total=files_count,
        format_id="lcov",
        pack_id=artifact.pack_id,
    )

    if total_brf > 0:
        summary.branches_covered = total_brh
        summary.branches_total = total_brf
        summary.branches_percent = (total_brh / total_brf * 100) if total_brf > 0 else 0.0

    if total_fnf > 0:
        summary.functions_covered = total_fnh
        summary.functions_total = total_fnf
        summary.functions_percent = (total_fnh / total_fnf * 100) if total_fnf > 0 else 0.0

    return summary


def _parse_istanbul(path: Path, artifact: CoverageArtifact) -> CoverageSummary:
    """Parse Istanbul/NYC JSON coverage.

    Istanbul stores coverage in coverage-summary.json with format:
    {
      "total": {
        "lines": {"total": N, "covered": N, "pct": N},
        "branches": {...},
        "functions": {...},
        "statements": {...}
      }
    }
    """
    import json

    # Istanbul creates a directory with multiple files
    summary_file = path / "coverage-summary.json" if path.is_dir() else path
    if not summary_file.exists():
        # Try coverage-final.json
        summary_file = path / "coverage-final.json" if path.is_dir() else path
        if not summary_file.exists():
            return CoverageSummary(format_id="istanbul", pack_id=artifact.pack_id)

    data = json.loads(summary_file.read_text())

    # Handle coverage-summary.json format
    if "total" in data:
        total = data["total"]
        return CoverageSummary(
            lines_covered=total.get("lines", {}).get("covered", 0),
            lines_total=total.get("lines", {}).get("total", 0),
            lines_percent=total.get("lines", {}).get("pct", 0.0),
            branches_covered=total.get("branches", {}).get("covered"),
            branches_total=total.get("branches", {}).get("total"),
            branches_percent=total.get("branches", {}).get("pct"),
            functions_covered=total.get("functions", {}).get("covered"),
            functions_total=total.get("functions", {}).get("total"),
            functions_percent=total.get("functions", {}).get("pct"),
            files_with_coverage=len(data) - 1,  # Subtract 'total' key
            files_total=len(data) - 1,
            format_id="istanbul",
            pack_id=artifact.pack_id,
        )

    # Handle coverage-final.json format (per-file data)
    files_count = len(data)
    total_lines_covered = 0
    total_lines = 0
    total_branches_covered = 0
    total_branches = 0

    for _file_path, file_cov in data.items():
        # Count statement coverage as line coverage
        if "s" in file_cov:
            for count in file_cov["s"].values():
                total_lines += 1
                if count > 0:
                    total_lines_covered += 1
        if "b" in file_cov:
            for branch_counts in file_cov["b"].values():
                for count in branch_counts:
                    total_branches += 1
                    if count > 0:
                        total_branches_covered += 1

    return CoverageSummary(
        lines_covered=total_lines_covered,
        lines_total=total_lines,
        lines_percent=(total_lines_covered / total_lines * 100) if total_lines > 0 else 0.0,
        branches_covered=total_branches_covered if total_branches > 0 else None,
        branches_total=total_branches if total_branches > 0 else None,
        branches_percent=(
            (total_branches_covered / total_branches * 100) if total_branches > 0 else None
        ),
        files_with_coverage=files_count,
        files_total=files_count,
        format_id="istanbul",
        pack_id=artifact.pack_id,
    )


def _parse_gocov(path: Path, artifact: CoverageArtifact) -> CoverageSummary:
    """Parse Go coverage profile.

    Go coverage format:
    mode: set/count/atomic
    file.go:start_line.start_col,end_line.end_col num_statements count
    """
    content = path.read_text()
    lines = content.strip().splitlines()

    if not lines:
        return CoverageSummary(format_id="gocov", pack_id=artifact.pack_id)

    files_seen: set[str] = set()
    total_statements = 0
    covered_statements = 0

    for line in lines[1:]:  # Skip mode line
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 3:
            file_block = parts[0]
            file_path = file_block.split(":")[0]
            files_seen.add(file_path)
            num_statements = int(parts[1])
            count = int(parts[2])
            total_statements += num_statements
            if count > 0:
                covered_statements += num_statements

    return CoverageSummary(
        lines_covered=covered_statements,
        lines_total=total_statements,
        lines_percent=(
            (covered_statements / total_statements * 100) if total_statements > 0 else 0.0
        ),
        files_with_coverage=len(files_seen),
        files_total=len(files_seen),
        format_id="gocov",
        pack_id=artifact.pack_id,
    )


def _parse_jacoco(path: Path, artifact: CoverageArtifact) -> CoverageSummary:
    """Parse JaCoCo XML coverage report.

    JaCoCo XML format has counters at multiple levels (report, package, class, method).
    We specifically read from the root <report> element's direct <counter> children
    to get the report-level totals.

    Structure:
        <report name="...">
            <counter type="LINE" missed="N" covered="N"/>
            <counter type="BRANCH" missed="N" covered="N"/>
            <counter type="METHOD" missed="N" covered="N"/>
            <package name="...">
                <counter type="LINE" .../> <!-- package-level, ignore -->
            </package>
        </report>
    """
    import xml.etree.ElementTree as ET

    # JaCoCo outputs to a directory; look for jacoco.xml
    if path.is_dir():
        xml_file = path / "jacoco.xml"
        if not xml_file.exists():
            # Try site/jacoco structure (Maven default)
            xml_file = path / "site" / "jacoco" / "jacoco.xml"
        if not xml_file.exists():
            logger.debug("JaCoCo XML not found in %s", path)
            return CoverageSummary(format_id="jacoco", pack_id=artifact.pack_id)
    else:
        xml_file = path

    tree = ET.parse(xml_file)
    root = tree.getroot()

    # Read counters directly under root (report-level totals)
    lines_covered = 0
    lines_missed = 0
    branches_covered = 0
    branches_missed = 0
    methods_covered = 0
    methods_missed = 0

    for counter in root.findall("./counter"):
        counter_type = counter.get("type")
        covered = int(counter.get("covered", 0))
        missed = int(counter.get("missed", 0))

        if counter_type == "LINE":
            lines_covered = covered
            lines_missed = missed
        elif counter_type == "BRANCH":
            branches_covered = covered
            branches_missed = missed
        elif counter_type == "METHOD":
            methods_covered = covered
            methods_missed = missed

    lines_total = lines_covered + lines_missed
    branches_total = branches_covered + branches_missed
    methods_total = methods_covered + methods_missed

    # Count packages as proxy for file count
    packages = root.findall(".//package")

    summary = CoverageSummary(
        lines_covered=lines_covered,
        lines_total=lines_total,
        lines_percent=(lines_covered / lines_total * 100) if lines_total > 0 else 0.0,
        files_with_coverage=len(packages),
        files_total=len(packages),
        format_id="jacoco",
        pack_id=artifact.pack_id,
    )

    if branches_total > 0:
        summary.branches_covered = branches_covered
        summary.branches_total = branches_total
        summary.branches_percent = branches_covered / branches_total * 100

    if methods_total > 0:
        summary.functions_covered = methods_covered
        summary.functions_total = methods_total
        summary.functions_percent = methods_covered / methods_total * 100

    return summary


def _parse_cobertura(path: Path, artifact: CoverageArtifact) -> CoverageSummary:
    """Parse Cobertura XML coverage report.

    Cobertura format (used by .NET coverlet, Python coverage.py, etc.):
        <coverage line-rate="0.85" branch-rate="0.50" lines-covered="100" lines-valid="117">
            <packages>...</packages>
        </coverage>

    The line-rate is a decimal (0.0-1.0), not a percentage.
    Some variants use lines-covered/lines-valid instead of line-rate.
    """
    import xml.etree.ElementTree as ET

    # Handle directory or direct file
    if path.is_dir():
        # Look for common cobertura filenames
        for name in ["coverage.cobertura.xml", "cobertura.xml", "coverage.xml"]:
            xml_file = path / name
            if xml_file.exists():
                break
        else:
            # Try glob for .NET TestResults structure
            cobertura_files = list(path.glob("**/coverage.cobertura.xml"))
            if cobertura_files:
                xml_file = cobertura_files[0]
            else:
                logger.debug("Cobertura XML not found in %s", path)
                return CoverageSummary(format_id="cobertura", pack_id=artifact.pack_id)
    else:
        xml_file = path

    tree = ET.parse(xml_file)
    root = tree.getroot()

    # Handle namespace if present (some cobertura variants use it)
    # Strip namespace for simpler parsing
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]

    # Try line-rate first (most common)
    line_rate_str = root.get("line-rate")
    branch_rate_str = root.get("branch-rate")
    lines_covered_str = root.get("lines-covered")
    lines_valid_str = root.get("lines-valid")
    branches_covered_str = root.get("branches-covered")
    branches_valid_str = root.get("branches-valid")

    # Calculate line coverage
    if lines_covered_str and lines_valid_str:
        lines_covered = int(lines_covered_str)
        lines_total = int(lines_valid_str)
        lines_percent = (lines_covered / lines_total * 100) if lines_total > 0 else 0.0
    elif line_rate_str:
        line_rate = float(line_rate_str)
        lines_percent = line_rate * 100
        # We don't have exact counts, estimate from packages
        lines_covered = 0
        lines_total = 0
        for pkg in root.findall(".//package"):
            for cls in pkg.findall(".//class"):
                for line in cls.findall(".//line"):
                    lines_total += 1
                    if int(line.get("hits", 0)) > 0:
                        lines_covered += 1
    else:
        lines_covered = 0
        lines_total = 0
        lines_percent = 0.0

    # Calculate branch coverage
    branches_covered: int | None = None
    branches_total: int | None = None
    branches_percent: float | None = None

    if branches_covered_str and branches_valid_str:
        branches_covered = int(branches_covered_str)
        branches_total = int(branches_valid_str)
        branches_percent = (branches_covered / branches_total * 100) if branches_total > 0 else 0.0
    elif branch_rate_str:
        branch_rate = float(branch_rate_str)
        if branch_rate > 0:
            branches_percent = branch_rate * 100
            # Counts not available from rate alone
            branches_covered = None
            branches_total = None

    # Count packages/classes
    packages = root.findall(".//package")
    classes = root.findall(".//class")

    return CoverageSummary(
        lines_covered=lines_covered,
        lines_total=lines_total,
        lines_percent=lines_percent,
        branches_covered=branches_covered,
        branches_total=branches_total,
        branches_percent=branches_percent,
        files_with_coverage=len(classes) if classes else len(packages),
        files_total=len(classes) if classes else len(packages),
        format_id="cobertura",
        pack_id=artifact.pack_id,
    )
