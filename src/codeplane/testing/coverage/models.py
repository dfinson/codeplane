"""Unified coverage data model.

File-centric model for coverage data — agents reason about files and lines,
not assemblies or classes. All formats convert to this unified representation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class CoverageParseError(Exception):
    """Error parsing coverage data."""

    pass


@dataclass(frozen=True, slots=True)
class BranchCoverage:
    """Branch coverage at a specific line.

    Represents a single branch point (e.g., if/else, switch case).
    """

    line: int
    block_id: int
    branch_id: int
    hits: int


@dataclass(frozen=True, slots=True)
class FunctionCoverage:
    """Function/method coverage."""

    name: str
    start_line: int
    hits: int


@dataclass(slots=True)
class FileCoverage:
    """Coverage data for a single file.

    Lines are stored as a dict mapping line number → hit count.
    Line numbers are 1-based to match source file conventions.
    """

    path: str  # workspace-relative path
    lines: dict[int, int] = field(default_factory=dict)  # line_number → hit_count
    branches: list[BranchCoverage] = field(default_factory=list)
    functions: dict[str, FunctionCoverage] = field(default_factory=dict)  # name → coverage

    @property
    def lines_found(self) -> int:
        """Total number of instrumented lines."""
        return len(self.lines)

    @property
    def lines_hit(self) -> int:
        """Number of lines with at least one hit."""
        return sum(1 for hits in self.lines.values() if hits > 0)

    @property
    def line_rate(self) -> float:
        """Fraction of lines covered (0.0 to 1.0)."""
        if not self.lines:
            return 0.0
        return self.lines_hit / len(self.lines)

    @property
    def uncovered_lines(self) -> list[int]:
        """Sorted list of line numbers with zero hits."""
        return sorted(line for line, hits in self.lines.items() if hits == 0)

    @property
    def branches_found(self) -> int:
        """Total number of branches."""
        return len(self.branches)

    @property
    def branches_hit(self) -> int:
        """Number of branches taken at least once."""
        return sum(1 for b in self.branches if b.hits > 0)

    @property
    def branch_rate(self) -> float:
        """Fraction of branches covered (0.0 to 1.0)."""
        if not self.branches:
            return 0.0
        return self.branches_hit / len(self.branches)

    @property
    def functions_found(self) -> int:
        """Total number of functions."""
        return len(self.functions)

    @property
    def functions_hit(self) -> int:
        """Number of functions called at least once."""
        return sum(1 for f in self.functions.values() if f.hits > 0)

    @property
    def function_rate(self) -> float:
        """Fraction of functions covered (0.0 to 1.0)."""
        if not self.functions:
            return 0.0
        return self.functions_hit / len(self.functions)


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    """Aggregate coverage statistics.

    Computed from a CoverageReport — immutable summary snapshot.
    """

    lines_found: int
    lines_hit: int
    branches_found: int
    branches_hit: int
    functions_found: int
    functions_hit: int
    line_rate: float
    branch_rate: float
    function_rate: float


@dataclass(slots=True)
class CoverageReport:
    """Complete coverage report from one or more sources.

    Files are keyed by workspace-relative path.
    """

    source_format: str  # format id (e.g., "lcov", "cobertura") or "merged"
    files: dict[str, FileCoverage] = field(default_factory=dict)  # path → coverage

    @property
    def summary(self) -> CoverageSummary:
        """Compute aggregate summary across all files."""
        lines_found = sum(f.lines_found for f in self.files.values())
        lines_hit = sum(f.lines_hit for f in self.files.values())
        branches_found = sum(f.branches_found for f in self.files.values())
        branches_hit = sum(f.branches_hit for f in self.files.values())
        functions_found = sum(f.functions_found for f in self.files.values())
        functions_hit = sum(f.functions_hit for f in self.files.values())

        return CoverageSummary(
            lines_found=lines_found,
            lines_hit=lines_hit,
            branches_found=branches_found,
            branches_hit=branches_hit,
            functions_found=functions_found,
            functions_hit=functions_hit,
            line_rate=lines_hit / lines_found if lines_found > 0 else 0.0,
            branch_rate=branches_hit / branches_found if branches_found > 0 else 0.0,
            function_rate=functions_hit / functions_found if functions_found > 0 else 0.0,
        )
