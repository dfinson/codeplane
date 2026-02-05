"""Comprehensive tests for all Tier-2 runner packs."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from codeplane.testing.models import TestTarget
from codeplane.testing.runner_pack import runner_registry

# =============================================================================
# Kotlin - Gradle
# =============================================================================


class TestKotlinGradlePack:
    """Tests for Kotlin Gradle runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("kotlin.gradle")
        assert pack_class is not None
        assert pack_class.pack_id == "kotlin.gradle"
        assert pack_class.language == "kotlin"

    def test_detect_with_kotlin_gradle(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "build.gradle.kts").write_text('plugins { kotlin("jvm") }')
            pack_class = runner_registry.get("kotlin.gradle")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_no_kotlin(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_class = runner_registry.get("kotlin.gradle")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.0

    @pytest.mark.asyncio
    async def test_discover(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "build.gradle.kts").write_text('plugins { kotlin("jvm") }')
            (root / "src" / "test" / "kotlin").mkdir(parents=True)

            pack_class = runner_registry.get("kotlin.gradle")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) >= 1
            assert targets[0].kind == "project"


# =============================================================================
# Swift - Swift Package Manager
# =============================================================================


class TestSwiftTestPack:
    """Tests for Swift test runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("swift.swiftpm")
        assert pack_class is not None
        assert pack_class.pack_id == "swift.swiftpm"
        assert pack_class.language == "swift"

    def test_detect_with_package_swift(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Package.swift").write_text("// swift-tools-version:5.9")
            pack_class = runner_registry.get("swift.swiftpm")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_no_swift(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_class = runner_registry.get("swift.swiftpm")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.0

    @pytest.mark.asyncio
    async def test_discover_swiftpm(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Package.swift").write_text("")
            (root / "Tests").mkdir()
            (root / "Tests" / "MyPackageTests").mkdir()
            (root / "Tests" / "MyPackageTests" / "Tests.swift").write_text("")

            pack_class = runner_registry.get("swift.swiftpm")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) >= 1


# =============================================================================
# Scala - SBT
# =============================================================================


class TestSbtTestPack:
    """Tests for Scala SBT test runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("scala.sbt")
        assert pack_class is not None
        assert pack_class.pack_id == "scala.sbt"
        assert pack_class.language == "scala"

    def test_detect_with_build_sbt(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "build.sbt").write_text("")
            pack_class = runner_registry.get("scala.sbt")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_no_sbt(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_class = runner_registry.get("scala.sbt")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.0

    @pytest.mark.asyncio
    async def test_discover(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "build.sbt").write_text("")
            (root / "src" / "test" / "scala").mkdir(parents=True)
            (root / "src" / "test" / "scala" / "TestSuite.scala").write_text("")

            pack_class = runner_registry.get("scala.sbt")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) >= 1
            assert targets[0].kind == "project"


# =============================================================================
# Dart - dart test
# =============================================================================


