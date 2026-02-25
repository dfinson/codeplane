"""Structured coverage report generation.

This module transforms CoverageReport data into structured JSON output
suitable for agent consumption. It replaces the old text-based coverage hints
with a fully structured format.

Output schema for build_summary:
{
    "summary": {
        "total_files": int,
        "covered_files": int,
        "total_lines": int,
        "covered_lines": int,
        "line_coverage_percent": float,
        "total_branches": int,
        "covered_branches": int,
        "branch_coverage_percent": float | null,
        "total_functions": int,
        "covered_functions": int,
        "function_coverage_percent": float | null
    },
    "files": [
        {
            "path": str,
            "total_lines": int,
            "covered_lines": int,
            "coverage_percent": float,
            "missed_lines": [int, ...],  # Line numbers with 0 hits
            "partial_branches": int | null
        },
        ...
    ],
    "source_format": str
}
"""

from typing import Any

from codeplane.testing.coverage.models import CoverageReport


def compute_file_stats(
    report: CoverageReport,
) -> list[dict[str, Any]]:
    """Compute per-file coverage statistics.

    Args:
        report: The coverage report to analyze.

    Returns:
        List of dicts with per-file stats, sorted by path.
    """
    file_stats = []

    for path in sorted(report.files.keys()):
        fc = report.files[path]

        total_lines = len(fc.lines)
        covered_lines = sum(1 for hits in fc.lines.values() if hits > 0)

        # Missed lines (0 hits)
        missed_lines = sorted(line for line, hits in fc.lines.items() if hits == 0)

        # Branch stats
        total_branches = len(fc.branches)
        partial_branches = len(
            {
                b.line
                for b in fc.branches
                if any(
                    b2.line == b.line and b2.hits == 0 for b2 in fc.branches if b2.line == b.line
                )
                and any(
                    b2.line == b.line and b2.hits > 0 for b2 in fc.branches if b2.line == b.line
                )
            }
        )

        # Coverage percent
        coverage_percent = (covered_lines / total_lines * 100.0) if total_lines > 0 else 100.0

        stats: dict[str, Any] = {
            "path": path,
            "total_lines": total_lines,
            "covered_lines": covered_lines,
            "coverage_percent": round(coverage_percent, 2),
            "missed_lines": missed_lines,
        }

        # Only include branch info if there are branches
        if total_branches > 0:
            stats["partial_branches"] = partial_branches

        file_stats.append(stats)

    return file_stats


def build_summary(
    report: CoverageReport,
    *,
    include_files: bool = True,
    max_files: int | None = None,
    max_missed_lines: int = 20,
) -> dict[str, Any]:
    """Build a structured coverage summary from a report.

    Args:
        report: The coverage report to summarize.
        include_files: Whether to include per-file details.
        max_files: Limit number of files (lowest coverage first). None = all.
        max_missed_lines: Max missed lines to list per file.

    Returns:
        Structured dict suitable for JSON serialization.
    """
    # Compute overall statistics
    total_lines = 0
    covered_lines = 0
    total_branches = 0
    covered_branches = 0
    total_functions = 0
    covered_functions = 0

    for fc in report.files.values():
        total_lines += len(fc.lines)
        covered_lines += sum(1 for hits in fc.lines.values() if hits > 0)

        total_branches += len(fc.branches)
        covered_branches += sum(1 for b in fc.branches if b.hits > 0)

        total_functions += len(fc.functions)
        covered_functions += sum(1 for f in fc.functions.values() if f.hits > 0)

    line_coverage_percent = (covered_lines / total_lines * 100.0) if total_lines > 0 else 100.0

    branch_coverage_percent = (
        (covered_branches / total_branches * 100.0) if total_branches > 0 else None
    )

    function_coverage_percent = (
        (covered_functions / total_functions * 100.0) if total_functions > 0 else None
    )

    covered_files = sum(
        1
        for fc in report.files.values()
        if all(hits > 0 for hits in fc.lines.values()) and fc.lines
    )

    summary_dict: dict[str, Any] = {
        "total_files": len(report.files),
        "covered_files": covered_files,
        "total_lines": total_lines,
        "covered_lines": covered_lines,
        "line_coverage_percent": round(line_coverage_percent, 2),
    }

    if total_branches > 0:
        summary_dict["total_branches"] = total_branches
        summary_dict["covered_branches"] = covered_branches
        summary_dict["branch_coverage_percent"] = (
            round(branch_coverage_percent, 2) if branch_coverage_percent else None
        )

    if total_functions > 0:
        summary_dict["total_functions"] = total_functions
        summary_dict["covered_functions"] = covered_functions
        summary_dict["function_coverage_percent"] = (
            round(function_coverage_percent, 2) if function_coverage_percent else None
        )

    result: dict[str, Any] = {
        "summary": summary_dict,
        "source_format": report.source_format,
    }

    if include_files:
        file_stats = compute_file_stats(report)

        # Sort by coverage percent (lowest first) to surface problem areas
        file_stats.sort(key=lambda f: f["coverage_percent"])

        if max_files is not None:
            file_stats = file_stats[:max_files]

        # Truncate missed_lines lists
        for fs in file_stats:
            missed = fs.get("missed_lines", [])
            if len(missed) > max_missed_lines:
                fs["missed_lines"] = missed[:max_missed_lines]
                fs["missed_lines_truncated"] = True

        result["files"] = file_stats

    return result


def build_text_summary(report: CoverageReport) -> str:
    """Build a concise text summary for display contexts.

    This is a fallback for contexts where structured data isn't suitable.

    Args:
        report: The coverage report to summarize.

    Returns:
        Human-readable text summary.
    """
    total_lines = sum(len(fc.lines) for fc in report.files.values())
    covered_lines = sum(
        sum(1 for hits in fc.lines.values() if hits > 0) for fc in report.files.values()
    )

    if total_lines == 0:
        return "No coverage data"

    percent = covered_lines / total_lines * 100.0

    return f"Coverage: {percent:.1f}% ({covered_lines}/{total_lines} lines)"
