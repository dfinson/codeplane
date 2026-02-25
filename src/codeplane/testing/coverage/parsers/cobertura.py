"""Cobertura XML format parser.

Cobertura XML is used by many coverage tools across languages:
- Python: coverage.py
- .NET: coverlet
- Go: gocover-cobertura
- Java: some tools export to Cobertura format

Structure:
<coverage line-rate="0.85" branch-rate="0.50" ...>
  <packages>
    <package name="...">
      <classes>
        <class name="..." filename="..." line-rate="...">
          <methods>
            <method name="..." signature="..." line-rate="...">
              <lines>
                <line number="1" hits="1" branch="false"/>
                <line number="2" hits="0" branch="true" condition-coverage="50% (1/2)"/>
              </lines>
            </method>
          </methods>
          <lines>
            <line number="1" hits="1"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""

import contextlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from codeplane.testing.coverage.models import (
    BranchCoverage,
    CoverageParseError,
    CoverageReport,
    FileCoverage,
    FunctionCoverage,
)


class CoberturaParser:
    """Parser for Cobertura XML format."""

    @property
    def format_id(self) -> str:
        return "cobertura"

    def can_parse(self, path: Path) -> bool:
        """Check if file looks like Cobertura XML."""
        if path.is_dir():
            # Check common filenames
            for name in ["coverage.xml", "cobertura.xml", "coverage.cobertura.xml"]:
                if (path / name).exists():
                    return True
            # Check for nested TestResults structure (.NET)
            return bool(any(path.glob("**/coverage.cobertura.xml")))

        if not path.is_file():
            return False

        # Content sniff: look for <coverage> root with line-rate attribute
        try:
            with path.open("rb") as f:
                # Read first 2KB
                header = f.read(2048).decode("utf-8", errors="ignore")
                # Look for coverage element with line-rate (distinguishes from JaCoCo/Clover)
                if (
                    "<coverage" in header
                    and "line-rate=" in header
                    and "<CoverletCoverage" not in header
                    and "<report name=" not in header
                ):
                    return True
        except (OSError, UnicodeDecodeError):
            pass
        return False

    def _find_xml_file(self, path: Path) -> Path:
        """Find the actual XML file from path or directory."""
        if path.is_file():
            return path

        # Check common filenames
        for name in ["coverage.cobertura.xml", "cobertura.xml", "coverage.xml"]:
            candidate = path / name
            if candidate.exists():
                return candidate

        # Check TestResults structure (.NET)
        cobertura_files = list(path.glob("**/coverage.cobertura.xml"))
        if cobertura_files:
            return cobertura_files[0]

        raise CoverageParseError(f"No Cobertura XML found in {path}")

    def parse(self, path: Path, *, base_path: Path | None = None) -> CoverageReport:
        """Parse Cobertura XML into CoverageReport."""
        if not path.exists():
            raise CoverageParseError(f"Cobertura path not found: {path}")

        xml_file = self._find_xml_file(path)

        try:
            tree = ET.parse(xml_file)
        except ET.ParseError as e:
            raise CoverageParseError(f"Invalid Cobertura XML: {e}") from e

        root = tree.getroot()

        # Strip namespace if present
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        files: dict[str, FileCoverage] = {}

        # Process all classes
        for cls in root.findall(".//class"):
            filename = cls.get("filename", "")
            if not filename:
                continue

            # Normalize path
            if base_path:
                with contextlib.suppress(ValueError):
                    filename = str(Path(filename).relative_to(base_path))

            # Get or create file coverage
            if filename not in files:
                files[filename] = FileCoverage(path=filename)
            file_cov = files[filename]

            # Extract method/function coverage from methods element
            for method in cls.findall(".//method"):
                method_name = method.get("name", "")
                if not method_name:
                    continue

                # Get method line range to find start line
                method_lines = method.findall(".//line")
                start_line = 0
                total_hits = 0
                if method_lines:
                    start_line = int(method_lines[0].get("number", 0))
                    total_hits = sum(int(ln.get("hits", 0)) for ln in method_lines)

                if method_name not in file_cov.functions:
                    file_cov.functions[method_name] = FunctionCoverage(
                        name=method_name,
                        start_line=start_line,
                        hits=total_hits,
                    )

            # Extract line coverage (from class-level lines, not method-level to avoid dupes)
            for line in cls.findall("./lines/line"):
                line_num = int(line.get("number", 0))
                hits = int(line.get("hits", 0))
                # Use max if line already exists (from another method)
                file_cov.lines[line_num] = max(file_cov.lines.get(line_num, 0), hits)

                # Extract branch coverage if present
                if line.get("branch") == "true":
                    condition_coverage = line.get("condition-coverage", "")
                    # Parse "50% (1/2)" format
                    match = re.search(r"\((\d+)/(\d+)\)", condition_coverage)
                    if match:
                        taken = int(match.group(1))
                        total = int(match.group(2))
                        for branch_id in range(total):
                            file_cov.branches.append(
                                BranchCoverage(
                                    line=line_num,
                                    block_id=0,
                                    branch_id=branch_id,
                                    hits=1 if branch_id < taken else 0,
                                )
                            )

        return CoverageReport(source_format="cobertura", files=files)
