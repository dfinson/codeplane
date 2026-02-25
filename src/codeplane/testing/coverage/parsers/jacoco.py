"""JaCoCo XML format parser.

JaCoCo is the standard Java coverage tool, used via Maven and Gradle.
Supports 3-tier granularity with intelligent fallback:

1. Per-line (best): <sourcefile> with <line> elements
2. Method-level: <method> with <counter> elements (no source info)
3. Class-level: <class> with only aggregate counters (minimal)

Structure:
<report name="...">
  <package name="com.example">
    <class name="com/example/Foo">
      <method name="bar" desc="()V" line="10">
        <counter type="LINE" missed="5" covered="10"/>
        <counter type="BRANCH" missed="2" covered="4"/>
      </method>
      <counter type="LINE" missed="10" covered="50"/>
    </class>
    <sourcefile name="Foo.java">
      <line nr="1" mi="0" ci="1" mb="0" cb="0"/>
      <line nr="2" mi="1" ci="0" mb="1" cb="1"/>
    </sourcefile>
  </package>
  <counter type="LINE" missed="100" covered="400"/>
</report>
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from codeplane.testing.coverage.models import (
    BranchCoverage,
    CoverageParseError,
    CoverageReport,
    FileCoverage,
    FunctionCoverage,
)


class JacocoParser:
    """Parser for JaCoCo XML format with intelligent fallback."""

    @property
    def format_id(self) -> str:
        return "jacoco"

    def can_parse(self, path: Path) -> bool:
        """Check if path contains JaCoCo coverage data."""
        if path.is_dir():
            # Check common locations
            for name in ["jacoco.xml", "jacocoTestReport.xml"]:
                if (path / name).exists():
                    return True
            # Maven structure
            return (path / "site" / "jacoco" / "jacoco.xml").exists()

        if not path.is_file():
            return False

        # Content sniff for JaCoCo XML
        try:
            with path.open("rb") as f:
                header = f.read(2048).decode("utf-8", errors="ignore")
                # JaCoCo has <report> root with counter elements
                if "<report" in header and '<counter type="' in header:
                    return True
        except (OSError, UnicodeDecodeError):
            pass
        return False

    def _find_xml_file(self, path: Path) -> Path:
        """Find the actual XML file."""
        if path.is_file():
            return path

        for name in ["jacoco.xml", "jacocoTestReport.xml"]:
            candidate = path / name
            if candidate.exists():
                return candidate

        # Maven structure
        maven_path = path / "site" / "jacoco" / "jacoco.xml"
        if maven_path.exists():
            return maven_path

        # Gradle structure
        gradle_paths = list(path.glob("**/jacoco*.xml"))
        if gradle_paths:
            return gradle_paths[0]

        raise CoverageParseError(f"No JaCoCo XML found in {path}")

    def parse(self, path: Path, *, base_path: Path | None = None) -> CoverageReport:
        """Parse JaCoCo XML into CoverageReport."""
        if not path.exists():
            raise CoverageParseError(f"JaCoCo path not found: {path}")

        xml_file = self._find_xml_file(path)

        try:
            tree = ET.parse(xml_file)
        except ET.ParseError as e:
            raise CoverageParseError(f"Invalid JaCoCo XML: {e}") from e

        root = tree.getroot()

        # Try Tier 1: Per-line from <sourcefile> elements
        sourcefiles = root.findall(".//sourcefile")
        if sourcefiles and any(sf.findall("line") for sf in sourcefiles):
            return self._parse_sourcefiles(root, base_path)

        # Try Tier 2: Method-level from <method> elements
        methods = root.findall(".//method")
        if methods:
            return self._parse_methods(root, base_path)

        # Tier 3: Class-level counters only (minimal data)
        return self._parse_classes(root, base_path)

    def _parse_sourcefiles(
        self,
        root: ET.Element,
        base_path: Path | None,  # noqa: ARG002
    ) -> CoverageReport:
        """Tier 1: Per-line coverage from <sourcefile> elements."""
        files: dict[str, FileCoverage] = {}

        for package in root.findall(".//package"):
            _ = package.get("name", "").replace("/", ".")  # package_name unused

            for sourcefile in package.findall("sourcefile"):
                filename = sourcefile.get("name", "")
                if not filename:
                    continue

                # Build file path from package + filename
                package_path = package.get("name", "")
                file_path = f"{package_path}/{filename}" if package_path else filename

                file_cov = FileCoverage(path=file_path)

                # Extract per-line coverage
                for line in sourcefile.findall("line"):
                    nr = int(line.get("nr", 0))
                    int(line.get("mi", 0))  # missed instructions (unused but parsed)
                    ci = int(line.get("ci", 0))  # covered instructions
                    mb = int(line.get("mb", 0))  # missed branches
                    cb = int(line.get("cb", 0))  # covered branches

                    # Line is hit if any instructions covered
                    file_cov.lines[nr] = ci

                    # Branch coverage
                    total_branches = mb + cb
                    if total_branches > 0:
                        for branch_id in range(total_branches):
                            file_cov.branches.append(
                                BranchCoverage(
                                    line=nr,
                                    block_id=0,
                                    branch_id=branch_id,
                                    hits=1 if branch_id < cb else 0,
                                )
                            )

                files[file_path] = file_cov

        # Add method info from class elements
        for package in root.findall(".//package"):
            package_path = package.get("name", "")
            for cls in package.findall("class"):
                # Match class to sourcefile
                source_filename = cls.get("sourcefilename", "")
                if source_filename:
                    file_path = f"{package_path}/{source_filename}"
                    if file_path in files:
                        file_cov = files[file_path]
                        for method in cls.findall("method"):
                            name = method.get("name", "")
                            method_line = int(method.get("line", 0))
                            # Get method hits from counter
                            counter = method.find("counter[@type='METHOD']")
                            hits = 0
                            if counter is not None:
                                hits = int(counter.get("covered", 0))
                            if name:
                                file_cov.functions[name] = FunctionCoverage(
                                    name=name, start_line=method_line, hits=hits
                                )

        return CoverageReport(source_format="jacoco", files=files)

    def _parse_methods(
        self,
        root: ET.Element,
        base_path: Path | None,  # noqa: ARG002
    ) -> CoverageReport:
        """Tier 2: Method-level coverage (degraded granularity)."""
        files: dict[str, FileCoverage] = {}

        for package in root.findall(".//package"):
            package_path = package.get("name", "")

            for cls in package.findall("class"):
                source_filename = cls.get("sourcefilename", "")
                if source_filename:
                    file_path = f"{package_path}/{source_filename}"
                else:
                    # Fallback to class name
                    class_name = cls.get("name", "").split("/")[-1]
                    file_path = f"{package_path}/{class_name}.java"

                if file_path not in files:
                    files[file_path] = FileCoverage(path=file_path)
                file_cov = files[file_path]

                for method in cls.findall("method"):
                    name = method.get("name", "")
                    line = int(method.get("line", 0))

                    # Get line counter for method
                    line_counter = method.find("counter[@type='LINE']")
                    if line_counter is not None:
                        covered = int(line_counter.get("covered", 0))
                        _ = int(line_counter.get("missed", 0))  # missed unused
                        # Synthesize line coverage (method start line only)
                        if line > 0:
                            file_cov.lines[line] = covered

                    # Get method counter
                    method_counter = method.find("counter[@type='METHOD']")
                    hits = 0
                    if method_counter is not None:
                        hits = int(method_counter.get("covered", 0))

                    if name:
                        file_cov.functions[name] = FunctionCoverage(
                            name=name, start_line=line, hits=hits
                        )

        return CoverageReport(source_format="jacoco", files=files)

    def _parse_classes(
        self,
        root: ET.Element,
        base_path: Path | None,  # noqa: ARG002
    ) -> CoverageReport:
        """Tier 3: Class-level counters only (minimal)."""
        files: dict[str, FileCoverage] = {}

        for package in root.findall(".//package"):
            package_path = package.get("name", "")

            for cls in package.findall("class"):
                source_filename = cls.get("sourcefilename", "")
                if source_filename:
                    file_path = f"{package_path}/{source_filename}"
                else:
                    class_name = cls.get("name", "").split("/")[-1]
                    file_path = f"{package_path}/{class_name}.java"

                if file_path not in files:
                    files[file_path] = FileCoverage(path=file_path)
                file_cov = files[file_path]

                # Extract counters at class level
                line_counter = cls.find("counter[@type='LINE']")
                if line_counter is not None:
                    covered = int(line_counter.get("covered", 0))
                    missed = int(line_counter.get("missed", 0))
                    # Synthesize lines (we don't know real line numbers)
                    for i in range(1, covered + 1):
                        file_cov.lines[i] = 1
                    for i in range(covered + 1, covered + missed + 1):
                        file_cov.lines[i] = 0

        return CoverageReport(source_format="jacoco", files=files)
