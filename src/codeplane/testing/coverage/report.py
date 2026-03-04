"""Coverage report generation — tiered compact text output.

Output tiers (per-file coverage determines verbosity):
    <20%  coverage → filename: N%  (broadly uncovered — no line detail)
    20-99% coverage → filename: N% + uncovered function names *or*
                       gap-tolerant compressed line ranges (fallback)
    100%  coverage → omitted from output entirely

Example:
    coverage: 72% (340/472 lines)
      checkpoint.py: 45% — uncovered: _build_coverage_text, _extract_traceback
      delivery.py: 82% — uncovered: 105-130,201-215
      models.py: 5%
"""

from pathlib import Path
from typing import Any

from codeplane.testing.coverage.models import CoverageReport, FileCoverage

# ---------------------------------------------------------------------------
# Tier thresholds
# ---------------------------------------------------------------------------
_LOW_COVERAGE_THRESHOLD = 20   # files below this: percent only
_FULL_COVERAGE_THRESHOLD = 100  # files at this: omitted

# _compress_ranges_tolerant: bridge gaps up to this many non-instrumented lines
_GAP_BRIDGE_LIMIT = 3


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


def _compress_ranges_tolerant(
    uncovered: list[int],
    instrumented: set[int],
    gap_limit: int = _GAP_BRIDGE_LIMIT,
) -> str:
    """Compress line ranges, bridging small gaps of non-instrumented lines.

    Unlike ``_compress_ranges`` which fragments at every blank/comment line,
    this variant merges adjacent ranges when the gap consists solely of
    non-instrumented lines (blanks, comments, decorators) and is at most
    ``gap_limit`` lines wide.

    Example: instrumented = {1,2,3,5,6,7,9,10} (line 4,8 are blanks)
             uncovered   = [1,2,3,5,6,7,9,10]
             _compress_ranges       → "1-3,5-7,9-10"  (3 segments)
             _compress_ranges_tolerant → "1-10"         (1 segment)
    """
    if not uncovered:
        return ""

    ranges: list[str] = []
    start = uncovered[0]
    end = uncovered[0]

    for line in uncovered[1:]:
        gap_start = end + 1
        gap_end = line - 1
        gap_size = gap_end - gap_start + 1

        # Bridge if gap is small AND none of the gap lines are instrumented
        # (i.e. they're blanks/comments that the coverage tool skipped)
        if line == end + 1 or (
            gap_size <= gap_limit
            and not any(g in instrumented for g in range(gap_start, gap_end + 1))
        ):
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


def _file_coverage_detail(fc: FileCoverage) -> str:
    """Build per-file coverage detail string based on coverage tier.

    Tiers:
        100%         → omitted (caller skips)
        < 20%        → just percent
        20–99%       → percent + uncovered function names (semantic)
                       OR gap-tolerant compressed ranges (fallback)
    """
    pct = int(fc.line_rate * 100)
    filename = _file_basename(fc.path)

    if pct >= _FULL_COVERAGE_THRESHOLD:
        return ""  # omit fully-covered files

    if pct < _LOW_COVERAGE_THRESHOLD:
        return f"  {filename}: {pct}%"

    # Mid-tier: prefer semantic (function names), fall back to ranges
    uncovered_fns = [
        fn.name for fn in fc.functions.values() if fn.hits == 0
    ]
    if uncovered_fns:
        names = ", ".join(uncovered_fns)
        return f"  {filename}: {pct}% — uncovered: {names}"

    # Fallback: gap-tolerant compressed line ranges
    missed = sorted(line for line, hits in fc.lines.items() if hits == 0)
    if missed:
        instrumented = set(fc.lines.keys())
        ranges = _compress_ranges_tolerant(missed, instrumented)
        return f"  {filename}: {pct}% — uncovered: {ranges}"

    return f"  {filename}: {pct}%"


def build_tiered_coverage(
    report: CoverageReport,
    *,
    filter_paths: set[str] | None = None,
) -> str:
    """Build tiered inline coverage summary.

    Returns a compact multi-line string suitable for direct inclusion in
    checkpoint responses — no sidecar cache needed.

    Tiers per file:
        100%  → omitted entirely
        <20%  → ``filename: N%`` (broadly uncovered, no line detail)
        20–99% → ``filename: N%`` + uncovered function names when available,
                 or gap-tolerant compressed line ranges as fallback

    Example::

        coverage: 72% (340/472 lines)
          checkpoint.py: 45% — uncovered: _build_coverage_text, _extract_traceback
          delivery.py: 82% — uncovered: 105-130,201-215
          models.py: 5%
    """
    if filter_paths is not None and len(filter_paths) == 0:
        return "coverage: no source files changed"

    total_lines = 0
    covered_lines = 0
    file_lines: list[str] = []

    for path in sorted(report.files.keys()):
        if filter_paths is not None and not _path_matches(path, filter_paths):
            continue

        fc = report.files[path]
        file_total = len(fc.lines)
        file_covered = sum(1 for hits in fc.lines.values() if hits > 0)
        total_lines += file_total
        covered_lines += file_covered

        detail_line = _file_coverage_detail(fc)
        if detail_line:
            file_lines.append(detail_line)

    if total_lines == 0:
        return "coverage: no data"

    percent = int(covered_lines / total_lines * 100)
    header = f"coverage: {percent}% ({covered_lines}/{total_lines} lines)"

    if not file_lines:
        return header

    return header + "\n" + "\n".join(file_lines)


def build_coverage_detail(
    report: CoverageReport,
    *,
    filter_paths: set[str] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Build inline summary + detailed per-file coverage data.

    .. deprecated::
        Use :func:`build_tiered_coverage` instead.  This function exists
        for backward compatibility only.

    Returns:
        (inline_summary, detail_dict) where:
        - inline_summary: compact one-liner like ``coverage: 85% (170/200 lines)``
        - detail_dict: structured per-file data, or None if no data.
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
