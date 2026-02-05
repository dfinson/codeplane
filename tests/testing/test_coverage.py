"""Tests for coverage emitters."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from codeplane.testing.coverage import (
    EMITTER_REGISTRY,
    CoverageArtifact,
    CoverageCapability,
    CoverageSummary,
    GoCoverageEmitter,
    JestCoverageEmitter,
    PackRuntime,
    PytestCovEmitter,
    get_emitter,
    parse_coverage_summary,
    supports_coverage,
)


class TestCoverageCapability:
    def test_pytest_cov_available_when_tool_present(self) -> None:
        emitter = PytestCovEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=True,
            coverage_tools={"pytest-cov": True},
        )
        assert emitter.capability(runtime) == CoverageCapability.AVAILABLE

    def test_pytest_cov_missing_prereq_when_tool_absent(self) -> None:
        emitter = PytestCovEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=True,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.MISSING_PREREQ

    def test_pytest_cov_unsupported_when_runner_unavailable(self) -> None:
        emitter = PytestCovEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=False,
            coverage_tools={"pytest-cov": True},
        )
        assert emitter.capability(runtime) == CoverageCapability.UNSUPPORTED

    def test_jest_always_available_when_runner_present(self) -> None:
        """Jest has built-in coverage, no extra tool needed."""
        emitter = JestCoverageEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=True,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.AVAILABLE

    def test_go_always_available_when_runner_present(self) -> None:
        """Go test has built-in coverage."""
        emitter = GoCoverageEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=True,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.AVAILABLE


class TestCommandModification:
    def test_pytest_cov_adds_coverage_flags(self) -> None:
        emitter = PytestCovEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            original_cmd = ["pytest", "tests/"]
            modified = emitter.modify_command(original_cmd, output_dir)

            assert "--cov=." in modified
            assert any("--cov-report=lcov:" in arg for arg in modified)

    def test_jest_adds_coverage_flags(self) -> None:
        emitter = JestCoverageEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            original_cmd = ["npx", "jest"]
            modified = emitter.modify_command(original_cmd, output_dir)

            assert "--coverage" in modified
            assert any("--coverageDirectory=" in arg for arg in modified)

    def test_go_adds_coverprofile(self) -> None:
        emitter = GoCoverageEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            original_cmd = ["go", "test", "./..."]
            modified = emitter.modify_command(original_cmd, output_dir)

            assert any("-coverprofile=" in arg for arg in modified)


class TestEmitterRegistry:
    def test_get_emitter_returns_instance(self) -> None:
        emitter = get_emitter("python.pytest")
        assert emitter is not None
        assert isinstance(emitter, PytestCovEmitter)

    def test_get_emitter_returns_none_for_unknown(self) -> None:
        assert get_emitter("unknown.pack") is None

    def test_supports_coverage_true_for_known_packs(self) -> None:
        assert supports_coverage("python.pytest")
        assert supports_coverage("js.jest")
        assert supports_coverage("go.gotest")

    def test_supports_coverage_false_for_unknown(self) -> None:
        assert not supports_coverage("bash.bats")
        assert not supports_coverage("unknown.pack")

    @pytest.mark.parametrize("pack_id", list(EMITTER_REGISTRY.keys()))
    def test_all_registered_emitters_have_format_id(self, pack_id: str) -> None:
        emitter = get_emitter(pack_id)
        assert emitter is not None
        assert emitter.format_id  # Non-empty string


class TestArtifactPaths:
    def test_pytest_artifact_path(self) -> None:
        emitter = PytestCovEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            path = emitter.artifact_path(output_dir)

            assert str(path).endswith("lcov.info")
            assert "coverage" in str(path)

    def test_jest_artifact_path_is_directory(self) -> None:
        emitter = JestCoverageEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            path = emitter.artifact_path(output_dir)

            # Jest produces a coverage directory
            assert str(path).endswith("coverage")

    def test_go_artifact_path(self) -> None:
        emitter = GoCoverageEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            path = emitter.artifact_path(output_dir)

            assert str(path).endswith("coverage.out")


# =============================================================================
# Additional Emitter Tests
# =============================================================================


class TestVitestEmitter:
    """Tests for Vitest coverage emitter."""

    def test_capability_available_when_runner_present(self) -> None:
        from codeplane.testing.coverage import VitestCoverageEmitter

        emitter = VitestCoverageEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=True,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.AVAILABLE

    def test_format_id(self) -> None:
        from codeplane.testing.coverage import VitestCoverageEmitter

        emitter = VitestCoverageEmitter()
        assert emitter.format_id == "istanbul"

    def test_modify_command(self) -> None:
        from codeplane.testing.coverage import VitestCoverageEmitter

        emitter = VitestCoverageEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            modified = emitter.modify_command(["npx", "vitest"], output_dir)
            assert "--coverage" in modified

    def test_artifact_path(self) -> None:
        from codeplane.testing.coverage import VitestCoverageEmitter

        emitter = VitestCoverageEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            path = emitter.artifact_path(output_dir)
            assert "coverage" in str(path)


class TestCargoLlvmCovEmitter:
    """Tests for Rust cargo-llvm-cov emitter."""

    def test_capability_unsupported_when_runner_unavailable(self) -> None:
        from codeplane.testing.coverage import CargoLlvmCovEmitter

        emitter = CargoLlvmCovEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=False,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.UNSUPPORTED

    def test_format_id(self) -> None:
        from codeplane.testing.coverage import CargoLlvmCovEmitter

        emitter = CargoLlvmCovEmitter()
        assert emitter.format_id == "lcov"

    def test_modify_command(self) -> None:
        from codeplane.testing.coverage import CargoLlvmCovEmitter

        emitter = CargoLlvmCovEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            modified = emitter.modify_command(["cargo", "test"], output_dir)
            assert "llvm-cov" in modified
            assert "--lcov" in modified

    def test_artifact_path(self) -> None:
        from codeplane.testing.coverage import CargoLlvmCovEmitter

        emitter = CargoLlvmCovEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            path = emitter.artifact_path(output_dir)
            assert str(path).endswith("lcov.info")


class TestMavenJacocoEmitter:
    """Tests for Maven JaCoCo emitter."""

    def test_capability_available_when_runner_present(self) -> None:
        from codeplane.testing.coverage import MavenJacocoEmitter

        emitter = MavenJacocoEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=True,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.AVAILABLE

    def test_format_id(self) -> None:
        from codeplane.testing.coverage import MavenJacocoEmitter

        emitter = MavenJacocoEmitter()
        assert emitter.format_id == "jacoco"

    def test_modify_command(self) -> None:
        from codeplane.testing.coverage import MavenJacocoEmitter

        emitter = MavenJacocoEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            modified = emitter.modify_command(["mvn", "test"], output_dir)
            assert "jacoco:report" in modified


class TestGradleJacocoEmitter:
    """Tests for Gradle JaCoCo emitter."""

    def test_capability_available_when_runner_present(self) -> None:
        from codeplane.testing.coverage import GradleJacocoEmitter

        emitter = GradleJacocoEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=True,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.AVAILABLE

    def test_modify_command(self) -> None:
        from codeplane.testing.coverage import GradleJacocoEmitter

        emitter = GradleJacocoEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            modified = emitter.modify_command(["./gradlew", "test"], output_dir)
            assert "jacocoTestReport" in modified


class TestDotnetCoverletEmitter:
    """Tests for .NET Coverlet emitter."""

    def test_capability_unsupported_when_runner_unavailable(self) -> None:
        from codeplane.testing.coverage import DotnetCoverletEmitter

        emitter = DotnetCoverletEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=False,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.UNSUPPORTED

    def test_format_id(self) -> None:
        from codeplane.testing.coverage import DotnetCoverletEmitter

        emitter = DotnetCoverletEmitter()
        assert emitter.format_id == "cobertura"

    def test_modify_command(self) -> None:
        from codeplane.testing.coverage import DotnetCoverletEmitter

        emitter = DotnetCoverletEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            modified = emitter.modify_command(["dotnet", "test"], output_dir)
            assert "--collect:XPlat Code Coverage" in modified or "--collect" in modified


class TestSimpleCovEmitter:
    """Tests for Ruby SimpleCov emitter."""

    def test_capability_unsupported_when_runner_unavailable(self) -> None:
        from codeplane.testing.coverage import SimpleCovEmitter

        emitter = SimpleCovEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=False,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.UNSUPPORTED

    def test_format_id(self) -> None:
        from codeplane.testing.coverage import SimpleCovEmitter

        emitter = SimpleCovEmitter()
        assert emitter.format_id == "simplecov"

    def test_modify_command(self) -> None:
        from codeplane.testing.coverage import SimpleCovEmitter

        emitter = SimpleCovEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            modified = emitter.modify_command(["bundle", "exec", "rspec"], output_dir)
            # SimpleCov doesn't modify command - it's configured in code
            assert modified is not None


class TestPHPUnitCoverageEmitter:
    """Tests for PHPUnit coverage emitter."""

    def test_capability_unsupported_when_runner_unavailable(self) -> None:
        from codeplane.testing.coverage import PHPUnitCoverageEmitter

        emitter = PHPUnitCoverageEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=False,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.UNSUPPORTED

    def test_format_id(self) -> None:
        from codeplane.testing.coverage import PHPUnitCoverageEmitter

        emitter = PHPUnitCoverageEmitter()
        assert emitter.format_id == "clover"

    def test_modify_command(self) -> None:
        from codeplane.testing.coverage import PHPUnitCoverageEmitter

        emitter = PHPUnitCoverageEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            modified = emitter.modify_command(["phpunit"], output_dir)
            assert "--coverage-clover" in " ".join(modified)


class TestDartCoverageEmitter:
    """Tests for Dart coverage emitter."""

    def test_capability_unsupported_when_runner_unavailable(self) -> None:
        from codeplane.testing.coverage import DartCoverageEmitter

        emitter = DartCoverageEmitter()
        runtime = PackRuntime(
            workspace_root=Path("/repo"),
            runner_available=False,
            coverage_tools={},
        )
        assert emitter.capability(runtime) == CoverageCapability.UNSUPPORTED

    def test_format_id(self) -> None:
        from codeplane.testing.coverage import DartCoverageEmitter

        emitter = DartCoverageEmitter()
        assert emitter.format_id == "dart"

    def test_modify_command(self) -> None:
        from codeplane.testing.coverage import DartCoverageEmitter

        emitter = DartCoverageEmitter()
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            modified = emitter.modify_command(["dart", "test"], output_dir)
            assert any("--coverage" in arg for arg in modified)


# =============================================================================
# Coverage Parsing Tests
# =============================================================================


class TestParseCoverageSummary:
    """Tests for parse_coverage_summary function."""

    def test_returns_none_for_missing_file(self) -> None:
        artifact = CoverageArtifact(
            format="lcov",
            path=Path("/nonexistent/lcov.info"),
            pack_id="python.pytest",
            invocation_id="test",
        )
        result = parse_coverage_summary(artifact)
        assert result is None

    def test_returns_none_for_unknown_format(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "coverage.xyz"
            path.write_text("some content")
            artifact = CoverageArtifact(
                format="unknown_format",
                path=path,
                pack_id="test.pack",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)
            assert result is None


class TestParseLcov:
    """Tests for LCOV format parsing."""

    def test_parses_basic_lcov(self) -> None:
        lcov_content = """SF:src/main.py