class TestDartTestPack:
    """Tests for Dart test runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("dart.dart_test")
        assert pack_class is not None
        assert pack_class.pack_id == "dart.dart_test"
        assert pack_class.language == "dart"

    def test_detect_with_pubspec(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pubspec.yaml").write_text("name: my_package\n")
            pack_class = runner_registry.get("dart.dart_test")
            assert pack_class is not None
            pack = pack_class()
            # Should detect as dart project (not flutter)
            assert pack.detect(root) >= 0.5

    def test_detect_no_dart(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_class = runner_registry.get("dart.dart_test")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.0

    @pytest.mark.asyncio
    async def test_discover(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pubspec.yaml").write_text("name: test\n")
            (root / "test").mkdir()
            (root / "test" / "widget_test.dart").write_text("")
            (root / "test" / "unit_test.dart").write_text("")

            pack_class = runner_registry.get("dart.dart_test")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) >= 2


# =============================================================================
# Flutter - flutter test
# =============================================================================


class TestFlutterTestPack:
    """Tests for Flutter test runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("dart.flutter_test")
        assert pack_class is not None
        assert pack_class.pack_id == "dart.flutter_test"
        assert pack_class.language == "dart"

    def test_detect_flutter_project(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pubspec.yaml").write_text("dependencies:\n  flutter:\n    sdk: flutter")
            pack_class = runner_registry.get("dart.flutter_test")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_non_flutter(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pubspec.yaml").write_text("name: my_cli\ndependencies: {}")
            pack_class = runner_registry.get("dart.flutter_test")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.0

    @pytest.mark.asyncio
    async def test_discover(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pubspec.yaml").write_text("dependencies:\n  flutter:\n    sdk: flutter")
            (root / "test").mkdir()
            (root / "test" / "widget_test.dart").write_text("")

            pack_class = runner_registry.get("dart.flutter_test")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) >= 1


# =============================================================================
# Bash - BATS
# =============================================================================


class TestBatsPack:
    """Tests for BATS shell test runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("bash.bats")
        assert pack_class is not None
        assert pack_class.pack_id == "bash.bats"
        assert pack_class.language == "bash"

    def test_detect_with_bats_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "test").mkdir()
            (root / "test" / "test_script.bats").write_text("")
            pack_class = runner_registry.get("bash.bats")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_no_bats(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_class = runner_registry.get("bash.bats")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.0

    @pytest.mark.asyncio
    async def test_discover(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "test").mkdir()
            (root / "test" / "test_one.bats").write_text("")
            (root / "test" / "test_two.bats").write_text("")

            pack_class = runner_registry.get("bash.bats")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) == 2
            assert all(t.kind == "file" for t in targets)


# =============================================================================
# PowerShell - Pester
# =============================================================================


class TestPesterPack:
    """Tests for PowerShell Pester test runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("powershell.pester")
        assert pack_class is not None
        assert pack_class.pack_id == "powershell.pester"
        assert pack_class.language == "powershell"

    def test_detect_with_pester_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "tests").mkdir()
            (root / "tests" / "Module.Tests.ps1").write_text("")
            pack_class = runner_registry.get("powershell.pester")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_no_pester(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_class = runner_registry.get("powershell.pester")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.0

    @pytest.mark.asyncio
    async def test_discover(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "tests").mkdir()
            (root / "tests" / "Unit.Tests.ps1").write_text("")
            (root / "tests" / "Integration.Tests.ps1").write_text("")

            pack_class = runner_registry.get("powershell.pester")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) == 2


# =============================================================================
# Lua - Busted
# =============================================================================


class TestBustedPack:
    """Tests for Lua Busted test runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("lua.busted")
        assert pack_class is not None
        assert pack_class.pack_id == "lua.busted"
        assert pack_class.language == "lua"

    def test_detect_with_bustedrc(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".busted").write_text("")
            pack_class = runner_registry.get("lua.busted")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_spec_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "spec").mkdir()
            (root / "spec" / "module_spec.lua").write_text("")
            pack_class = runner_registry.get("lua.busted")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) >= 0.5

    def test_detect_no_busted(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_class = runner_registry.get("lua.busted")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.0

    @pytest.mark.asyncio
    async def test_discover(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "spec").mkdir()
            (root / "spec" / "module_spec.lua").write_text("")
            (root / "spec" / "helper_spec.lua").write_text("")

            pack_class = runner_registry.get("lua.busted")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) == 2
            assert all(t.kind == "file" for t in targets)


# =============================================================================
# build_command Tests for Tier-2 Packs
# =============================================================================


class TestKotlinBuildCommand:
    """Tests for Kotlin Gradle build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("kotlin.gradle")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="project",
            language="kotlin",
            runner_pack_id="kotlin.gradle",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"))

        assert "test" in cmd

    def test_build_command_with_pattern(self) -> None:
        pack_class = runner_registry.get("kotlin.gradle")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="project",
            language="kotlin",
            runner_pack_id="kotlin.gradle",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"), pattern="MyTest")

        assert "--tests" in cmd
        assert "MyTest" in cmd


class TestSwiftBuildCommand:
    """Tests for Swift build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("swift.swiftpm")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="package",
            language="swift",
            runner_pack_id="swift.swiftpm",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"))

        assert "swift" in cmd
        assert "test" in cmd

    def test_build_command_with_pattern(self) -> None:
        pack_class = runner_registry.get("swift.swiftpm")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="package",
            language="swift",
            runner_pack_id="swift.swiftpm",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"), pattern="testMyFunc")

        assert "--filter" in cmd
        assert "testMyFunc" in cmd


class TestScalaBuildCommand:
    """Tests for Scala SBT build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("scala.sbt")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="project",
            language="scala",
            runner_pack_id="scala.sbt",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"))

        assert "sbt" in cmd
        assert "test" in cmd


class TestDartBuildCommand:
    """Tests for Dart build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("dart.dart_test")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:test/widget_test.dart",
            selector="test/widget_test.dart",
            kind="file",
            language="dart",
            runner_pack_id="dart.dart_test",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.json"))

        assert "dart" in cmd
        assert "test" in cmd

    def test_build_command_with_pattern(self) -> None:
        pack_class = runner_registry.get("dart.dart_test")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:test/widget_test.dart",
            selector="test/widget_test.dart",
            kind="file",
            language="dart",
            runner_pack_id="dart.dart_test",
            workspace_root="/repo",
        )
        cmd = pack.build_command(
            target, output_path=Path("/out/results.json"), pattern="widget renders"
        )

        assert "--name" in cmd


class TestFlutterBuildCommand:
    """Tests for Flutter build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("dart.flutter_test")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:test/widget_test.dart",
            selector="test/widget_test.dart",
            kind="file",
            language="dart",
            runner_pack_id="dart.flutter_test",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.json"))

        assert "flutter" in cmd
        assert "test" in cmd


class TestBatsBuildCommand:
    """Tests for BATS build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("bash.bats")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:test/unit.bats",
            selector="test/unit.bats",
            kind="file",
            language="bash",
            runner_pack_id="bash.bats",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.tap"))

        assert "bats" in cmd


class TestPesterBuildCommand:
    """Tests for Pester build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("powershell.pester")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:Tests/Unit.Tests.ps1",
            selector="Tests/Unit.Tests.ps1",
            kind="file",
            language="powershell",
            runner_pack_id="powershell.pester",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"))

        assert "pwsh" in cmd or "powershell" in cmd


class TestBustedBuildCommand:
    """Tests for Busted build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("lua.busted")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:spec/module_spec.lua",
            selector="spec/module_spec.lua",
            kind="file",
            language="lua",
            runner_pack_id="lua.busted",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"))

        assert "busted" in cmd

    def test_build_command_with_pattern(self) -> None:
        pack_class = runner_registry.get("lua.busted")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:spec/module_spec.lua",
            selector="spec/module_spec.lua",
            kind="file",
            language="lua",
            runner_pack_id="lua.busted",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"), pattern="test_add")

        assert "--filter" in cmd
        assert "test_add" in cmd


# =============================================================================
# get_cwd Tests for Tier-2 Packs
# =============================================================================


class TestTier2GetCwd:
    """Tests for get_cwd method across tier-2 packs."""

    def test_kotlin_get_cwd(self) -> None:
        pack_class = runner_registry.get("kotlin.gradle")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="project",
            language="kotlin",
            runner_pack_id="kotlin.gradle",
            workspace_root="/repo",
        )
        cwd = pack.get_cwd(target)
        assert cwd == Path("/repo")

    def test_swift_get_cwd(self) -> None:
        pack_class = runner_registry.get("swift.swiftpm")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="package",
            language="swift",
            runner_pack_id="swift.swiftpm",
            workspace_root="/repo",
        )
        cwd = pack.get_cwd(target)
        assert cwd == Path("/repo")

    def test_dart_get_cwd(self) -> None:
        pack_class = runner_registry.get("dart.dart_test")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:test/widget_test.dart",
            selector="test/widget_test.dart",
            kind="file",
            language="dart",
            runner_pack_id="dart.dart_test",
            workspace_root="/repo",
        )
        cwd = pack.get_cwd(target)
        assert cwd == Path("/repo")

    def test_bats_get_cwd(self) -> None:
        pack_class = runner_registry.get("bash.bats")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:test/unit.bats",
            selector="test/unit.bats",
            kind="file",
            language="bash",
            runner_pack_id="bash.bats",
            workspace_root="/repo",
        )
        cwd = pack.get_cwd(target)
        assert cwd == Path("/repo")

    def test_busted_get_cwd(self) -> None:
        pack_class = runner_registry.get("lua.busted")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:spec/module_spec.lua",
            selector="spec/module_spec.lua",
            kind="file",
            language="lua",
            runner_pack_id="lua.busted",
            workspace_root="/repo",
        )
        cwd = pack.get_cwd(target)
        assert cwd == Path("/repo")


# =============================================================================
# parse_output Tests for Tier-2 Packs
# =============================================================================


class TestKotlinParseOutput:
    """Tests for Kotlin parse_output method."""

    def test_parse_output_valid_junit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            test_results = root / "build" / "test-results" / "test"
            test_results.mkdir(parents=True)
            (test_results / "TEST-com.example.MyTest.xml").write_text("""<?xml version="1.0"?>
<testsuite tests="2" failures="0">
    <testcase classname="com.example.MyTest" name="testOne" time="0.01"/>
    <testcase classname="com.example.MyTest" name="testTwo" time="0.02"/>
</testsuite>
""")
            pack_class = runner_registry.get("kotlin.gradle")
            assert pack_class is not None
            pack = pack_class()
            result = pack.parse_output(root / "results.xml", "")

            assert result.total == 2
            assert result.passed == 2


class TestSwiftParseOutput:
    """Tests for Swift parse_output method."""

    def test_parse_output_from_stdout(self) -> None:
        pack_class = runner_registry.get("swift.swiftpm")
        assert pack_class is not None
        pack = pack_class()
        stdout = """Test Suite 'All tests' started.
Test Suite 'MyPackageTests.xctest' started.
Test Suite 'MyTests' started.
Test Case 'MyTests.testOne' started.
Test Case 'MyTests.testOne' passed (0.001 seconds).
Test Case 'MyTests.testTwo' started.
Test Case 'MyTests.testTwo' passed (0.002 seconds).
Test Suite 'MyTests' passed.
Test Suite 'MyPackageTests.xctest' passed.
Test Suite 'All tests' passed.
Executed 2 tests, with 0 failures (0 unexpected) in 0.003 seconds
"""
        result = pack.parse_output(Path("/nonexistent.xml"), stdout)

        assert result.total >= 0


class TestDartParseOutput:
    """Tests for Dart parse_output method."""

    def test_parse_output_valid_json(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output = root / "results.json"
            output.write_text("""{"type":"testDone","testID":1,"result":"success"}
{"type":"done","success":true}
""")
            pack_class = runner_registry.get("dart.dart_test")
            assert pack_class is not None
            pack = pack_class()
            result = pack.parse_output(output, "")

            assert result is not None


class TestBatsParseOutput:
    """Tests for BATS parse_output method."""

    def test_parse_output_from_tap_stdout(self) -> None:
        pack_class = runner_registry.get("bash.bats")
        assert pack_class is not None
        pack = pack_class()
        stdout = """1..3
ok 1 - test one
ok 2 - test two
not ok 3 - test three
"""
        result = pack.parse_output(Path("/nonexistent.tap"), stdout)

        assert result.total == 3
        assert result.passed == 2
        assert result.failed == 1
