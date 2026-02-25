"""Istanbul/NYC JSON format parser.

Istanbul (used by Jest, Vitest, NYC) produces JSON coverage:
- coverage-final.json: Per-file detailed coverage
- coverage-summary.json: Aggregate summary (not used for per-line data)

Structure of coverage-final.json:
{
  "/path/to/file.js": {
    "path": "/path/to/file.js",
    "statementMap": { "0": {"start": {"line": 1, "column": 0}, "end": ...}, ... },
    "s": { "0": 1, "1": 0, ... },  // statement hit counts
    "branchMap": { "0": {"type": "if", "locations": [...], "line": 5}, ... },
    "b": { "0": [1, 0], ... },  // branch hit counts per location
    "fnMap": { "0": {"name": "foo", "decl": {"start": {"line": 1}}, ...}, ... },
    "f": { "0": 1, ... }  // function hit counts
  }
}
"""

import contextlib
import json
from pathlib import Path

from codeplane.testing.coverage.models import (
    BranchCoverage,
    CoverageParseError,
    CoverageReport,
    FileCoverage,
    FunctionCoverage,
)


class IstanbulParser:
    """Parser for Istanbul JSON format."""

    @property
    def format_id(self) -> str:
        return "istanbul"

    def can_parse(self, path: Path) -> bool:
        """Check if path contains Istanbul coverage data."""
        if path.is_dir():
            # Check for coverage-final.json
            return bool((path / "coverage-final.json").exists())

        if not path.is_file():
            return False

        if path.name == "coverage-final.json":
            return True

        # Content sniff for JSON with statementMap
        try:
            with path.open() as f:
                header = f.read(2048)
                if '"statementMap"' in header or '"fnMap"' in header:
                    return True
        except (OSError, UnicodeDecodeError):
            pass
        return False

    def parse(self, path: Path, *, base_path: Path | None = None) -> CoverageReport:
        """Parse Istanbul JSON into CoverageReport."""
        if not path.exists():
            raise CoverageParseError(f"Istanbul path not found: {path}")

        # Find the coverage JSON
        if path.is_dir():
            json_file = path / "coverage-final.json"
            if not json_file.exists():
                raise CoverageParseError(f"coverage-final.json not found in {path}")
        else:
            json_file = path

        try:
            with json_file.open() as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise CoverageParseError(f"Failed to parse Istanbul JSON: {e}") from e

        files: dict[str, FileCoverage] = {}

        for file_path, file_data in data.items():
            # Normalize path
            normalized_path = file_path
            if base_path:
                with contextlib.suppress(ValueError):
                    normalized_path = str(Path(file_path).relative_to(base_path))

            file_cov = FileCoverage(path=normalized_path)

            # Extract line coverage from statements
            statement_map = file_data.get("statementMap", {})
            statement_hits = file_data.get("s", {})

            for stmt_id, stmt_info in statement_map.items():
                start = stmt_info.get("start", {})
                end = stmt_info.get("end", {})
                start_line = start.get("line", 0)
                end_line = end.get("line", start_line)
                hits = statement_hits.get(stmt_id, 0)

                # Mark all lines in statement range
                for line_num in range(start_line, end_line + 1):
                    file_cov.lines[line_num] = max(file_cov.lines.get(line_num, 0), hits)

            # Extract branch coverage
            branch_map = file_data.get("branchMap", {})
            branch_hits = file_data.get("b", {})

            for branch_id, branch_info in branch_map.items():
                line = branch_info.get("line", 0)
                # Fallback to first location if line not set
                if not line:
                    locations = branch_info.get("locations", [])
                    if locations:
                        line = locations[0].get("start", {}).get("line", 0)

                hits_array = branch_hits.get(branch_id, [])
                for idx, hits in enumerate(hits_array):
                    file_cov.branches.append(
                        BranchCoverage(
                            line=line,
                            block_id=int(branch_id),
                            branch_id=idx,
                            hits=hits,
                        )
                    )

            # Extract function coverage
            fn_map = file_data.get("fnMap", {})
            fn_hits = file_data.get("f", {})

            for fn_id, fn_info in fn_map.items():
                name = fn_info.get("name", f"anonymous_{fn_id}")
                decl = fn_info.get("decl", {})
                start_line = decl.get("start", {}).get("line", 0)
                hits = fn_hits.get(fn_id, 0)

                file_cov.functions[name] = FunctionCoverage(
                    name=name,
                    start_line=start_line,
                    hits=hits,
                )

            files[normalized_path] = file_cov

        return CoverageReport(source_format="istanbul", files=files)
