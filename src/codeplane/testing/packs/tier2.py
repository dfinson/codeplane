"""Runner packs for Tier-2 languages.

This module registers runner packs for:
- Kotlin (Gradle with Kotlin, kotlintest)
- Swift (swift test)
- Scala (sbt test)
- Dart (dart test, flutter test)
- Bash (bats)
- PowerShell (Pester)
- Lua (busted)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from codeplane.index._internal.ignore import PRUNABLE_DIRS
from codeplane.testing.models import ParsedTestCase, ParsedTestSuite, TestTarget
from codeplane.testing.runner_pack import (
    MarkerRule,
    OutputStrategy,
    RunnerCapabilities,
    RunnerPack,
    runner_registry,
)


def _is_prunable_path(
    path: Path,
    workspace_root: Path,
    *,
    allowed_dirs: frozenset[str] | None = None,
) -> bool:
    """Check if path contains any prunable directory components.

    Args:
        path: Path to check
        workspace_root: Root directory for relative path calculation
        allowed_dirs: Optional set of directories that should be allowed
            even if they appear in PRUNABLE_DIRS
    """
    try:
        rel = path.relative_to(workspace_root)
        for part in rel.parts:
            if part in PRUNABLE_DIRS:
                if allowed_dirs and part in allowed_dirs:
                    continue
                return True
        return False
    except ValueError:
        return True


# =============================================================================
# Kotlin - Gradle with Kotlin DSL
# =============================================================================


@runner_registry.register
class KotlinGradlePack(RunnerPack):
    """Kotlin Gradle runner."""

    pack_id = "kotlin.gradle"
    language = "kotlin"
    runner_name = "gradle test"
    markers = [
        MarkerRule("build.gradle.kts", content_match="kotlin", confidence="high"),
        MarkerRule("settings.gradle.kts", confidence="medium"),
    ]
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="build/test-results/test/*.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["project"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,
    )

    def detect(self, workspace_root: Path) -> float:
        build_kts = workspace_root / "build.gradle.kts"
        if build_kts.exists():
            try:
                content = build_kts.read_text()
                if "kotlin" in content.lower():
                    return 1.0
            except Exception:
                pass
            return 0.7
        if (workspace_root / "settings.gradle.kts").exists():
            return 0.5
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        if (workspace_root / "src" / "test").exists():
            targets.append(
                TestTarget(
                    target_id="test:.",
                    selector=".",
                    kind="project",
                    language="kotlin",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        gradle = "./gradlew" if (Path(target.workspace_root) / "gradlew").exists() else "gradle"
        cmd = [gradle, "test"]
        if pattern:
            cmd.extend(["--tests", pattern])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        reports_dir = output_path.parent / "build" / "test-results" / "test"
        if not reports_dir.exists():
            return ParsedTestSuite(name="kotlin", errors=1)

        all_tests: list[ParsedTestCase] = []
        total_duration = 0.0

        for xml_file in reports_dir.glob("TEST-*.xml"):
            suite = parse_junit_xml(xml_file.read_text())
            all_tests.extend(suite.tests)
            total_duration += suite.duration_seconds

        return ParsedTestSuite(
            name="kotlin",
            tests=all_tests,
            total=len(all_tests),
            passed=sum(1 for t in all_tests if t.status == "passed"),
            failed=sum(1 for t in all_tests if t.status == "failed"),
            skipped=sum(1 for t in all_tests if t.status == "skipped"),
            errors=sum(1 for t in all_tests if t.status == "error"),
            duration_seconds=total_duration,
        )


# =============================================================================
# Swift - swift test
# =============================================================================


@runner_registry.register
class SwiftTestPack(RunnerPack):
    """Swift Package Manager test runner."""

    pack_id = "swift.swiftpm"
    language = "swift"
    runner_name = "swift test"
    markers = [
        MarkerRule("Package.swift", confidence="high"),
    ]
    output_strategy = OutputStrategy(
        format="coarse", file_based=False
    )  # swift test has limited output options
    capabilities = RunnerCapabilities(
        supported_kinds=["package"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=False,
    )

    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "Package.swift").exists():
            return 1.0
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        if (workspace_root / "Package.swift").exists():
            targets.append(
                TestTarget(
                    target_id="test:.",
                    selector=".",
                    kind="package",
                    language="swift",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,  # noqa: ARG002
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        cmd = ["swift", "test"]
        if pattern:
            cmd.extend(["--filter", pattern])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        # Parse coarse swift test output
        lines = stdout.split("\n")
        passed = 0
        failed = 0

        for line in lines:
            if "Test Suite" in line and "passed" in line:
                # e.g., "Test Suite 'All tests' passed at ..."
                # Count individual test results
                pass
            if line.strip().startswith("Test Case"):
                if "passed" in line:
                    passed += 1
                elif "failed" in line:
                    failed += 1

        return ParsedTestSuite(
            name="swift test",
            total=passed + failed,
            passed=passed,
            failed=failed,
        )


# =============================================================================
# Scala - sbt test
# =============================================================================


@runner_registry.register
class SbtTestPack(RunnerPack):
    """Scala sbt test runner."""

    pack_id = "scala.sbt"
    language = "scala"
    runner_name = "sbt test"
    markers = [
        MarkerRule("build.sbt", confidence="high"),
        MarkerRule("project/build.properties", confidence="medium"),
    ]
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="target/test-reports/*.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["project"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,  # Via sbt-junit-interface or scalatest junit reporter
    )

    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / "build.sbt").exists():
            return 1.0
        if (workspace_root / "project" / "build.properties").exists():
            return 0.8
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        if (workspace_root / "build.sbt").exists():
            targets.append(
                TestTarget(
                    target_id="test:.",
                    selector=".",
                    kind="project",
                    language="scala",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,  # noqa: ARG002
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        cmd = ["sbt"]
        if pattern:
            cmd.append(f'"testOnly *{pattern}*"')
        else:
            cmd.append("test")
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        reports_dir = output_path.parent / "target" / "test-reports"
        if not reports_dir.exists():
            return ParsedTestSuite(name="sbt", errors=1)

        all_tests: list[ParsedTestCase] = []
        total_duration = 0.0

        for xml_file in reports_dir.glob("*.xml"):
            suite = parse_junit_xml(xml_file.read_text())
            all_tests.extend(suite.tests)
            total_duration += suite.duration_seconds

        return ParsedTestSuite(
            name="sbt",
            tests=all_tests,
            total=len(all_tests),
            passed=sum(1 for t in all_tests if t.status == "passed"),
            failed=sum(1 for t in all_tests if t.status == "failed"),
            skipped=sum(1 for t in all_tests if t.status == "skipped"),
            errors=sum(1 for t in all_tests if t.status == "error"),
            duration_seconds=total_duration,
        )


# =============================================================================
# Dart - dart test / flutter test
# =============================================================================


@runner_registry.register
class DartTestPack(RunnerPack):
    """Dart test runner."""

    pack_id = "dart.dart_test"
    language = "dart"
    runner_name = "dart test"
    markers = [
        MarkerRule("pubspec.yaml", confidence="high"),
    ]
    output_strategy = OutputStrategy(format="json", file_based=False)  # dart test --reporter json
    capabilities = RunnerCapabilities(
        supported_kinds=["file", "package"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=False,  # Use json reporter
    )

    def detect(self, workspace_root: Path) -> float:
        pubspec = workspace_root / "pubspec.yaml"
        if pubspec.exists():
            try:
                content = pubspec.read_text()
                # Check if it's Flutter
                if "flutter:" in content:
                    return 0.0  # Let FlutterTestPack handle it
                return 1.0
            except Exception:
                return 0.8
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        for path in workspace_root.glob("test/**/*_test.dart"):
            if _is_prunable_path(path, workspace_root):
                continue
            rel = str(path.relative_to(workspace_root))
            targets.append(
                TestTarget(
                    target_id=f"test:{rel}",
                    selector=rel,
                    kind="file",
                    language="dart",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,
    ) -> list[str]:
        cmd = ["dart", "test", "--reporter", "json", target.selector]
        if pattern:
            cmd.extend(["--name", pattern])
        if tags:
            cmd.extend(["--tags", ",".join(tags)])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        # Parse dart test JSON output (NDJSON format)
        tests: dict[int, ParsedTestCase] = {}

        for line in stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")

            if event_type == "testStart":
                test = event.get("test", {})
                test_id = test.get("id")
                if test_id is not None:
                    tests[test_id] = ParsedTestCase(
                        name=test.get("name", "unknown"),
                        classname=None,
                        status="passed",  # Default, updated on done
                        duration_seconds=0,
                    )
            elif event_type == "testDone":
                test_id = event.get("testID")
                result = event.get("result", "")
                if test_id in tests:
                    status: Literal["passed", "failed", "skipped", "error"]
                    if result == "success":
                        status = "passed"
                    elif result == "failure":
                        status = "failed"
                    elif result == "error":
                        status = "error"
                    else:
                        status = "skipped"
                    tests[test_id].status = status
                    tests[test_id].duration_seconds = event.get("time", 0) / 1000

        test_list = list(tests.values())
        return ParsedTestSuite(
            name="dart test",
            tests=test_list,
            total=len(test_list),
            passed=sum(1 for t in test_list if t.status == "passed"),
            failed=sum(1 for t in test_list if t.status == "failed"),
            skipped=sum(1 for t in test_list if t.status == "skipped"),
            errors=sum(1 for t in test_list if t.status == "error"),
        )


@runner_registry.register
class FlutterTestPack(RunnerPack):
    """Flutter test runner."""

    pack_id = "dart.flutter_test"
    language = "dart"
    runner_name = "flutter test"
    markers = [
        MarkerRule("pubspec.yaml", content_match="flutter:", confidence="high"),
    ]
    output_strategy = OutputStrategy(format="json", file_based=False)
    capabilities = RunnerCapabilities(
        supported_kinds=["file", "package"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=False,
    )

    def detect(self, workspace_root: Path) -> float:
        pubspec = workspace_root / "pubspec.yaml"
        if pubspec.exists():
            try:
                content = pubspec.read_text()
                if "flutter:" in content:
                    return 1.0
            except Exception:
                pass
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        for path in workspace_root.glob("test/**/*_test.dart"):
            if _is_prunable_path(path, workspace_root):
                continue
            rel = str(path.relative_to(workspace_root))
            targets.append(
                TestTarget(
                    target_id=f"test:{rel}",
                    selector=rel,
                    kind="file",
                    language="dart",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,  # noqa: ARG002
        pattern: str | None = None,
        tags: list[str] | None = None,
    ) -> list[str]:
        cmd = ["flutter", "test", "--machine", target.selector]
        if pattern:
            cmd.extend(["--name", pattern])
        if tags:
            cmd.extend(["--tags", ",".join(tags)])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:
        # Flutter uses same JSON format as dart test
        dart_pack = DartTestPack()
        result = dart_pack.parse_output(output_path, stdout)
        result.name = "flutter test"
        return result


# =============================================================================
# Bash - bats (Bash Automated Testing System)
# =============================================================================


@runner_registry.register
class BatsPack(RunnerPack):
    """Bash bats test runner."""

    pack_id = "bash.bats"
    language = "bash"
    runner_name = "bats"
    markers = [
        MarkerRule("test/*.bats", confidence="high"),
        MarkerRule("tests/*.bats", confidence="high"),
        MarkerRule(".bats", confidence="medium"),
    ]
    output_strategy = OutputStrategy(format="tap", file_based=False)  # bats outputs TAP by default
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=False,
        supports_parallel=True,
        supports_junit_output=True,  # bats --formatter junit
    )

    def detect(self, workspace_root: Path) -> float:
        if list(workspace_root.glob("test/*.bats")) or list(workspace_root.glob("tests/*.bats")):
            return 1.0
        if list(workspace_root.glob("**/*.bats")):
            return 0.7
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        for path in workspace_root.glob("**/*.bats"):
            if _is_prunable_path(path, workspace_root):
                continue
            rel = str(path.relative_to(workspace_root))
            targets.append(
                TestTarget(
                    target_id=f"test:{rel}",
                    selector=rel,
                    kind="file",
                    language="bash",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,  # noqa: ARG002
    ) -> list[str]:
        cmd = ["bats", "--formatter", "junit", target.selector]
        if pattern:
            cmd.extend(["--filter", pattern])
        # Redirect to file
        return cmd + [">", str(output_path)]

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:
        from codeplane.testing.parsers import parse_junit_xml, parse_tap

        # Try JUnit first (if formatter was used)
        if output_path.exists():
            content = output_path.read_text()
            if content.strip().startswith("<"):
                return parse_junit_xml(content)

        # Fall back to TAP
        return parse_tap(stdout)


# =============================================================================
# PowerShell - Pester
# =============================================================================


@runner_registry.register
class PesterPack(RunnerPack):
    """PowerShell Pester test runner."""

    pack_id = "powershell.pester"
    language = "powershell"
    runner_name = "Pester"
    markers = [
        MarkerRule("*.Tests.ps1", confidence="high"),
        MarkerRule("tests/*.Tests.ps1", confidence="high"),
    ]
    output_strategy = OutputStrategy(
        format="junit_xml", file_based=True, file_pattern="testResults.xml"
    )
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=True,  # Via -OutputFormat JUnitXml
    )

    def detect(self, workspace_root: Path) -> float:
        if list(workspace_root.glob("**/*.Tests.ps1")):
            return 1.0
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        for path in workspace_root.glob("**/*.Tests.ps1"):
            if _is_prunable_path(path, workspace_root):
                continue
            rel = str(path.relative_to(workspace_root))
            targets.append(
                TestTarget(
                    target_id=f"test:{rel}",
                    selector=rel,
                    kind="file",
                    language="powershell",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
    ) -> list[str]:
        # Build Pester invocation
        pester_config = f"""
