"""SimpleCov JSON format parser.

SimpleCov is the standard Ruby coverage tool.
It produces JSON with per-file line arrays.

Structure of .resultset.json:
{
  "RSpec": {
    "coverage": {
      "/path/to/file.rb": {
        "lines": [null, 1, 2, 0, null, ...]
      }
    },
    "timestamp": 1234567890
  }
}

Or older format (array directly):
{
  "RSpec": {
    "coverage": {
      "/path/to/file.rb": [null, 1, 2, 0, null, ...]
    }
  }
}

Line array semantics:
- null: non-executable line (comment, blank, etc.)
- 0: executable but not executed
- N > 0: executed N times

Array is 0-indexed but represents 1-indexed lines (element 0 = line 1).
"""

import contextlib
import json
from pathlib import Path

from codeplane.testing.coverage.models import (
    CoverageParseError,
    CoverageReport,
    FileCoverage,
)


class SimplecovParser:
    """Parser for SimpleCov JSON format."""

    @property
    def format_id(self) -> str:
        return "simplecov"

    def can_parse(self, path: Path) -> bool:
        """Check if path contains SimpleCov coverage data."""
        if path.is_dir():
            # Check for SimpleCov results file
            return (path / ".resultset.json").exists() or (
                path / "coverage" / ".resultset.json"
            ).exists()

        if not path.is_file():
            return False

        if path.name == ".resultset.json":
            return True

        # Content sniff for SimpleCov JSON structure
        try:
            with path.open() as f:
                header = f.read(4096)
                # Look for SimpleCov structure markers
                if '"coverage"' in header and ('"lines":' in header or '".rb":' in header):
                    return True
        except (OSError, UnicodeDecodeError):
            pass
        return False

    def _find_json_file(self, path: Path) -> Path:
        """Find the SimpleCov results file."""
        if path.is_file():
            return path

        if (path / ".resultset.json").exists():
            return path / ".resultset.json"

        if (path / "coverage" / ".resultset.json").exists():
            return path / "coverage" / ".resultset.json"

        raise CoverageParseError(f"No SimpleCov results found in {path}")

    def parse(self, path: Path, *, base_path: Path | None = None) -> CoverageReport:
        """Parse SimpleCov JSON into CoverageReport."""
        if not path.exists():
            raise CoverageParseError(f"SimpleCov path not found: {path}")

        json_file = self._find_json_file(path)

        try:
            with json_file.open() as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise CoverageParseError(f"Failed to parse SimpleCov JSON: {e}") from e

        files: dict[str, FileCoverage] = {}

        # Process each test framework's results
        for _framework_name, framework_data in data.items():
            if not isinstance(framework_data, dict):
                continue

            coverage_data = framework_data.get("coverage", {})
            if not isinstance(coverage_data, dict):
                continue

            for file_path, file_data in coverage_data.items():
                # Normalize path
                normalized_path = file_path.replace("\\", "/")
                if base_path:
                    with contextlib.suppress(ValueError):
                        normalized_path = str(Path(normalized_path).relative_to(base_path))

                if normalized_path not in files:
                    files[normalized_path] = FileCoverage(path=normalized_path)
                file_cov = files[normalized_path]

                # Extract lines array
                if isinstance(file_data, dict):
                    # New format: {"lines": [...]}
                    lines_array = file_data.get("lines", [])
                elif isinstance(file_data, list):
                    # Old format: direct array
                    lines_array = file_data
                else:
                    continue

                # Process line array (0-indexed array = 1-indexed lines)
                for idx, count in enumerate(lines_array):
                    if count is None:
                        # Non-executable line
                        continue

                    line_num = idx + 1  # Convert to 1-indexed

                    # Merge with max semantics
                    file_cov.lines[line_num] = max(file_cov.lines.get(line_num, 0), count)

        return CoverageReport(source_format="simplecov", files=files)
