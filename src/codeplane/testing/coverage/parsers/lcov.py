"""LCOV format parser.

LCOV format is a plain text format with records like:
- SF:<source file path>
- DA:<line>,<hit count>
- BRDA:<line>,<block>,<branch>,<taken>
- FN:<line>,<name>
- FNDA:<hit count>,<name>
- LF:<lines found>
- LH:<lines hit>
- BRF:<branches found>
- BRH:<branches hit>
- FNF:<functions found>
- FNH:<functions hit>
- end_of_record

Used by: pytest-cov, cargo-llvm-cov, gcov, dart test
"""

import contextlib
from pathlib import Path

from codeplane.testing.coverage.models import (
    BranchCoverage,
    CoverageParseError,
    CoverageReport,
    FileCoverage,
    FunctionCoverage,
)


class LcovParser:
    """Parser for LCOV format coverage files."""

    @property
    def format_id(self) -> str:
        return "lcov"

    def can_parse(self, path: Path) -> bool:
        """Check if file looks like LCOV format."""
        if not path.is_file():
            return False
        # Check extension
        if path.suffix in (".info", ".lcov"):
            return True
        # Content sniff: look for SF: at start of a line
        try:
            with path.open() as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("SF:"):
                        return True
                    # Stop after first non-empty line that's not a comment
                    if stripped and not stripped.startswith("#"):
                        break
        except (OSError, UnicodeDecodeError):
            pass
        return False

    def parse(self, path: Path, *, base_path: Path | None = None) -> CoverageReport:
        """Parse LCOV file into CoverageReport."""
        if not path.exists():
            raise CoverageParseError(f"LCOV file not found: {path}")

        try:
            content = path.read_text()
        except (OSError, UnicodeDecodeError) as e:
            raise CoverageParseError(f"Failed to read LCOV file: {e}") from e

        files: dict[str, FileCoverage] = {}
        current_file: FileCoverage | None = None
        # Track function names for FNDA matching
        fn_names: dict[int, str] = {}  # line -> name

        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue

            if line.startswith("SF:"):
                # Start new source file
                file_path = line[3:]
                if base_path:
                    # Path not under base_path -> use as-is
                    with contextlib.suppress(ValueError):
                        file_path = str(Path(file_path).relative_to(base_path))
                current_file = FileCoverage(path=file_path)
                fn_names = {}

            elif line.startswith("DA:"):
                # Line data: DA:line,hits[,checksum]
                if current_file is None:
                    continue
                parts = line[3:].split(",")
                if len(parts) >= 2:
                    try:
                        line_num = int(parts[0])
                        hits_str = parts[1]
                        # Handle '-' as 0 (some tools use this)
                        hits = 0 if hits_str == "-" else int(hits_str)
                        current_file.lines[line_num] = hits
                    except ValueError:
                        pass

            elif line.startswith("BRDA:"):
                # Branch data: BRDA:line,block,branch,taken
                if current_file is None:
                    continue
                parts = line[5:].split(",")
                if len(parts) >= 4:
                    try:
                        line_num = int(parts[0])
                        block_id = int(parts[1])
                        branch_id = int(parts[2])
                        taken_str = parts[3]
                        # '-' means branch not taken
                        hits = 0 if taken_str == "-" else int(taken_str)
                        current_file.branches.append(
                            BranchCoverage(
                                line=line_num,
                                block_id=block_id,
                                branch_id=branch_id,
                                hits=hits,
                            )
                        )
                    except ValueError:
                        pass

            elif line.startswith("FN:"):
                # Function definition: FN:line,name
                parts = line[3:].split(",", 1)
                if len(parts) >= 2:
                    try:
                        line_num = int(parts[0])
                        name = parts[1]
                        fn_names[line_num] = name
                    except ValueError:
                        pass

            elif line.startswith("FNDA:"):
                # Function hits: FNDA:hits,name
                if current_file is None:
                    continue
                parts = line[5:].split(",", 1)
                if len(parts) >= 2:
                    try:
                        hits = int(parts[0])
                        name = parts[1]
                        # Find line number from FN records
                        start_line = 0
                        for ln, fn in fn_names.items():
                            if fn == name:
                                start_line = ln
                                break
                        current_file.functions[name] = FunctionCoverage(
                            name=name,
                            start_line=start_line,
                            hits=hits,
                        )
                    except ValueError:
                        pass

            elif line == "end_of_record":
                # End of file record
                if current_file is not None:
                    files[current_file.path] = current_file
                current_file = None
                fn_names = {}

        # Handle file without end_of_record
        if current_file is not None:
            files[current_file.path] = current_file

        return CoverageReport(source_format="lcov", files=files)