DA:1,1
DA:2,1
DA:3,0
LF:3
LH:2
end_of_record
SF:src/utils.py
DA:1,1
DA:2,1
LF:2
LH:2
end_of_record
"""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "lcov.info"
            path.write_text(lcov_content)
            artifact = CoverageArtifact(
                format="lcov",
                path=path,
                pack_id="python.pytest",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.is_valid
            assert result.lines_total == 5
            assert result.lines_covered == 4
            assert result.lines_percent == pytest.approx(80.0, rel=0.1)
            assert result.files_with_coverage == 2

    def test_parses_lcov_with_branches(self) -> None:
        lcov_content = """SF:src/main.py
LF:10
LH:8
BRF:4
BRH:3
end_of_record
"""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "lcov.info"
            path.write_text(lcov_content)
            artifact = CoverageArtifact(
                format="lcov",
                path=path,
                pack_id="python.pytest",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.branches_total == 4
            assert result.branches_covered == 3
            assert result.branches_percent == pytest.approx(75.0, rel=0.1)

    def test_parses_lcov_with_functions(self) -> None:
        lcov_content = """SF:src/main.py
LF:10
LH:10
FNF:5
FNH:4
end_of_record
"""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "lcov.info"
            path.write_text(lcov_content)
            artifact = CoverageArtifact(
                format="lcov",
                path=path,
                pack_id="python.pytest",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.functions_total == 5
            assert result.functions_covered == 4
            assert result.functions_percent == pytest.approx(80.0, rel=0.1)


class TestParseIstanbul:
    """Tests for Istanbul/NYC JSON format parsing."""

    def test_parses_coverage_summary_json(self) -> None:
        import json

        summary_content = {
            "total": {
                "lines": {"total": 100, "covered": 85, "pct": 85.0},
                "branches": {"total": 40, "covered": 32, "pct": 80.0},
                "functions": {"total": 20, "covered": 18, "pct": 90.0},
                "statements": {"total": 100, "covered": 85, "pct": 85.0},
            },
            "src/app.js": {},
            "src/utils.js": {},
        }
        with TemporaryDirectory() as tmpdir:
            cov_dir = Path(tmpdir) / "coverage"
            cov_dir.mkdir()
            summary_file = cov_dir / "coverage-summary.json"
            summary_file.write_text(json.dumps(summary_content))

            artifact = CoverageArtifact(
                format="istanbul",
                path=cov_dir,
                pack_id="js.jest",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.is_valid
            assert result.lines_total == 100
            assert result.lines_covered == 85
            assert result.lines_percent == 85.0
            assert result.branches_total == 40
            assert result.branches_covered == 32
            assert result.functions_total == 20
            assert result.functions_covered == 18


class TestParseGocov:
    """Tests for Go coverage profile parsing."""

    def test_parses_go_coverage_profile(self) -> None:
        gocov_content = """mode: set
