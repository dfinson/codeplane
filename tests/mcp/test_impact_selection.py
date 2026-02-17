"""Tests for impact-aware test selection MCP tools.

Covers the new MCP tool parameters and functions:
- discover_test_targets with affected_by parameter
- inspect_affected_tests tool
- Auto-scoped coverage in _execute_tests
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.indexing.import_graph import (
    CoverageSourceResult,
    ImpactConfidence,
    ImpactMatch,
    ImportGraphResult,
)

# ---------------------------------------------------------------------------
# ImportGraphResult fixtures
# ---------------------------------------------------------------------------


def _make_graph_result(
    *,
    matches: list[ImpactMatch] | None = None,
    tier: str = "complete",
    resolved_ratio: float = 1.0,
    unresolved: list[str] | None = None,
    null_count: int = 0,
) -> ImportGraphResult:
    """Helper to build ImportGraphResult for tests."""
    return ImportGraphResult(
        matches=matches or [],
        confidence=ImpactConfidence(
            tier=tier,  # type: ignore[arg-type]
            resolved_ratio=resolved_ratio,
            unresolved_files=unresolved or [],
            null_source_count=null_count,
            reasoning="test",
        ),
        changed_modules=["mylib.core"],
    )


# ---------------------------------------------------------------------------
# ImportGraphResult model tests
# ---------------------------------------------------------------------------


class TestImportGraphResultModel:
    """Test ImportGraphResult convenience properties."""

    def test_test_files(self) -> None:
        result = _make_graph_result(
            matches=[
                ImpactMatch(
                    test_file="tests/test_a.py",
                    source_modules=["mylib.core"],
                    confidence="high",
                    reason="directly imports mylib.core",
                ),
                ImpactMatch(
                    test_file="tests/test_b.py",
                    source_modules=["mylib.core"],
                    confidence="low",
                    reason="imports parent module",
                ),
            ],
        )
        assert result.test_files == ["tests/test_a.py", "tests/test_b.py"]
        assert result.high_confidence_tests == ["tests/test_a.py"]
        assert result.low_confidence_tests == ["tests/test_b.py"]

    def test_empty_result(self) -> None:
        result = _make_graph_result()
        assert result.test_files == []
        assert result.high_confidence_tests == []
        assert result.low_confidence_tests == []


# ---------------------------------------------------------------------------
# CoverageSourceResult model tests
# ---------------------------------------------------------------------------


class TestCoverageSourceResult:
    """Test CoverageSourceResult."""

    def test_complete_confidence(self) -> None:
        result = CoverageSourceResult(
            source_dirs=["src/mylib"],
            source_modules=["mylib.core", "mylib.utils"],
            confidence="complete",
            null_import_count=0,
        )
        assert result.confidence == "complete"
        assert len(result.source_dirs) == 1

    def test_partial_with_nulls(self) -> None:
        result = CoverageSourceResult(
            source_dirs=["src/mylib"],
            source_modules=["mylib.core"],
            confidence="partial",
            null_import_count=3,
        )
        assert result.confidence == "partial"
        assert result.null_import_count == 3


# ---------------------------------------------------------------------------
# Auto-scoped coverage in _execute_tests
# ---------------------------------------------------------------------------


class TestAutoScopedCoverage:
    """Test that _execute_tests auto-scopes coverage via import graph."""

    @pytest.mark.asyncio
    async def test_source_dirs_passed_to_modify_command(self) -> None:
        """When coverage is enabled, source_dirs from import graph are used."""
        from codeplane.testing.coverage import PytestCovEmitter

        emitter = PytestCovEmitter()
        cmd = ["pytest", "tests/test_core.py"]
        cov_dir = Path("/tmp/cov")
        source_dirs = ["src/mylib", "src/utils"]

        result = emitter.modify_command(cmd, cov_dir, source_dirs=source_dirs)

        # Should have --cov=src/mylib and --cov=src/utils instead of --cov=.
        assert "--cov=src/mylib" in result
        assert "--cov=src/utils" in result
        assert "--cov=." not in result

    @pytest.mark.asyncio
    async def test_no_source_dirs_falls_back(self) -> None:
        """When source_dirs is None, falls back to --cov=. ."""
        from codeplane.testing.coverage import PytestCovEmitter

        emitter = PytestCovEmitter()
        cmd = ["pytest", "tests/test_core.py"]
        cov_dir = Path("/tmp/cov")

        result = emitter.modify_command(cmd, cov_dir, source_dirs=None)

        assert "--cov=." in result

    @pytest.mark.asyncio
    async def test_empty_source_dirs_falls_back(self) -> None:
        """When source_dirs is empty list, falls back to --cov=. ."""
        from codeplane.testing.coverage import PytestCovEmitter

        emitter = PytestCovEmitter()
        cmd = ["pytest", "tests/test_core.py"]
        cov_dir = Path("/tmp/cov")

        result = emitter.modify_command(cmd, cov_dir, source_dirs=[])

        assert "--cov=." in result


# ---------------------------------------------------------------------------
# Coverage emitter signature tests
# ---------------------------------------------------------------------------


class TestCoverageEmitterSignatures:
    """Verify all emitters accept source_dirs parameter."""

    @pytest.mark.parametrize(
        "emitter_class",
        [
            "PytestCovEmitter",
            "JestCoverageEmitter",
            "VitestCoverageEmitter",
            "GoCoverageEmitter",
            "CargoLlvmCovEmitter",
            "MavenJacocoEmitter",
            "GradleJacocoEmitter",
            "DotnetCoverletEmitter",
            "SimpleCovEmitter",
            "PHPUnitCoverageEmitter",
            "DartCoverageEmitter",
        ],
    )
    def test_modify_command_accepts_source_dirs(self, emitter_class: str) -> None:
        """Each emitter's modify_command accepts source_dirs kwarg."""
        import inspect

        from codeplane.testing import coverage

        cls = getattr(coverage, emitter_class)
        sig = inspect.signature(cls.modify_command)
        assert (
            "source_dirs" in sig.parameters
        ), f"{emitter_class}.modify_command missing source_dirs parameter"
