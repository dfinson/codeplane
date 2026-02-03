"""Tests for TestOps and workspace detection."""

from pathlib import Path
from tempfile import TemporaryDirectory

from codeplane.testing.ops import detect_workspaces


class TestWorkspaceDetection:
    """Tests for monorepo workspace detection."""

    def test_given_single_project_when_detect_then_finds_workspace(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")

            workspaces = detect_workspaces(root)

            assert len(workspaces) >= 1
            assert any(ws.root == root for ws in workspaces)

    def test_given_js_monorepo_when_detect_then_finds_nested_workspaces(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Root package.json
            (root / "package.json").write_text('{"workspaces": ["packages/*"]}')

            # Nested packages
            (root / "packages").mkdir()
            (root / "packages" / "pkg-a").mkdir()
            (root / "packages" / "pkg-a" / "package.json").write_text('{"jest": {}}')
            (root / "packages" / "pkg-b").mkdir()
            (root / "packages" / "pkg-b" / "package.json").write_text('{"jest": {}}')

            workspaces = detect_workspaces(root)

            workspace_roots = [ws.root for ws in workspaces]
            # Should find root + nested packages
            assert root in workspace_roots or any("packages" in str(r) for r in workspace_roots)

    def test_given_empty_dir_when_detect_then_no_workspaces(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            workspaces = detect_workspaces(root)

            assert len(workspaces) == 0

    def test_given_multiple_runners_when_detect_then_highest_confidence_wins(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Both pytest.ini (high) and conftest.py (medium)
            (root / "pytest.ini").write_text("[pytest]")
            (root / "conftest.py").write_text("")

            workspaces = detect_workspaces(root)

            # Should have exactly one pytest workspace (deduplicated)
            pytest_workspaces = [ws for ws in workspaces if ws.pack.pack_id == "python.pytest"]
            assert len(pytest_workspaces) == 1
            assert pytest_workspaces[0].confidence == 1.0  # High confidence