$config = New-PesterConfiguration
$config.Run.Path = '{target.selector}'
$config.TestResult.Enabled = $true
$config.TestResult.OutputPath = '{output_path}'
$config.TestResult.OutputFormat = 'JUnitXml'
"""
        if pattern:
            pester_config += f"$config.Filter.FullName = '*{pattern}*'\n"
        if tags:
            pester_config += f"$config.Filter.Tag = @({', '.join(repr(t) for t in tags)})\n"
        pester_config += "Invoke-Pester -Configuration $config"

        return ["pwsh", "-NoProfile", "-Command", pester_config]

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="pester", errors=1)


# =============================================================================
# Lua - busted
# =============================================================================


@runner_registry.register
class BustedPack(RunnerPack):
    """Lua busted test runner."""

    pack_id = "lua.busted"
    language = "lua"
    runner_name = "busted"
    markers = [
        MarkerRule(".busted", confidence="high"),
        MarkerRule("spec/*_spec.lua", confidence="high"),
    ]
    output_strategy = OutputStrategy(format="junit_xml", file_based=True, file_pattern="junit.xml")
    capabilities = RunnerCapabilities(
        supported_kinds=["file"],
        supports_pattern_filter=True,
        supports_tag_filter=True,
        supports_parallel=True,
        supports_junit_output=True,  # busted -o junit
    )

    def detect(self, workspace_root: Path) -> float:
        if (workspace_root / ".busted").exists():
            return 1.0
        if list(workspace_root.glob("spec/*_spec.lua")):
            return 0.9
        if list(workspace_root.glob("**/*_spec.lua")):
            return 0.7
        return 0.0

    async def discover(self, workspace_root: Path) -> list[TestTarget]:
        targets: list[TestTarget] = []
        for path in workspace_root.glob("**/*_spec.lua"):
            if _is_prunable_path(path, workspace_root):
                continue
            rel = str(path.relative_to(workspace_root))
            targets.append(
                TestTarget(
                    target_id=f"test:{rel}",
                    selector=rel,
                    kind="file",
                    language="lua",
                    runner_pack_id=self.pack_id,
                    workspace_root=str(workspace_root),
                )
            )
        return targets

    def build_command(
        self,
        target: TestTarget,
        *,
        output_path: Path,
        pattern: str | None = None,
        tags: list[str] | None = None,
    ) -> list[str]:
        cmd = ["busted", "-o", "junit", target.selector, ">", str(output_path)]
        if pattern:
            cmd.extend(["--filter", pattern])
        if tags:
            cmd.extend(["--tags", ",".join(tags)])
        return cmd

    def parse_output(self, output_path: Path, stdout: str) -> ParsedTestSuite:  # noqa: ARG002
        from codeplane.testing.parsers import parse_junit_xml

        if output_path.exists():
            return parse_junit_xml(output_path.read_text())
        return ParsedTestSuite(name="busted", errors=1)
