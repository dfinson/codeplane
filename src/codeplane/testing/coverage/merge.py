"""Coverage report merging with max-hit semantics.

When merging multiple coverage reports (e.g., from parallel test runs or
multiple test suites), we use max-hit semantics:

- line[i] = max(line[i] across all reports)
- branch[j] = max(branch[j].hits across all reports)
- function[k] = max(function[k].hits across all reports)

This ensures the merged result represents "covered in any run" rather than
accidentally dropping coverage from parallel test shards.
"""

from collections.abc import Iterable

from codeplane.testing.coverage.models import (
    BranchCoverage,
    CoverageReport,
    FileCoverage,
    FunctionCoverage,
)


def merge_file_coverage(files: Iterable[FileCoverage]) -> FileCoverage:
    """Merge multiple FileCoverage objects for the same file.

    Uses max-hit semantics for all coverage data.

    Args:
        files: FileCoverage objects to merge (must have same path).

    Returns:
        Merged FileCoverage with max hits across all inputs.
    """
    files_list = list(files)
    if not files_list:
        raise ValueError("Cannot merge empty file coverage list")

    path = files_list[0].path

    # Merge line coverage
    merged_lines: dict[int, int] = {}
    for fc in files_list:
        for line_num, hits in fc.lines.items():
            merged_lines[line_num] = max(merged_lines.get(line_num, 0), hits)

    # Merge branch coverage - keyed by (line, block_id, branch_id)
    branch_key_to_hits: dict[tuple[int, int, int], int] = {}
    for fc in files_list:
        for branch in fc.branches:
            key = (branch.line, branch.block_id, branch.branch_id)
            branch_key_to_hits[key] = max(branch_key_to_hits.get(key, 0), branch.hits)

    merged_branches = [
        BranchCoverage(line=line, block_id=block_id, branch_id=branch_id, hits=hits)
        for (line, block_id, branch_id), hits in sorted(branch_key_to_hits.items())
    ]

    # Merge function coverage - keyed by name
    func_data: dict[str, tuple[int, int]] = {}  # name -> (start_line, max_hits)
    for fc in files_list:
        for name, func in fc.functions.items():
            if name in func_data:
                existing_line, existing_hits = func_data[name]
                # Keep earliest start line, max hits
                func_data[name] = (
                    min(existing_line, func.start_line),
                    max(existing_hits, func.hits),
                )
            else:
                func_data[name] = (func.start_line, func.hits)

    merged_functions = {
        name: FunctionCoverage(name=name, start_line=start_line, hits=hits)
        for name, (start_line, hits) in func_data.items()
    }

    # Build result
    result = FileCoverage(path=path)
    result.lines.update(merged_lines)
    result.branches.extend(merged_branches)
    result.functions.update(merged_functions)

    return result


def merge_reports(reports: Iterable[CoverageReport]) -> CoverageReport:
    """Merge multiple CoverageReport objects.

    Uses max-hit semantics across all reports. Files present in multiple
    reports are merged; files present in only one are included as-is.

    Args:
        reports: CoverageReport objects to merge.

    Returns:
        Merged CoverageReport with max hits across all inputs.
    """
    reports_list = list(reports)

    if not reports_list:
        return CoverageReport(source_format="merged", files={})

    if len(reports_list) == 1:
        return reports_list[0]

    # Group files by path
    files_by_path: dict[str, list[FileCoverage]] = {}
    source_formats: set[str] = set()

    for report in reports_list:
        source_formats.add(report.source_format)
        for path, fc in report.files.items():
            if path not in files_by_path:
                files_by_path[path] = []
            files_by_path[path].append(fc)

    # Merge each file group
    merged_files: dict[str, FileCoverage] = {}
    for path, file_list in files_by_path.items():
        if len(file_list) == 1:
            merged_files[path] = file_list[0]
        else:
            merged_files[path] = merge_file_coverage(file_list)

    # Determine source format
    source_format = source_formats.pop() if len(source_formats) == 1 else "merged"

    return CoverageReport(source_format=source_format, files=merged_files)


def merge(*reports: CoverageReport) -> CoverageReport:
    """Convenience function to merge reports as varargs.

    Args:
        *reports: CoverageReport objects to merge.

    Returns:
        Merged CoverageReport.
    """
    return merge_reports(reports)
