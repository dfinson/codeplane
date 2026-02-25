"""Tests for coverage parsing, merging, and reporting.

Tests the new coverage/ package:
- report.py: _compress_ranges, build_compact_summary
- merge.py: merge_file_coverage, merge
- models.py: FileCoverage, CoverageReport properties
- parsers: lcov, cobertura formats
"""

from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest

from codeplane.testing.coverage import (
    CoverageParseError,
    CoverageReport,
    FileCoverage,
    build_compact_summary,
    merge,
    parse_artifact,
)
from codeplane.testing.coverage.report import _compress_ranges, _path_matches

# =============================================================================
# _compress_ranges tests
# =============================================================================


class TestCompressRanges:
    """Tests for _compress_ranges helper."""

    def test_empty_list(self) -> None:
        assert _compress_ranges([]) == ""

    def test_single_line(self) -> None:
        assert _compress_ranges([5]) == "5"

    def test_two_consecutive_lines(self) -> None:
        assert _compress_ranges([1, 2]) == "1-2"

    def test_two_non_consecutive_lines(self) -> None:
        assert _compress_ranges([1, 5]) == "1,5"

    def test_mixed_ranges_and_singles(self) -> None:
        assert _compress_ranges([1, 2, 3, 5, 7, 8, 9]) == "1-3,5,7-9"

    def test_all_consecutive(self) -> None:
        assert _compress_ranges([10, 11, 12, 13, 14]) == "10-14"

    def test_all_separate(self) -> None:
        assert _compress_ranges([1, 3, 5, 7]) == "1,3,5,7"

    def test_long_gap(self) -> None:
        assert _compress_ranges([1, 2, 100, 101, 102]) == "1-2,100-102"


# =============================================================================
# _path_matches tests
# =============================================================================


class TestPathMatches:
    """Tests for _path_matches helper."""

    def test_exact_match(self) -> None:
        assert _path_matches("src/foo.py", {"src/foo.py"})

    def test_no_match(self) -> None:
        assert not _path_matches("src/foo.py", {"src/bar.py"})

    def test_leading_dot_slash_ignored(self) -> None:
        assert _path_matches("./src/foo.py", {"src/foo.py"})
        assert _path_matches("src/foo.py", {"./src/foo.py"})

    def test_suffix_match_with_slash(self) -> None:
        # Absolute path matching relative
        assert _path_matches("/workspace/repo/src/foo.py", {"src/foo.py"})


# =============================================================================
# FileCoverage model tests
# =============================================================================


class TestFileCoverage:
    """Tests for FileCoverage model properties."""

    def test_empty_file(self) -> None:
        fc = FileCoverage(path="empty.py")
        assert fc.lines_found == 0
        assert fc.lines_hit == 0
        assert fc.line_rate == 0.0
        assert fc.uncovered_lines == []

    def test_fully_covered(self) -> None:
        fc = FileCoverage(path="full.py", lines={1: 5, 2: 3, 3: 1})
        assert fc.lines_found == 3
        assert fc.lines_hit == 3
        assert fc.line_rate == 1.0
        assert fc.uncovered_lines == []

    def test_partially_covered(self) -> None:
        fc = FileCoverage(path="partial.py", lines={1: 5, 2: 0, 3: 1, 4: 0})
        assert fc.lines_found == 4
        assert fc.lines_hit == 2
        assert fc.line_rate == 0.5
        assert fc.uncovered_lines == [2, 4]

    def test_zero_covered(self) -> None:
        fc = FileCoverage(path="zero.py", lines={1: 0, 2: 0, 3: 0})
        assert fc.lines_found == 3
        assert fc.lines_hit == 0
        assert fc.line_rate == 0.0
        assert fc.uncovered_lines == [1, 2, 3]


# =============================================================================
# CoverageReport model tests
# =============================================================================


