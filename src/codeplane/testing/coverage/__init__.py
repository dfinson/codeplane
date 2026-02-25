"""Unified coverage parsing, merging, and reporting.

This package provides:
- Multi-format coverage parsing (8 formats)
- Max-hit merge across reports
- Structured JSON output for agents

Usage:
    from codeplane.testing.coverage import parse_artifact, merge, build_summary

    # Parse a coverage artifact
    report = parse_artifact(Path("coverage/lcov.info"))

    # Merge multiple reports
    merged = merge(report1, report2, report3)

    # Build structured summary for agents
    summary = build_summary(merged)

Supported formats:
    - lcov: pytest-cov, cargo-llvm-cov, dart test
    - cobertura: coverage.py, coverlet (.NET)
    - opencover: .NET with SequencePoint/BranchPoint
    - istanbul: Jest, Vitest, NYC (JavaScript)
    - jacoco: Java (Maven/Gradle)
    - gocov: Go test coverage profiles
    - clover: PHP (PHPUnit), Kotlin (kover)
    - simplecov: Ruby (SimpleCov)
"""

from codeplane.testing.coverage.merge import (
    merge,
    merge_file_coverage,
    merge_reports,
)
from codeplane.testing.coverage.models import (
    BranchCoverage,
    CoverageParseError,
    CoverageReport,
    CoverageSummary,
    FileCoverage,
    FunctionCoverage,
)
from codeplane.testing.coverage.parsers import (
    PARSER_BY_FORMAT,
    PARSER_REGISTRY,
    CoverageParser,
    detect_parser,
    parse_artifact,
)
from codeplane.testing.coverage.report import (
    build_summary,
    build_text_summary,
    compute_file_stats,
)

__all__ = [
    # Models
    "BranchCoverage",
    "CoverageParseError",
    "CoverageReport",
    "CoverageSummary",
    "FileCoverage",
    "FunctionCoverage",
    # Parsers
    "CoverageParser",
    "PARSER_BY_FORMAT",
    "PARSER_REGISTRY",
    "detect_parser",
    "parse_artifact",
    # Merge
    "merge",
    "merge_file_coverage",
    "merge_reports",
    # Report
    "build_summary",
    "build_text_summary",
    "compute_file_stats",
]
