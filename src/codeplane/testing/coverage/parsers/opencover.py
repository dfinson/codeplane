r"""OpenCover XML format parser.

OpenCover is a .NET coverage tool producing XML with detailed per-sequence-point data.
Similar to Cobertura but with different element names and structure.

Structure:
<CoverageSession>
  <Modules>
    <Module>
      <FullName>MyAssembly</FullName>
      <Files>
        <File uid="1" fullPath="C:\src\Foo.cs"/>
      </Files>
      <Classes>
        <Class>
          <FullName>MyNamespace.MyClass</FullName>
          <Methods>
            <Method>
              <Name>MyMethod</Name>
              <SequencePoints>
                <SequencePoint vc="5" sl="10" el="12" sc="0" ec="0" fileid="1"/>
              </SequencePoints>
              <BranchPoints>
                <BranchPoint vc="3" sl="11" path="0" offset="1" fileid="1"/>
              </BranchPoints>
            </Method>
          </Methods>
        </Class>
      </Classes>
    </Module>
  </Modules>
</CoverageSession>

vc = visit count, sl = start line, el = end line, sc = start column, ec = end column
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


class OpencoverParser:
    """Parser for OpenCover XML format."""

    @property
    def format_id(self) -> str:
        return "opencover"

    def can_parse(self, path: Path) -> bool:
        """Check if path contains OpenCover coverage data."""
        if path.is_dir():
            # Check for common OpenCover output names
            for name in ["coverage.opencover.xml", "opencover.xml", "coverage.xml"]:
                if (path / name).exists():
                    return True
            # Coverlet TestResults structure
            for results_dir in path.glob("TestResults/*"):
                if results_dir.is_dir() and any(results_dir.glob("coverage.opencover.xml")):
                    return True
            return False

        if not path.is_file():
            return False

        # Content sniff for OpenCover XML
        try:
            with path.open("rb") as f:
                header = f.read(2048).decode("utf-8", errors="ignore")
                if "<CoverageSession" in header or "<SequencePoint" in header:
                    return True
        except (OSError, UnicodeDecodeError):
            pass
        return False

    def _find_xml_file(self, path: Path) -> Path:
        """Find the actual XML file."""
        if path.is_file():
            return path

        for name in ["coverage.opencover.xml", "opencover.xml", "coverage.xml"]:
            candidate = path / name
            if candidate.exists():
                return candidate

        # Coverlet TestResults structure
        xml_files = list(path.glob("TestResults/*/coverage.opencover.xml"))
        if xml_files:
            return xml_files[0]

        raise CoverageParseError(f"No OpenCover XML found in {path}")

    def parse(self, path: Path, *, base_path: Path | None = None) -> CoverageReport:
        """Parse OpenCover XML into CoverageReport."""
        if not path.exists():
            raise CoverageParseError(f"OpenCover path not found: {path}")

        xml_file = self._find_xml_file(path)

        try:
            tree = ET.parse(xml_file)
        except ET.ParseError as e:
            raise CoverageParseError(f"Invalid OpenCover XML: {e}") from e

        root = tree.getroot()
        files: dict[str, FileCoverage] = {}

        # Build file ID -> path mapping
        file_map: dict[str, str] = {}
        for module in root.findall(".//Module"):
            for file_elem in module.findall(".//File"):
                uid = file_elem.get("uid", "")
                full_path = file_elem.get("fullPath", "")
                if uid and full_path:
                    # Normalize Windows paths
                    normalized = full_path.replace("\\", "/")
                    if base_path:
                        with contextlib.suppress(ValueError):
                            normalized = str(Path(normalized).relative_to(base_path))
                    file_map[uid] = normalized

        # Parse methods and their sequence/branch points
        for module in root.findall(".//Module"):
            for cls in module.findall(".//Class"):
                for method in cls.findall(".//Method"):
                    method_name = ""
                    name_elem = method.find("Name")
                    if name_elem is not None and name_elem.text:
                        method_name = name_elem.text

                    # Skip compiler-generated methods
                    if method_name.startswith("<") and ">b__" in method_name:
                        continue

                    # Process sequence points
                    for sp in method.findall(".//SequencePoint"):
                        file_id = sp.get("fileid", "")
                        if file_id not in file_map:
                            continue

                        file_path = file_map[file_id]
                        start_line = int(sp.get("sl", 0))
                        end_line = int(sp.get("el", start_line))
                        visit_count = int(sp.get("vc", 0))

                        if start_line <= 0:
                            continue

                        if file_path not in files:
                            files[file_path] = FileCoverage(path=file_path)
                        file_cov = files[file_path]

                        # Mark all lines in range
                        for line_num in range(start_line, end_line + 1):
                            file_cov.lines[line_num] = max(
                                file_cov.lines.get(line_num, 0), visit_count
                            )

                        # Record function on first sequence point
                        if method_name and start_line > 0:
                            if method_name not in file_cov.functions:
                                file_cov.functions[method_name] = FunctionCoverage(
                                    name=method_name,
                                    start_line=start_line,
                                    hits=visit_count,
                                )
                            else:
                                # Update hits to max
                                existing = file_cov.functions[method_name]
                                if visit_count > existing.hits:
                                    file_cov.functions[method_name] = FunctionCoverage(
                                        name=method_name,
                                        start_line=existing.start_line,
                                        hits=visit_count,
                                    )

                    # Process branch points
                    for bp in method.findall(".//BranchPoint"):
                        file_id = bp.get("fileid", "")
                        if file_id not in file_map:
                            continue

                        file_path = file_map[file_id]
                        line = int(bp.get("sl", 0))
                        path_id = int(bp.get("path", 0))
                        visit_count = int(bp.get("vc", 0))

                        if line <= 0:
                            continue

                        if file_path not in files:
                            files[file_path] = FileCoverage(path=file_path)
                        file_cov = files[file_path]

                        file_cov.branches.append(
                            BranchCoverage(
                                line=line,
                                block_id=0,
                                branch_id=path_id,
                                hits=visit_count,
                            )
                        )

        return CoverageReport(source_format="opencover", files=files)