class TestCoverageReport:
    """Tests for CoverageReport model properties."""

    def test_empty_report(self) -> None:
        report = CoverageReport(source_format="lcov")
        summary = report.summary
        assert summary.lines_found == 0
        assert summary.line_rate == 0.0

    def test_single_file_report(self) -> None:
        fc = FileCoverage(path="foo.py", lines={1: 5, 2: 0, 3: 1})
        report = CoverageReport(source_format="lcov", files={"foo.py": fc})
        summary = report.summary
        assert summary.lines_found == 3
        assert summary.lines_hit == 2
        assert pytest.approx(summary.line_rate, 0.01) == 2 / 3

    def test_multi_file_report(self) -> None:
        fc1 = FileCoverage(path="a.py", lines={1: 1, 2: 1})  # 2/2
        fc2 = FileCoverage(path="b.py", lines={1: 0, 2: 0, 3: 0})  # 0/3
        report = CoverageReport(source_format="lcov", files={"a.py": fc1, "b.py": fc2})
        summary = report.summary
        assert summary.lines_found == 5
        assert summary.lines_hit == 2
        assert pytest.approx(summary.line_rate, 0.01) == 2 / 5


# =============================================================================
# merge tests
# =============================================================================


class TestMerge:
    """Tests for merge function."""

    def test_merge_single_report(self) -> None:
        fc = FileCoverage(path="foo.py", lines={1: 5, 2: 0})
        report = CoverageReport(source_format="lcov", files={"foo.py": fc})
        merged = merge(report)
        assert merged.files["foo.py"].lines == {1: 5, 2: 0}

    def test_merge_takes_max_hits(self) -> None:
        fc1 = FileCoverage(path="foo.py", lines={1: 5, 2: 0, 3: 1})
        fc2 = FileCoverage(path="foo.py", lines={1: 0, 2: 3, 3: 10})
        r1 = CoverageReport(source_format="lcov", files={"foo.py": fc1})
        r2 = CoverageReport(source_format="lcov", files={"foo.py": fc2})
        merged = merge(r1, r2)
        # Max of each line
        assert merged.files["foo.py"].lines == {1: 5, 2: 3, 3: 10}

    def test_merge_disjoint_files(self) -> None:
        fc1 = FileCoverage(path="a.py", lines={1: 1})
        fc2 = FileCoverage(path="b.py", lines={1: 2})
        r1 = CoverageReport(source_format="lcov", files={"a.py": fc1})
        r2 = CoverageReport(source_format="lcov", files={"b.py": fc2})
        merged = merge(r1, r2)
        assert "a.py" in merged.files
        assert "b.py" in merged.files
        assert merged.files["a.py"].lines == {1: 1}
        assert merged.files["b.py"].lines == {1: 2}


# =============================================================================
# build_compact_summary tests
# =============================================================================


class TestBuildCompactSummary:
    """Tests for build_compact_summary function."""

    def test_empty_report(self) -> None:
        report = CoverageReport(source_format="lcov")
        result = build_compact_summary(report)
        assert result == "coverage: no data"

    def test_fully_covered(self) -> None:
        fc = FileCoverage(path="foo.py", lines={1: 5, 2: 3, 3: 1})
        report = CoverageReport(source_format="lcov", files={"foo.py": fc})
        result = build_compact_summary(report)
        assert result == "coverage: 100% (3/3 lines)"

    def test_partially_covered_shows_uncovered(self) -> None:
        fc = FileCoverage(path="src/foo.py", lines={1: 5, 2: 0, 3: 1, 4: 0, 5: 0})
        report = CoverageReport(source_format="lcov", files={"src/foo.py": fc})
        result = build_compact_summary(report)
        assert "coverage: 40% (2/5 lines)" in result
        assert "uncovered: foo.py:2,4-5" in result

    def test_multiple_files(self) -> None:
        fc1 = FileCoverage(path="a.py", lines={1: 0, 2: 0})
        fc2 = FileCoverage(path="b.py", lines={1: 0})
        report = CoverageReport(source_format="lcov", files={"a.py": fc1, "b.py": fc2})
        result = build_compact_summary(report)
        assert "coverage: 0% (0/3 lines)" in result
        assert "a.py:1-2" in result
        assert "b.py:1" in result
        assert " | " in result  # Multiple files separated by |

    def test_filter_paths(self) -> None:
        fc1 = FileCoverage(path="src/a.py", lines={1: 0, 2: 0})
        fc2 = FileCoverage(path="src/b.py", lines={1: 1, 2: 1})
        report = CoverageReport(source_format="lcov", files={"src/a.py": fc1, "src/b.py": fc2})
        # Only include b.py
        result = build_compact_summary(report, filter_paths={"src/b.py"})
        assert "coverage: 100% (2/2 lines)" in result
        assert "a.py" not in result


