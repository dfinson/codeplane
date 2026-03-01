"""Tests for checkpoint test debt detection.

Covers:
- _detect_test_debt detects source changes without test updates
- _detect_test_debt returns None when tests are included
- _detect_test_debt returns None for test-only changes
- _detect_test_debt respects filesystem existence
"""

from __future__ import annotations

from pathlib import Path

from codeplane.mcp.tools.checkpoint import _detect_test_debt


class TestDetectTestDebt:
    """Tests for _detect_test_debt helper."""

    def test_no_debt_when_tests_included(self, tmp_path: Path) -> None:
        """No debt when test counterpart is in changed_files."""
        # Create source and test files on disk
        src = tmp_path / "src" / "codeplane" / "foo"
        src.mkdir(parents=True)
        (src / "bar.py").write_text("# source")
        tests = tmp_path / "tests" / "foo"
        tests.mkdir(parents=True)
        (tests / "test_bar.py").write_text("# test")

        result = _detect_test_debt(
            changed_files=[
                "src/codeplane/foo/bar.py",
                "tests/foo/test_bar.py",
            ],
            repo_root=tmp_path,
        )
        assert result is None

    def test_debt_when_test_missing_from_changed(self, tmp_path: Path) -> None:
        """Debt when source changed but existing test not in changed_files."""
        src = tmp_path / "src" / "codeplane" / "foo"
        src.mkdir(parents=True)
        (src / "bar.py").write_text("# source")
        tests = tmp_path / "tests" / "foo"
        tests.mkdir(parents=True)
        (tests / "test_bar.py").write_text("# test")

        result = _detect_test_debt(
            changed_files=["src/codeplane/foo/bar.py"],
            repo_root=tmp_path,
        )
        assert result is not None
        assert result["source_files_changed"] == 1
        assert result["test_files_changed"] == 0
        assert len(result["missing_test_updates"]) == 1
        assert result["missing_test_updates"][0]["source"] == "src/codeplane/foo/bar.py"
        assert "test_bar.py" in result["missing_test_updates"][0]["test_file"]

    def test_no_debt_when_test_not_on_disk(self, tmp_path: Path) -> None:
        """No debt if test counterpart doesn't exist on disk."""
        src = tmp_path / "src" / "codeplane" / "foo"
        src.mkdir(parents=True)
        (src / "bar.py").write_text("# source")
        # No test file created on disk

        result = _detect_test_debt(
            changed_files=["src/codeplane/foo/bar.py"],
            repo_root=tmp_path,
        )
        assert result is None

    def test_no_debt_for_test_only_changes(self, tmp_path: Path) -> None:
        """No debt when only test files are changed."""
        tests = tmp_path / "tests" / "foo"
        tests.mkdir(parents=True)
        (tests / "test_bar.py").write_text("# test")

        result = _detect_test_debt(
            changed_files=["tests/foo/test_bar.py"],
            repo_root=tmp_path,
        )
        assert result is None

    def test_debt_hint_text(self, tmp_path: Path) -> None:
        """Hint text mentions TEST DEBT and file names."""
        src = tmp_path / "src" / "codeplane" / "foo"
        src.mkdir(parents=True)
        (src / "bar.py").write_text("# source")
        tests = tmp_path / "tests" / "foo"
        tests.mkdir(parents=True)
        (tests / "test_bar.py").write_text("# test")

        result = _detect_test_debt(
            changed_files=["src/codeplane/foo/bar.py"],
            repo_root=tmp_path,
        )
        assert result is not None
        assert "TEST DEBT" in result["hint"]
        assert "bar.py" in result["hint"]

    def test_multiple_sources_partial_debt(self, tmp_path: Path) -> None:
        """Only sources with existing but unchanged tests generate debt."""
        src = tmp_path / "src" / "codeplane" / "foo"
        src.mkdir(parents=True)
        (src / "bar.py").write_text("# source")
        (src / "baz.py").write_text("# source 2")
        tests = tmp_path / "tests" / "foo"
        tests.mkdir(parents=True)
        (tests / "test_bar.py").write_text("# test for bar")
        # No test_baz.py on disk

        result = _detect_test_debt(
            changed_files=[
                "src/codeplane/foo/bar.py",
                "src/codeplane/foo/baz.py",
            ],
            repo_root=tmp_path,
        )
        assert result is not None
        # Only bar.py should show debt (baz.py test doesn't exist)
        assert len(result["missing_test_updates"]) == 1
        assert result["missing_test_updates"][0]["source"] == "src/codeplane/foo/bar.py"

    def test_empty_changed_files(self, tmp_path: Path) -> None:
        """Empty changed_files returns None."""
        result = _detect_test_debt([], tmp_path)
        assert result is None

    def test_go_test_convention(self, tmp_path: Path) -> None:
        """Go test convention: handler.go -> handler_test.go."""
        pkg = tmp_path / "pkg" / "server"
        pkg.mkdir(parents=True)
        (pkg / "handler.go").write_text("// source")
        (pkg / "handler_test.go").write_text("// test")

        result = _detect_test_debt(
            changed_files=["pkg/server/handler.go"],
            repo_root=tmp_path,
        )
        assert result is not None
        assert result["missing_test_updates"][0]["test_file"] == "pkg/server/handler_test.go"

    def test_go_no_debt_when_test_included(self, tmp_path: Path) -> None:
        """Go: no debt when test file is in changed_files."""
        pkg = tmp_path / "pkg" / "server"
        pkg.mkdir(parents=True)
        (pkg / "handler.go").write_text("// source")
        (pkg / "handler_test.go").write_text("// test")

        result = _detect_test_debt(
            changed_files=["pkg/server/handler.go", "pkg/server/handler_test.go"],
            repo_root=tmp_path,
        )
        assert result is None
