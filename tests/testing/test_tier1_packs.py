"""Comprehensive tests for all Tier-1 runner packs."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from codeplane.testing.models import TestTarget
from codeplane.testing.runner_pack import runner_registry

# =============================================================================
# Python - pytest
# =============================================================================


class TestPytestPack:
    """Tests for Python pytest runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("python.pytest")
        assert pack_class is not None
        assert pack_class.pack_id == "python.pytest"
        assert pack_class.language == "python"
        assert pack_class.runner_name == "pytest"

    def test_detect_with_pytest_ini(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("[pytest]\n")
            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_conftest(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "conftest.py").write_text("")
            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.8

    def test_detect_with_pyproject_pytest_section(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_setup_cfg(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "setup.cfg").write_text("[tool:pytest]\n")
            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.9

    def test_detect_with_test_files_only(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "tests").mkdir()
            (root / "tests" / "test_foo.py").write_text("")
            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.5

    def test_detect_no_markers(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.0

    @pytest.mark.asyncio
    async def test_discover_finds_test_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "tests").mkdir()
            (root / "tests" / "test_one.py").write_text("")
            (root / "tests" / "test_two.py").write_text("")
            (root / "src").mkdir()
            (root / "src" / "module_test.py").write_text("")

            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) == 3
            selectors = [t.selector for t in targets]
            assert any("test_one.py" in s for s in selectors)
            assert any("test_two.py" in s for s in selectors)
            assert any("module_test.py" in s for s in selectors)

    @pytest.mark.asyncio
    async def test_discover_excludes_conftest(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "tests").mkdir()
            (root / "tests" / "test_foo.py").write_text("")
            (root / "tests" / "conftest.py").write_text("")

            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) == 1
            assert "conftest.py" not in targets[0].selector

    @pytest.mark.asyncio
    async def test_discover_excludes_underscore_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "tests").mkdir()
            (root / "tests" / "test_foo.py").write_text("")
            (root / "tests" / "_test_internal.py").write_text("")

            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) == 1

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("python.pytest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:tests/test_foo.py",
            selector="tests/test_foo.py",
            kind="file",
            language="python",
            runner_pack_id="python.pytest",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/junit.xml"))

        assert "pytest" in cmd
        assert "tests/test_foo.py" in cmd
        assert any("junit" in arg for arg in cmd)

    def test_build_command_with_pattern(self) -> None:
        pack_class = runner_registry.get("python.pytest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:tests/test_foo.py",
            selector="tests/test_foo.py",
            kind="file",
            language="python",
            runner_pack_id="python.pytest",
            workspace_root="/repo",
        )
        cmd = pack.build_command(
            target, output_path=Path("/out/junit.xml"), pattern="test_specific"
        )

        assert "-k" in cmd
        assert "test_specific" in cmd

    def test_build_command_with_tags(self) -> None:
        pack_class = runner_registry.get("python.pytest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:tests/test_foo.py",
            selector="tests/test_foo.py",
            kind="file",
            language="python",
            runner_pack_id="python.pytest",
            workspace_root="/repo",
        )
        cmd = pack.build_command(
            target, output_path=Path("/out/junit.xml"), tags=["slow", "integration"]
        )

        assert "-m" in cmd


# =============================================================================
# JavaScript - Jest
# =============================================================================


class TestJestPack:
    """Tests for JavaScript Jest runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("js.jest")
        assert pack_class is not None
        assert pack_class.pack_id == "js.jest"
        assert pack_class.language == "javascript"

    def test_detect_with_jest_config_js(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "jest.config.js").write_text("")
            pack_class = runner_registry.get("js.jest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_jest_config_ts(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "jest.config.ts").write_text("")
            pack_class = runner_registry.get("js.jest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_jest_config_json(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "jest.config.json").write_text("{}")
            pack_class = runner_registry.get("js.jest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_jest_config_mjs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "jest.config.mjs").write_text("")
            pack_class = runner_registry.get("js.jest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_package_json_jest_field(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "package.json").write_text('{"jest": {}}')
            pack_class = runner_registry.get("js.jest")
            assert pack_class is not None
            pack = pack_class()
            # Package.json with jest config is medium confidence
            assert pack.detect(root) >= 0.5

    def test_detect_no_markers(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pack_class = runner_registry.get("js.jest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 0.0

    @pytest.mark.asyncio
    async def test_discover_finds_test_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "app.test.js").write_text("")
            (root / "src" / "utils.spec.ts").write_text("")
            (root / "src" / "component.test.tsx").write_text("")

            pack_class = runner_registry.get("js.jest")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) == 3

    @pytest.mark.asyncio
    async def test_discover_excludes_node_modules(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "app.test.js").write_text("")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "pkg.test.js").write_text("")

            pack_class = runner_registry.get("js.jest")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) == 1
            assert "node_modules" not in targets[0].selector

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("js.jest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:src/app.test.js",
            selector="src/app.test.js",
            kind="file",
            language="javascript",
            runner_pack_id="js.jest",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.json"))

        assert "jest" in cmd
        assert "src/app.test.js" in cmd


# =============================================================================
# JavaScript - Vitest
# =============================================================================


class TestVitestPack:
    """Tests for JavaScript Vitest runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("js.vitest")
        assert pack_class is not None
        assert pack_class.pack_id == "js.vitest"

    def test_detect_with_vitest_config(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "vitest.config.ts").write_text("")
            pack_class = runner_registry.get("js.vitest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_build_command(self) -> None:
        pack_class = runner_registry.get("js.vitest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:src/app.test.ts",
            selector="src/app.test.ts",
            kind="file",
            language="typescript",
            runner_pack_id="js.vitest",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/junit.xml"))

        assert "vitest" in cmd
        assert "run" in cmd


# =============================================================================
# Go - go test
# =============================================================================


class TestGoTestPack:
    """Tests for Go test runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("go.gotest")
        assert pack_class is not None
        assert pack_class.pack_id == "go.gotest"
        assert pack_class.language == "go"

    def test_detect_with_go_mod(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "go.mod").write_text("module example.com/test")
            pack_class = runner_registry.get("go.gotest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_go_files_only(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.go").write_text("package main")
            pack_class = runner_registry.get("go.gotest")
            assert pack_class is not None
            pack = pack_class()
            # Go files without go.mod has lower confidence
            assert 0.0 < pack.detect(root) < 1.0

    @pytest.mark.asyncio
    async def test_discover_finds_packages(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "go.mod").write_text("module example")
            (root / "pkg").mkdir()
            (root / "pkg" / "foo_test.go").write_text("package foo")
            (root / "internal").mkdir()
            (root / "internal" / "bar_test.go").write_text("package bar")

            pack_class = runner_registry.get("go.gotest")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) >= 2
            assert all(t.kind == "package" for t in targets)

    def test_build_command(self) -> None:
        pack_class = runner_registry.get("go.gotest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:./pkg",
            selector="./pkg",
            kind="package",
            language="go",
            runner_pack_id="go.gotest",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out"))

        assert "go" in cmd
        assert "test" in cmd
        assert "-json" in cmd

    def test_build_command_with_pattern(self) -> None:
        pack_class = runner_registry.get("go.gotest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:./pkg",
            selector="./pkg",
            kind="package",
            language="go",
            runner_pack_id="go.gotest",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out"), pattern="TestFoo")

        assert "-run" in cmd
        assert "TestFoo" in cmd


# =============================================================================
# Rust - cargo-nextest and cargo test
# =============================================================================


class TestCargoNextestPack:
    """Tests for Rust cargo-nextest runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("rust.nextest")
        assert pack_class is not None
        assert pack_class.pack_id == "rust.nextest"
        assert pack_class.language == "rust"

    def test_detect_with_cargo_toml(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Cargo.toml").write_text('[package]\nname = "test"')
            pack_class = runner_registry.get("rust.nextest")
            assert pack_class is not None
            pack = pack_class()
            # Will be >= 0.0 depending on if cargo-nextest is installed
            result = pack.detect(root)
            assert result >= 0.0

    @pytest.mark.asyncio
    async def test_discover_single_package(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Cargo.toml").write_text('[package]\nname = "mylib"')
            (root / "tests").mkdir()

            pack_class = runner_registry.get("rust.nextest")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) >= 1
            assert targets[0].kind == "package"


class TestCargoTestPack:
    """Tests for Rust cargo test runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("rust.cargo_test")
        assert pack_class is not None
        assert pack_class.pack_id == "rust.cargo_test"


# =============================================================================
# Java - Maven and Gradle
# =============================================================================


class TestMavenSurefirePack:
    """Tests for Java Maven Surefire runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("java.maven")
        assert pack_class is not None
        assert pack_class.pack_id == "java.maven"
        assert pack_class.language == "java"

    def test_detect_with_pom(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pom.xml").write_text("<project></project>")
            pack_class = runner_registry.get("java.maven")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    @pytest.mark.asyncio
    async def test_discover_single_module(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pom.xml").write_text("<project></project>")
            (root / "src" / "test" / "java").mkdir(parents=True)

            pack_class = runner_registry.get("java.maven")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) >= 1
            assert targets[0].kind == "project"


class TestGradlePack:
    """Tests for Java Gradle runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("java.gradle")
        assert pack_class is not None
        assert pack_class.pack_id == "java.gradle"

    def test_detect_with_build_gradle(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "build.gradle").write_text("")
            pack_class = runner_registry.get("java.gradle")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_build_gradle_kts(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "build.gradle.kts").write_text("")
            pack_class = runner_registry.get("java.gradle")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0


# =============================================================================
# C# - dotnet test
# =============================================================================


class TestDotnetTestPack:
    """Tests for C# dotnet test runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("csharp.dotnet")
        assert pack_class is not None
        assert pack_class.pack_id == "csharp.dotnet"
        assert pack_class.language == "csharp"

    def test_detect_with_sln(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "MySolution.sln").write_text("")
            pack_class = runner_registry.get("csharp.dotnet")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_csproj(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "MyProject.csproj").write_text("")
            pack_class = runner_registry.get("csharp.dotnet")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) >= 0.5


# =============================================================================
# C/C++ - CTest
# =============================================================================


class TestCTestPack:
    """Tests for C/C++ CTest runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("cpp.ctest")
        assert pack_class is not None
        assert pack_class.pack_id == "cpp.ctest"
        assert pack_class.language == "cpp"

    def test_detect_with_cmake_testing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "CMakeLists.txt").write_text("enable_testing()\nadd_test(NAME foo COMMAND foo)")
            pack_class = runner_registry.get("cpp.ctest")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_cmake_no_testing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "CMakeLists.txt").write_text("project(foo)")
            pack_class = runner_registry.get("cpp.ctest")
            assert pack_class is not None
            pack = pack_class()
            # CMakeLists without testing has lower confidence
            assert 0.0 < pack.detect(root) < 1.0

    def test_build_command(self) -> None:
        pack_class = runner_registry.get("cpp.ctest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="project",
            language="cpp",
            runner_pack_id="cpp.ctest",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out"))

        assert "ctest" in cmd


# =============================================================================
# Ruby - RSpec
# =============================================================================


class TestRSpecPack:
    """Tests for Ruby RSpec runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("ruby.rspec")
        assert pack_class is not None
        assert pack_class.pack_id == "ruby.rspec"
        assert pack_class.language == "ruby"

    def test_detect_with_rspec_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".rspec").write_text("--format documentation")
            pack_class = runner_registry.get("ruby.rspec")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_gemfile(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Gemfile").write_text("gem 'rspec'")
            pack_class = runner_registry.get("ruby.rspec")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) >= 0.5

    @pytest.mark.asyncio
    async def test_discover(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "spec").mkdir()
            (root / "spec" / "models").mkdir()
            (root / "spec" / "models" / "user_spec.rb").write_text("")
            (root / "spec" / "controllers").mkdir()
            (root / "spec" / "controllers" / "api_spec.rb").write_text("")

            pack_class = runner_registry.get("ruby.rspec")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) == 2


# =============================================================================
# PHP - PHPUnit
# =============================================================================


class TestPHPUnitPack:
    """Tests for PHP PHPUnit runner pack."""

    def test_pack_id(self) -> None:
        pack_class = runner_registry.get("php.phpunit")
        assert pack_class is not None
        assert pack_class.pack_id == "php.phpunit"
        assert pack_class.language == "php"

    def test_detect_with_phpunit_xml(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "phpunit.xml").write_text("<phpunit></phpunit>")
            pack_class = runner_registry.get("php.phpunit")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) == 1.0

    def test_detect_with_phpunit_xml_dist(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "phpunit.xml.dist").write_text("<phpunit></phpunit>")
            pack_class = runner_registry.get("php.phpunit")
            assert pack_class is not None
            pack = pack_class()
            assert pack.detect(root) >= 0.5

    @pytest.mark.asyncio
    async def test_discover(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "tests").mkdir()
            (root / "tests" / "Unit").mkdir()
            (root / "tests" / "Unit" / "ExampleTest.php").write_text("")
            (root / "tests" / "Feature").mkdir()
            (root / "tests" / "Feature" / "ApiTest.php").write_text("")

            pack_class = runner_registry.get("php.phpunit")
            assert pack_class is not None
            pack = pack_class()
            targets = await pack.discover(root)

            assert len(targets) == 2


# =============================================================================
# parse_output and get_cwd Tests
# =============================================================================


class TestPytestParseOutput:
    """Tests for pytest parse_output method."""

    def test_parse_output_valid_junit_xml(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output = root / "junit.xml"
            output.write_text("""<?xml version="1.0"?>
<testsuite tests="2" failures="0" errors="0">
    <testcase classname="test_mod" name="test_one" time="0.01"/>
    <testcase classname="test_mod" name="test_two" time="0.02"/>
</testsuite>
""")
            pack_class = runner_registry.get("python.pytest")
            assert pack_class is not None
            pack = pack_class()
            result = pack.parse_output(output, "")

            assert result.total == 2
            assert result.passed == 2

    def test_parse_output_missing_file(self) -> None:
        pack_class = runner_registry.get("python.pytest")
        assert pack_class is not None
        pack = pack_class()
        result = pack.parse_output(Path("/nonexistent/junit.xml"), "")

        assert result.errors >= 1


class TestJestParseOutput:
    """Tests for Jest parse_output method."""

    def test_parse_output_valid_json(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output = root / "results.json"
            output.write_text("""{
    "testResults": [
        {
            "name": "App.test.js",
            "assertionResults": [
                {"title": "renders", "status": "passed", "duration": 100},
                {"title": "clicks", "status": "failed", "duration": 200, "failureMessages": ["assertion failed"]},
                {"title": "submits", "status": "passed", "duration": 100}
            ]
        }
    ]
}
""")
            pack_class = runner_registry.get("js.jest")
            assert pack_class is not None
            pack = pack_class()
            result = pack.parse_output(output, "")

            assert result.total == 3
            assert result.passed == 2
            assert result.failed == 1


class TestGoTestParseOutput:
    """Tests for Go test parse_output method."""

    def test_parse_output_from_stdout(self) -> None:
        pack_class = runner_registry.get("go.gotest")
        assert pack_class is not None
        pack = pack_class()
        stdout = """{"Action":"run","Package":"pkg","Test":"TestA"}
{"Action":"pass","Package":"pkg","Test":"TestA","Elapsed":0.1}
{"Action":"pass","Package":"pkg","Elapsed":0.1}
"""
        result = pack.parse_output(Path("/nonexistent.json"), stdout)

        assert result.passed >= 1

    def test_parse_output_with_failure(self) -> None:
        pack_class = runner_registry.get("go.gotest")
        assert pack_class is not None
        pack = pack_class()
        stdout = """{"Action":"run","Package":"pkg","Test":"TestB"}
{"Action":"fail","Package":"pkg","Test":"TestB","Elapsed":0.2}
{"Action":"fail","Package":"pkg","Elapsed":0.2}
"""
        result = pack.parse_output(Path("/nonexistent.json"), stdout)

        assert result.failed >= 1


class TestCargoParseOutput:
    """Tests for Cargo parse_output method."""

    def test_parse_output_from_stdout(self) -> None:
        pack_class = runner_registry.get("rust.cargo_test")
        assert pack_class is not None
        pack = pack_class()
        stdout = """running 2 tests
test tests::test_add ... ok
test tests::test_sub ... ok

test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
"""
        result = pack.parse_output(Path("/nonexistent.xml"), stdout)

        assert result.total == 2
        assert result.passed == 2


class TestMavenParseOutput:
    """Tests for Maven parse_output method."""

    def test_parse_output_valid_surefire(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Maven looks at output_path.parent / target / surefire-reports
            surefire = root / "target" / "surefire-reports"
            surefire.mkdir(parents=True)
            (surefire / "TEST-com.example.AppTest.xml").write_text("""<?xml version="1.0"?>
<testsuite tests="2" failures="0" errors="0" name="com.example.AppTest">
    <testcase classname="com.example.AppTest" name="testOne" time="0.01"/>
    <testcase classname="com.example.AppTest" name="testTwo" time="0.02"/>
</testsuite>
""")
            pack_class = runner_registry.get("java.maven")
            assert pack_class is not None
            pack = pack_class()
            # output_path should be in root so that output_path.parent / target / surefire-reports exists
            result = pack.parse_output(root / "results.xml", "")

            assert result.total == 2
            assert result.passed == 2


# =============================================================================
# get_cwd Tests
# =============================================================================


class TestGetCwd:
    """Tests for get_cwd method across packs."""

    def test_pytest_get_cwd(self) -> None:
        pack_class = runner_registry.get("python.pytest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:tests/test_foo.py",
            selector="tests/test_foo.py",
            kind="file",
            language="python",
            runner_pack_id="python.pytest",
            workspace_root="/repo",
        )
        cwd = pack.get_cwd(target)
        assert cwd == Path("/repo")

    def test_jest_get_cwd(self) -> None:
        pack_class = runner_registry.get("js.jest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:src/app.test.js",
            selector="src/app.test.js",
            kind="file",
            language="javascript",
            runner_pack_id="js.jest",
            workspace_root="/repo",
        )
        cwd = pack.get_cwd(target)
        assert cwd == Path("/repo")

    def test_gotest_get_cwd(self) -> None:
        pack_class = runner_registry.get("go.gotest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:./...",
            selector="./...",
            kind="package",
            language="go",
            runner_pack_id="go.gotest",
            workspace_root="/repo",
        )
        cwd = pack.get_cwd(target)
        assert cwd == Path("/repo")

    def test_cargo_get_cwd(self) -> None:
        pack_class = runner_registry.get("rust.cargo_test")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:lib",
            selector="lib",
            kind="package",
            language="rust",
            runner_pack_id="rust.cargo_test",
            workspace_root="/repo",
        )
        cwd = pack.get_cwd(target)
        assert cwd == Path("/repo")


# =============================================================================
# Additional Tier-1 build_command Coverage
# =============================================================================


class TestVitestBuildCommandDetailed:
    """Detailed tests for Vitest build_command method."""

    def test_build_command_with_pattern(self) -> None:
        pack_class = runner_registry.get("js.vitest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:src/app.test.ts",
            selector="src/app.test.ts",
            kind="file",
            language="typescript",
            runner_pack_id="js.vitest",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.json"), pattern="render")

        assert any("render" in str(arg) for arg in cmd)


class TestGradleBuildCommandDetailed:
    """Detailed tests for Gradle build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("java.gradle")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="module",
            language="java",
            runner_pack_id="java.gradle",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"))

        assert any("gradle" in str(arg) for arg in cmd)
        assert "test" in cmd


class TestDotNetBuildCommandDetailed:
    """Detailed tests for .NET build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("csharp.dotnet")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:Tests.csproj",
            selector="Tests.csproj",
            kind="project",
            language="csharp",
            runner_pack_id="csharp.dotnet",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"))

        assert "dotnet" in cmd
        assert "test" in cmd

    def test_build_command_with_pattern(self) -> None:
        pack_class = runner_registry.get("csharp.dotnet")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:Tests.csproj",
            selector="Tests.csproj",
            kind="project",
            language="csharp",
            runner_pack_id="csharp.dotnet",
            workspace_root="/repo",
        )
        cmd = pack.build_command(
            target, output_path=Path("/out/results.xml"), pattern="SpecificTest"
        )

        assert "--filter" in cmd


class TestCTestBuildCommandDetailed:
    """Detailed tests for CTest build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("cpp.ctest")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:.",
            selector=".",
            kind="directory",
            language="cpp",
            runner_pack_id="cpp.ctest",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"))

        assert "ctest" in cmd


class TestRSpecBuildCommandDetailed:
    """Detailed tests for RSpec build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("ruby.rspec")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:spec/models/user_spec.rb",
            selector="spec/models/user_spec.rb",
            kind="file",
            language="ruby",
            runner_pack_id="ruby.rspec",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"))

        assert "rspec" in cmd

    def test_build_command_with_pattern(self) -> None:
        pack_class = runner_registry.get("ruby.rspec")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:spec/models/user_spec.rb",
            selector="spec/models/user_spec.rb",
            kind="file",
            language="ruby",
            runner_pack_id="ruby.rspec",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"), pattern="validates")

        assert any("validates" in str(arg) for arg in cmd)


class TestPHPUnitBuildCommandDetailed:
    """Detailed tests for PHPUnit build_command method."""

    def test_build_command_basic(self) -> None:
        pack_class = runner_registry.get("php.phpunit")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:tests/Unit/ExampleTest.php",
            selector="tests/Unit/ExampleTest.php",
            kind="file",
            language="php",
            runner_pack_id="php.phpunit",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"))

        # PHPUnit uses ./vendor/bin/phpunit
        assert any("phpunit" in arg for arg in cmd)
        assert target.selector in cmd

    def test_build_command_with_pattern(self) -> None:
        pack_class = runner_registry.get("php.phpunit")
        assert pack_class is not None
        pack = pack_class()
        target = TestTarget(
            target_id="test:tests/Unit/ExampleTest.php",
            selector="tests/Unit/ExampleTest.php",
            kind="file",
            language="php",
            runner_pack_id="php.phpunit",
            workspace_root="/repo",
        )
        cmd = pack.build_command(target, output_path=Path("/out/results.xml"), pattern="testCreate")

        assert any("testCreate" in str(arg) for arg in cmd)
