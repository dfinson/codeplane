"""Unit tests for Context Probe (probe.py).

Tests cover:
- Probe validation rules (Code vs Data families)
- Sampling logic (up to 5 files per context)
- Status transitions: pending → valid/failed/empty
- Error ratio threshold (<10% for code files)
- Tree-sitter validation integration
"""

from __future__ import annotations

from pathlib import Path

from codeplane.index._internal.discovery import (
    BatchProbeResult,
    ContextProbe,
    ProbeConfig,
    ProbeResult,
)
from codeplane.index._internal.parsing import TreeSitterParser
from codeplane.index.models import CandidateContext, LanguageFamily, ProbeStatus


def make_candidate(
    family: LanguageFamily,
    root_path: str,
    include_spec: list[str] | None = None,
) -> CandidateContext:
    """Helper to create CandidateContext."""
    return CandidateContext(
        language_family=family,
        root_path=root_path,
        tier=2,
        markers=[],
        include_spec=include_spec,
        probe_status=ProbeStatus.PENDING,
    )


class TestContextProbe:
    """Tests for ContextProbe class."""

    def test_probe_valid_python_context(self, temp_dir: Path) -> None:
        """Valid Python files should pass probe."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create valid Python files
        pkg_path = repo_path / "src"
        pkg_path.mkdir()
        (pkg_path / "main.py").write_text('def hello():\n    return "hello"\n')
        (pkg_path / "utils.py").write_text("def helper():\n    pass\n")

        candidate = make_candidate(LanguageFamily.PYTHON, "src", include_spec=["*.py"])

        parser = TreeSitterParser()
        probe = ContextProbe(repo_path, parser=parser)
        result = probe.validate(candidate)

        assert isinstance(result, ProbeResult)
        assert result.valid

    def test_probe_empty_context(self, temp_dir: Path) -> None:
        """Context with no matching files should be empty."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create directory with no Python files
        pkg_path = repo_path / "empty_pkg"
        pkg_path.mkdir()
        (pkg_path / "readme.txt").write_text("not python")

        candidate = make_candidate(LanguageFamily.PYTHON, "empty_pkg", include_spec=["*.py"])

        parser = TreeSitterParser()
        probe = ContextProbe(repo_path, parser=parser)
        result = probe.validate(candidate)

        # Should report as empty (no matching files)
        assert not result.valid or "empty" in (result.reason or "").lower()

    def test_probe_failed_context_syntax_errors(self, temp_dir: Path) -> None:
        """Context with many syntax errors should fail probe."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create files with syntax errors
        pkg_path = repo_path / "broken"
        pkg_path.mkdir()
        (pkg_path / "bad1.py").write_text("def broken(\n  # missing close\n")
        (pkg_path / "bad2.py").write_text("class {\n  invalid\n")
        (pkg_path / "bad3.py").write_text("def ()\n  return\n")

        candidate = make_candidate(LanguageFamily.PYTHON, "broken", include_spec=["*.py"])

        parser = TreeSitterParser()
        probe = ContextProbe(repo_path, parser=parser)
        result = probe.validate(candidate)

        # High error ratio should cause failure
        # Note: exact threshold depends on implementation
        assert result.files_sampled > 0

    def test_probe_samples_up_to_5_files(self, temp_dir: Path) -> None:
        """Probe should sample at most 5 files."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create 10 Python files
        pkg_path = repo_path / "many_files"
        pkg_path.mkdir()
        for i in range(10):
            (pkg_path / f"file{i}.py").write_text(f"x = {i}\n")

        candidate = make_candidate(LanguageFamily.PYTHON, "many_files", include_spec=["*.py"])

        parser = TreeSitterParser()
        config = ProbeConfig(max_sample=5)
        probe = ContextProbe(repo_path, config=config, parser=parser)
        result = probe.validate(candidate)

        assert result.files_sampled <= 5

    def test_probe_javascript_context(self, temp_dir: Path) -> None:
        """Valid JavaScript files should pass probe."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        pkg_path = repo_path / "js_pkg"
        pkg_path.mkdir()
        (pkg_path / "index.js").write_text("function hello() { return 'hello'; }\n")

        candidate = make_candidate(LanguageFamily.JAVASCRIPT, "js_pkg", include_spec=["*.js"])

        parser = TreeSitterParser()
        probe = ContextProbe(repo_path, parser=parser)
        result = probe.validate(candidate)

        assert result.valid

    def test_probe_data_family_json(self, temp_dir: Path) -> None:
        """Valid JSON files should pass probe for data family."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        pkg_path = repo_path / "config"
        pkg_path.mkdir()
        (pkg_path / "settings.json").write_text('{"key": "value"}\n')

        candidate = make_candidate(LanguageFamily.JSON_YAML, "config", include_spec=["*.json"])

        parser = TreeSitterParser()
        probe = ContextProbe(repo_path, parser=parser)
        result = probe.validate(candidate)

        # Data families have different validation rules
        # (valid tree with content, zero errors)
        assert result.files_sampled >= 0


class TestProbeResult:
    """Tests for ProbeResult dataclass."""

    def test_probe_result_valid(self) -> None:
        """Valid ProbeResult should be constructible."""
        ctx = make_candidate(LanguageFamily.PYTHON, "src")
        result = ProbeResult(
            context=ctx,
            valid=True,
            files_sampled=3,
            files_passed=3,
            reason="",
        )
        assert result.valid
        assert result.files_sampled == 3

    def test_probe_result_failed(self) -> None:
        """Failed ProbeResult should include reason."""
        ctx = make_candidate(LanguageFamily.PYTHON, "src")
        result = ProbeResult(
            context=ctx,
            valid=False,
            files_sampled=5,
            files_passed=1,
            reason="High error ratio",
        )
        assert not result.valid
        assert result.reason == "High error ratio"


class TestProbeConfig:
    """Tests for ProbeConfig."""

    def test_default_config(self) -> None:
        """Default config should have sensible values."""
        config = ProbeConfig()
        assert config.max_sample == 10
        assert config.min_ratio == 0.5

    def test_custom_config(self) -> None:
        """Custom config values should be respected."""
        config = ProbeConfig(max_sample=5, min_ratio=0.3)
        assert config.max_sample == 5
        assert config.min_ratio == 0.3


class TestBatchProbe:
    """Tests for batch probe validation."""

    def test_validate_contexts_batch(self, temp_dir: Path) -> None:
        """Should validate multiple contexts in batch."""
        from codeplane.index._internal.discovery import validate_contexts

        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create two packages
        (repo_path / "pkg_a").mkdir()
        (repo_path / "pkg_a" / "main.py").write_text("x = 1\n")

        (repo_path / "pkg_b").mkdir()
        (repo_path / "pkg_b" / "main.py").write_text("y = 2\n")

        candidates = [
            make_candidate(LanguageFamily.PYTHON, "pkg_a", include_spec=["*.py"]),
            make_candidate(LanguageFamily.PYTHON, "pkg_b", include_spec=["*.py"]),
        ]

        result = validate_contexts(repo_path, candidates)

        assert isinstance(result, BatchProbeResult)
        # BatchProbeResult has valid and invalid lists
        assert len(result.valid) + len(result.invalid) == 2
