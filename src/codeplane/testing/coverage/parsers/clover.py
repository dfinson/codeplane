"""Clover XML format parser.

Clover is used by multiple tools:
- PHP: phpunit --coverage-clover
- Kotlin: kover
- Java: OpenClover (historical)

Structure:
<coverage generated="..." clover="...">
  <project timestamp="...">
    <metrics ...aggregate stats.../>
    <package name="com.example">
      <file name="Foo.php" path="/path/to/Foo.php">
        <class name="FooClass" .../>
        <line num="1" type="stmt" count="1"/>
        <line num="5" type="cond" count="0" truecount="1" falsecount="0"/>
        <line num="10" type="method" name="bar" .../>
        <metrics ...file stats.../>
      </file>
    </package>
  </project>
</coverage>

Line types:
- stmt: statement line
- cond: conditional (branch)
- method: method declaration
"""

import contextlib
import xml.etree.ElementTree as ET
from pathlib import Path

from codeplane.testing.coverage.models import (
    BranchCoverage,
    CoverageParseError,
    CoverageReport,
    FileCoverage,
    FunctionCoverage,
)


class CloverParser:
    """Parser for Clover XML format."""

    @property
    def format_id(self) -> str:
        return "clover"

    def can_parse(self, path: Path) -> bool:
        """Check if path contains Clover coverage data."""
        if path.is_dir():
            for name in ["clover.xml", "coverage.xml", "coverage-clover.xml"]:
                if (path / name).exists():
                    return True
            return False

        if not path.is_file():
            return False

        if "clover" in path.name.lower():
            return True

        # Content sniff for Clover XML
        try:
            with path.open("rb") as f:
                header = f.read(2048).decode("utf-8", errors="ignore")
                if '<coverage generated="' in header or 'clover="' in header:
                    return True
                if '<project timestamp="' in header and '<line num="' in header:
                    return True
        except (OSError, UnicodeDecodeError):
            pass
        return False

    def _find_xml_file(self, path: Path) -> Path:
        """Find the actual XML file."""
        if path.is_file():
            return path

        for name in ["clover.xml", "coverage.xml", "coverage-clover.xml"]:
            candidate = path / name
            if candidate.exists():
                return candidate

        raise CoverageParseError(f"No Clover XML found in {path}")

    def parse(self, path: Path, *, base_path: Path | None = None) -> CoverageReport:
        """Parse Clover XML into CoverageReport."""
        if not path.exists():
            raise CoverageParseError(f"Clover path not found: {path}")

        xml_file = self._find_xml_file(path)

        try:
            tree = ET.parse(xml_file)
        except ET.ParseError as e:
            raise CoverageParseError(f"Invalid Clover XML: {e}") from e

        root = tree.getroot()
        files: dict[str, FileCoverage] = {}

        # Find all file elements
        for file_elem in root.findall(".//file"):
            file_path = file_elem.get("path") or file_elem.get("name", "")
            if not file_path:
                continue

            # Normalize path
            normalized_path = file_path.replace("\\", "/")
            if base_path:
                with contextlib.suppress(ValueError):
                    normalized_path = str(Path(normalized_path).relative_to(base_path))

            file_cov = FileCoverage(path=normalized_path)

            # Process line elements
            for line in file_elem.findall("line"):
                num = int(line.get("num", 0))
                if num <= 0:
                    continue

                line_type = line.get("type", "stmt")
                count = int(line.get("count", 0))

                if line_type == "method":
                    # Method declaration
                    method_name = line.get("name", f"method_{num}")
                    file_cov.lines[num] = count
                    file_cov.functions[method_name] = FunctionCoverage(
                        name=method_name,
                        start_line=num,
                        hits=count,
                    )
                elif line_type == "cond":
                    # Conditional/branch line
                    file_cov.lines[num] = count

                    # Extract branch info
                    true_count = int(line.get("truecount", 0))
                    false_count = int(line.get("falsecount", 0))

                    # True branch
                    file_cov.branches.append(
                        BranchCoverage(
                            line=num,
                            block_id=0,
                            branch_id=0,
                            hits=true_count,
                        )
                    )
                    # False branch
                    file_cov.branches.append(
                        BranchCoverage(
                            line=num,
                            block_id=0,
                            branch_id=1,
                            hits=false_count,
                        )
                    )
                else:
                    # Statement line
                    file_cov.lines[num] = count

            files[normalized_path] = file_cov

        return CoverageReport(source_format="clover", files=files)
