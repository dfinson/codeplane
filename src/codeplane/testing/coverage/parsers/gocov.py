"""Go coverage profile parser.

Go test produces coverage profiles with format:
mode: set|count|atomic
<package>/<file>:<startline>.<startcol>,<endline>.<endcol> <numstmt> <count>

Example:
mode: set
github.com/user/pkg/main.go:10.2,12.16 3 1
github.com/user/pkg/main.go:15.2,20.16 5 0

- mode: set (0/1), count (hit count), atomic (thread-safe count)
- numstmt: number of statements in block
- count: execution count (0 = not covered)
"""

import contextlib
from pathlib import Path

from codeplane.testing.coverage.models import (
    CoverageParseError,
    CoverageReport,
    FileCoverage,
)


class GocovParser:
    """Parser for Go coverage profiles."""

    @property
    def format_id(self) -> str:
        return "gocov"

    def can_parse(self, path: Path) -> bool:
        """Check if file looks like Go coverage profile."""
        if not path.is_file():
            return False

        # Check extension
        if path.suffix == ".out":
            return True

        # Content sniff for mode: line
        try:
            with path.open() as f:
                first_line = f.readline().strip()
                if first_line.startswith("mode:"):
                    return True
        except (OSError, UnicodeDecodeError):
            pass
        return False

    def parse(self, path: Path, *, base_path: Path | None = None) -> CoverageReport:
        """Parse Go coverage profile into CoverageReport."""
        if not path.exists():
            raise CoverageParseError(f"Go coverage file not found: {path}")

        try:
            content = path.read_text()
        except (OSError, UnicodeDecodeError) as e:
            raise CoverageParseError(f"Failed to read Go coverage: {e}") from e

        lines = content.strip().splitlines()
        if not lines:
            return CoverageReport(source_format="gocov", files={})

        # First line should be mode
        mode_line = lines[0].strip()
        if not mode_line.startswith("mode:"):
            raise CoverageParseError("Invalid Go coverage: missing mode line")

        files: dict[str, FileCoverage] = {}

        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue

            # Parse: path:start.col,end.col numstmt count
            try:
                # Split on space to get path:range, numstmt, count
                parts = line.split()
                if len(parts) != 3:
                    continue

                path_range = parts[0]
                # numstmt = int(parts[1])  # Not used for per-line
                count = int(parts[2])

                # Split path:range
                colon_idx = path_range.rfind(":")
                if colon_idx == -1:
                    continue

                file_path = path_range[:colon_idx]
                range_part = path_range[colon_idx + 1 :]

                # Parse range: start.col,end.col
                range_parts = range_part.split(",")
                if len(range_parts) != 2:
                    continue

                start = range_parts[0].split(".")
                end = range_parts[1].split(".")

                start_line = int(start[0])
                end_line = int(end[0])

                # Normalize path
                if base_path:
                    with contextlib.suppress(ValueError):
                        file_path = str(Path(file_path).relative_to(base_path))

                # Get or create file coverage
                if file_path not in files:
                    files[file_path] = FileCoverage(path=file_path)
                file_cov = files[file_path]

                # Mark all lines in range
                for line_num in range(start_line, end_line + 1):
                    # Use max to handle overlapping blocks
                    file_cov.lines[line_num] = max(file_cov.lines.get(line_num, 0), count)

            except (ValueError, IndexError):
                continue

        return CoverageReport(source_format="gocov", files=files)