# =============================================================================
# Parser tests - LCOV format
# =============================================================================


class TestLcovParser:
    """Tests for LCOV format parser."""

    def test_parse_simple_lcov(self) -> None:
        lcov_content = """\
TN:Test
SF:src/foo.py
DA:1,5
DA:2,0
DA:3,1
LF:3
LH:2
end_of_record
"""
        with NamedTemporaryFile(mode="w", suffix=".info", delete=False) as f:
            f.write(lcov_content)
            f.flush()
            report = parse_artifact(Path(f.name))

        assert report.source_format == "lcov"
        assert "src/foo.py" in report.files
        fc = report.files["src/foo.py"]
        assert fc.lines == {1: 5, 2: 0, 3: 1}

    def test_parse_lcov_with_branches(self) -> None:
        lcov_content = """\
TN:Test
SF:src/bar.py
DA:10,1
BRDA:10,0,0,1
BRDA:10,0,1,0
end_of_record
"""
        with NamedTemporaryFile(mode="w", suffix=".info", delete=False) as f:
            f.write(lcov_content)
            f.flush()
            report = parse_artifact(Path(f.name))

        fc = report.files["src/bar.py"]
        assert len(fc.branches) == 2
        assert fc.branches_hit == 1
        assert fc.branches_found == 2

    def test_parse_lcov_with_functions(self) -> None:
        lcov_content = """\
TN:Test
SF:src/baz.py
FN:5,my_function
FNDA:3,my_function
DA:5,3
DA:6,3
end_of_record
"""
        with NamedTemporaryFile(mode="w", suffix=".info", delete=False) as f:
            f.write(lcov_content)
            f.flush()
            report = parse_artifact(Path(f.name))

        fc = report.files["src/baz.py"]
        assert "my_function" in fc.functions
        assert fc.functions["my_function"].hits == 3
        assert fc.functions["my_function"].start_line == 5


# =============================================================================
# Parser tests - Cobertura format
# =============================================================================


class TestCoberturaParser:
    """Tests for Cobertura XML format parser."""

    def test_parse_simple_cobertura(self) -> None:
        cobertura_xml = """\
<?xml version="1.0" ?>
<coverage version="1.0">
    <packages>
        <package name="src">
            <classes>
                <class name="foo.py" filename="src/foo.py" line-rate="0.66">
                    <lines>
                        <line number="1" hits="5"/>
                        <line number="2" hits="0"/>
                        <line number="3" hits="1"/>
                    </lines>
                </class>
            </classes>
        </package>
    </packages>
</coverage>
"""
        with NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write(cobertura_xml)
            f.flush()
            report = parse_artifact(Path(f.name), format_id="cobertura")

        assert report.source_format == "cobertura"
        assert "src/foo.py" in report.files
        fc = report.files["src/foo.py"]
        assert fc.lines == {1: 5, 2: 0, 3: 1}


# =============================================================================
# Error handling tests
# =============================================================================


class TestParserErrors:
    """Tests for parser error handling."""

    def test_invalid_format_raises(self) -> None:
        with NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("garbage data that is not coverage")
            f.flush()
            with pytest.raises(CoverageParseError):
                parse_artifact(Path(f.name))

    def test_missing_file_raises(self) -> None:
        # parse_artifact wraps file errors in CoverageParseError
        with pytest.raises(CoverageParseError):
            parse_artifact(Path("/nonexistent/coverage.info"))