github.com/user/pkg/main.go:10.2,12.16 3 1
github.com/user/pkg/main.go:15.2,20.16 5 0
github.com/user/pkg/utils.go:5.2,8.16 4 1
"""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "coverage.out"
            path.write_text(gocov_content)
            artifact = CoverageArtifact(
                format="gocov",
                path=path,
                pack_id="go.gotest",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.is_valid
            # Total statements: 3 + 5 + 4 = 12
            # Covered (count > 0): 3 + 4 = 7
            assert result.lines_total == 12
            assert result.lines_covered == 7
            assert result.files_with_coverage == 2

    def test_handles_empty_coverage(self) -> None:
        gocov_content = "mode: set\n"
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "coverage.out"
            path.write_text(gocov_content)
            artifact = CoverageArtifact(
                format="gocov",
                path=path,
                pack_id="go.gotest",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert not result.is_valid  # lines_total == 0


class TestParseJacoco:
    """Tests for JaCoCo XML format parsing."""

    def test_parses_jacoco_xml(self) -> None:
        jacoco_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">
<report name="example">
    <counter type="INSTRUCTION" missed="100" covered="400"/>
    <counter type="BRANCH" missed="10" covered="30"/>
    <counter type="LINE" missed="20" covered="80"/>
    <counter type="COMPLEXITY" missed="5" covered="15"/>
    <counter type="METHOD" missed="2" covered="18"/>
    <counter type="CLASS" missed="0" covered="5"/>
    <package name="com.example.app">
        <counter type="LINE" missed="10" covered="40"/>
    </package>
    <package name="com.example.util">
        <counter type="LINE" missed="10" covered="40"/>
    </package>
</report>
"""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "jacoco.xml"
            path.write_text(jacoco_content)
            artifact = CoverageArtifact(
                format="jacoco",
                path=path,
                pack_id="java.maven",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.is_valid
            # Report-level LINE counter: missed=20, covered=80 -> total=100
            assert result.lines_total == 100
            assert result.lines_covered == 80
            assert result.lines_percent == pytest.approx(80.0, rel=0.1)
            # Report-level BRANCH counter: missed=10, covered=30 -> total=40
            assert result.branches_total == 40
            assert result.branches_covered == 30
            # Report-level METHOD counter: missed=2, covered=18 -> total=20
            assert result.functions_total == 20
            assert result.functions_covered == 18
            # 2 packages
            assert result.files_with_coverage == 2

    def test_parses_jacoco_from_directory(self) -> None:
        jacoco_content = """<?xml version="1.0"?>
<report name="test">
    <counter type="LINE" missed="5" covered="15"/>
</report>
"""
        with TemporaryDirectory() as tmpdir:
            cov_dir = Path(tmpdir) / "jacoco"
            cov_dir.mkdir()
            xml_file = cov_dir / "jacoco.xml"
            xml_file.write_text(jacoco_content)

            artifact = CoverageArtifact(
                format="jacoco",
                path=cov_dir,
                pack_id="java.gradle",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.lines_total == 20
            assert result.lines_covered == 15


class TestParseCobertura:
    """Tests for Cobertura XML format parsing."""

    def test_parses_cobertura_with_line_rate(self) -> None:
        cobertura_content = """<?xml version="1.0"?>
<coverage line-rate="0.85" branch-rate="0.70" lines-covered="85" lines-valid="100">
    <packages>
        <package name="app">
            <classes>
                <class name="App" filename="app.cs">
                    <lines>
                        <line number="1" hits="1"/>
                    </lines>
                </class>
            </classes>
        </package>
    </packages>
</coverage>
"""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "coverage.cobertura.xml"
            path.write_text(cobertura_content)
            artifact = CoverageArtifact(
                format="cobertura",
                path=path,
                pack_id="dotnet.dotnettest",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.is_valid
            assert result.lines_total == 100
            assert result.lines_covered == 85
            assert result.lines_percent == pytest.approx(85.0, rel=0.1)

    def test_parses_cobertura_with_branches(self) -> None:
        cobertura_content = """<?xml version="1.0"?>
<coverage line-rate="0.90" branch-rate="0.75"
         lines-covered="90" lines-valid="100"
         branches-covered="30" branches-valid="40">
    <packages/>
</coverage>
"""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "coverage.xml"
            path.write_text(cobertura_content)
            artifact = CoverageArtifact(
                format="cobertura",
                path=path,
                pack_id="dotnet.dotnettest",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.branches_total == 40
            assert result.branches_covered == 30
            assert result.branches_percent == pytest.approx(75.0, rel=0.1)

    def test_parses_cobertura_from_directory(self) -> None:
        cobertura_content = """<?xml version="1.0"?>
<coverage line-rate="0.80" lines-covered="80" lines-valid="100">
    <packages/>
</coverage>
"""
        with TemporaryDirectory() as tmpdir:
            cov_dir = Path(tmpdir)
            xml_file = cov_dir / "coverage.cobertura.xml"
            xml_file.write_text(cobertura_content)

            artifact = CoverageArtifact(
                format="cobertura",
                path=cov_dir,
                pack_id="dotnet.dotnettest",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.lines_covered == 80

    def test_handles_dotnet_testresults_structure(self) -> None:
        """Cobertura files from .NET often land in TestResults/**/."""
        cobertura_content = """<?xml version="1.0"?>
<coverage line-rate="0.75" lines-covered="75" lines-valid="100">
    <packages/>
</coverage>
"""
        with TemporaryDirectory() as tmpdir:
            cov_dir = Path(tmpdir)
            # .NET structure: TestResults/<guid>/coverage.cobertura.xml
            test_results = cov_dir / "TestResults" / "abc123"
            test_results.mkdir(parents=True)
            xml_file = test_results / "coverage.cobertura.xml"
            xml_file.write_text(cobertura_content)

            artifact = CoverageArtifact(
                format="cobertura",
                path=cov_dir,
                pack_id="dotnet.dotnettest",
                invocation_id="test",
            )
            result = parse_coverage_summary(artifact)

            assert result is not None
            assert result.lines_covered == 75


class TestCoverageSummaryToDict:
    """Tests for CoverageSummary.to_dict() method."""

    def test_rounds_percentages_to_one_decimal(self) -> None:
        summary = CoverageSummary(
            lines_covered=847,
            lines_total=1000,
            lines_percent=84.7,
            branches_covered=333,
            branches_total=500,
            branches_percent=66.666,
            functions_covered=95,
            functions_total=100,
            functions_percent=95.0,
            files_with_coverage=10,
            files_total=10,
            format_id="lcov",
            pack_id="python.pytest",
        )
        result = summary.to_dict()

        assert result["lines"]["percent"] == 84.7
        assert result["branches"]["percent"] == 66.7  # Rounded to 1 decimal
        assert result["functions"]["percent"] == 95.0

    def test_includes_optional_fields_when_present(self) -> None:
        summary = CoverageSummary(
            lines_covered=100,
            lines_total=100,
            lines_percent=100.0,
            branches_covered=50,
            branches_total=50,
            branches_percent=100.0,
            files_with_coverage=5,
            files_total=5,
            format_id="lcov",
            pack_id="test",
        )
        result = summary.to_dict()

        assert "branches" in result
        assert "functions" not in result  # functions_total is None

    def test_excludes_optional_fields_when_none(self) -> None:
        summary = CoverageSummary(
            lines_covered=100,
            lines_total=100,
            lines_percent=100.0,
            files_with_coverage=5,
            files_total=5,
            format_id="gocov",
            pack_id="go.gotest",
        )
        result = summary.to_dict()

        assert "branches" not in result
        assert "functions" not in result
