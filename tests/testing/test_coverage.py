"""Tests for coverage emitters."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from codeplane.testing.coverage import (
    EMITTER_REGISTRY,
    CoverageCapability,
    GoCoverageEmitter,
    JestCoverageEmitter,
    PackRuntime,
    PytestCovEmitter,
    get_emitter,
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
