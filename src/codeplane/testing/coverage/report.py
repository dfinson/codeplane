"""Coverage report generation — compact text output.

Output format:
    coverage: 85% (170/200 lines)
    uncovered: report.py:37,39,42-48 | merge.py:15-20,45
"""

from pathlib import Path
from typing import Any

from codeplane.testing.coverage.models import CoverageReport


def _normalize_path(path: str) -> str:
    """Normalize path for matching (strip leading ./ and trailing /)."""
    p = path.lstrip("./").rstrip("/")
    return p


def _path_matches(file_path: str, filter_paths: set[str]) -> bool:
    """Check if file_path matches any path in filter_paths."""
    normalized = _normalize_path(file_path)
    for fp in filter_paths:
        fp_norm = _normalize_path(fp)
        if normalized == fp_norm:
            return True
        if normalized.endswith("/" + fp_norm) or normalized.endswith("\\" + fp_norm):
            return True
        if fp_norm.endswith("/" + normalized) or fp_norm.endswith("\\" + normalized):
            return True
    return False


def _compress_ranges(lines: list[int]) -> str:
    """Compress sorted line numbers into ranges: [1,2,3,5,7,8,9] -> '1-3,5,7-9'."""
    if not lines:
        return ""

    ranges: list[str] = []
    start = lines[0]
    end = lines[0]

    for line in lines[1:]:
        if line == end + 1:
            end = line
        else:
            ranges.append(f"{start}-{end}" if end > start else str(start))
            start = end = line

    ranges.append(f"{start}-{end}" if end > start else str(start))
    return ",".join(ranges)


def _file_basename(path: str) -> str:
    """Extract filename from path."""
    return Path(path).name


def build_compact_summary(
    report: CoverageReport,
    *,
    filter_paths: set[str] | None = None,
) -> str:
    """Build compact text coverage summary.

    Format:
        coverage: 85% (170/200 lines)
        uncovered: report.py:37,39,42-48 | merge.py:15-20,45

    Args:
        report: The coverage report to summarize.
        filter_paths: If provided, only include files matching these paths.
            If empty set, returns minimal summary (no uncovered line details).

    Returns:
        Compact text summary.
    """
    # If filter_paths is an empty set (not None), no source files to evaluate
    if filter_paths is not None and len(filter_paths) == 0:
        return "coverage: no source files changed"

    total_lines = 0
    covered_lines = 0
    uncovered_parts: list[str] = []

    for path in sorted(report.files.keys()):
        if filter_paths is not None and not _path_matches(path, filter_paths):
            continue

        fc = report.files[path]
        total_lines += len(fc.lines)
        covered_lines += sum(1 for hits in fc.lines.values() if hits > 0)

        # Collect uncovered lines
        missed = sorted(line for line, hits in fc.lines.items() if hits == 0)
        if missed:
            filename = _file_basename(path)
            ranges = _compress_ranges(missed)
            uncovered_parts.append(f"{filename}:{ranges}")

    if total_lines == 0:
        return "coverage: no data"

    percent = int(covered_lines / total_lines * 100)
    header = f"coverage: {percent}% ({covered_lines}/{total_lines} lines)"

    if not uncovered_parts:
        return header

    uncovered_text = " | ".join(uncovered_parts)
    return f"{header}\nuncovered: {uncovered_text}"


def build_coverage_detail(
    report: CoverageReport,
    *,
    filter_paths: set[str] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Build inline summary + detailed per-file coverage data.

    Returns:
        (inline_summary, detail_dict) where:
        - inline_summary: compact one-liner like ``coverage: 85% (170/200 lines)``
        - detail_dict: structured per-file data (for cache), or None if no data.
          Keys: ``summary`` (str), ``total_lines``, ``covered_lines``,
          ``coverage_percent``, ``files`` (list of per-file dicts with
          ``path``, ``total``, ``covered``, ``percent``, ``uncovered_ranges``).
    """
    if filter_paths is not None and len(filter_paths) == 0:
        return "coverage: no source files changed", None

    total_lines = 0
    covered_lines = 0
    file_details: list[dict[str, Any]] = []

    for path in sorted(report.files.keys()):
        if filter_paths is not None and not _path_matches(path, filter_paths):
            continue

        fc = report.files[path]
        file_total = len(fc.lines)
        file_covered = sum(1 for hits in fc.lines.values() if hits > 0)
        total_lines += file_total
        covered_lines += file_covered

        missed = sorted(line for line, hits in fc.lines.items() if hits == 0)
        pct = int(file_covered / file_total * 100) if file_total > 0 else 100

        entry: dict[str, Any] = {
            "path": path,
            "total": file_total,
            "covered": file_covered,
            "percent": pct,
        }
        if missed:
            entry["uncovered_ranges"] = _compress_ranges(missed)
        file_details.append(entry)

    if total_lines == 0:
        return "coverage: no data", None

    percent = int(covered_lines / total_lines * 100)
    inline = f"coverage: {percent}% ({covered_lines}/{total_lines} lines)"

    n_uncovered = sum(1 for f in file_details if "uncovered_ranges" in f)
    if n_uncovered:
        inline += f", {n_uncovered} file(s) with gaps"

    detail: dict[str, Any] = {
        "summary": inline,
        "total_lines": total_lines,
        "covered_lines": covered_lines,
        "coverage_percent": percent,
        "files": file_details,
    }
    return inline, detail


# Legacy functions kept for backward compatibility


def compute_file_stats(
    report: CoverageReport,
    *,
    filter_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Compute per-file coverage statistics (legacy)."""
    file_stats = []
    for path in sorted(report.files.keys()):
        if filter_paths is not None and not _path_matches(path, filter_paths):
            continue
        fc = report.files[path]
        total = len(fc.lines)
        covered = sum(1 for hits in fc.lines.values() if hits > 0)
        missed = sorted(line for line, hits in fc.lines.items() if hits == 0)
        pct = (covered / total * 100.0) if total > 0 else 100.0
        file_stats.append(
            {
                "path": path,
                "total_lines": total,
                "covered_lines": covered,
                "coverage_percent": round(pct, 2),
                "missed_lines": missed,
            }
        )
    return file_stats


def build_summary(
    report: CoverageReport,
    *,
    filter_paths: set[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Build structured summary (legacy — prefer build_compact_summary)."""
    total_lines = 0
    covered_lines = 0
    for path, fc in report.files.items():
        if filter_paths is not None and not _path_matches(path, filter_paths):
            continue
        total_lines += len(fc.lines)
        covered_lines += sum(1 for hits in fc.lines.values() if hits > 0)

    pct = (covered_lines / total_lines * 100.0) if total_lines > 0 else 100.0
    return {
        "summary": {
            "total_lines": total_lines,
            "covered_lines": covered_lines,
            "line_coverage_percent": round(pct, 2),
        },
        "source_format": report.source_format,
    }
