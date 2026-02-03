"""Comprehensive tests for TestOps operations."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from codeplane.testing.ops import ActiveRun, DetectedWorkspace, detect_workspaces

# =============================================================================
# detect_workspaces()
# =============================================================================


class TestDetectWorkspaces:
    """Tests for workspace detection."""

    def test_single_workspace(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("[pytest]\n")

            workspaces = detect_workspaces(root)

            assert len(workspaces) >= 1
            ws = workspaces[0]
            assert isinstance(ws, DetectedWorkspace)
            assert ws.root == root
            assert ws.pack.pack_id == "python.pytest"

    def test_detects_js_packages_monorepo(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # JS packages monorepo
            (root / "packages" / "app").mkdir(parents=True)
            (root / "packages" / "app" / "package.json").write_text(
                '{"devDependencies": {"jest": "1.0"}}'
            )
            (root / "packages" / "app" / "jest.config.js").write_text("")

            workspaces = detect_workspaces(root)

            # Should detect the package
            assert len(workspaces) >= 1
            pack_ids = {ws.pack.pack_id for ws in workspaces}
            assert "js.jest" in pack_ids

    def test_ignores_node_modules(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "package.json").write_text('{"devDependencies": {"jest": "1.0"}}')
            (root / "jest.config.js").write_text("")
            (root / "node_modules" / "lib").mkdir(parents=True)
            (root / "node_modules" / "lib" / "jest.config.js").write_text("")

            workspaces = detect_workspaces(root)

            # Should only find root, not node_modules
            paths = [ws.root for ws in workspaces]
            assert all("node_modules" not in str(p) for p in paths)

    def test_returns_detected_workspace_objects(self) -> None:
        """Workspaces should be DetectedWorkspace objects."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")

            workspaces = detect_workspaces(root)

            assert len(workspaces) >= 1
            for ws in workspaces:
                assert isinstance(ws, DetectedWorkspace)
                assert hasattr(ws, "root")
                assert hasattr(ws, "pack")
                assert hasattr(ws, "confidence")

    def test_empty_directory(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspaces = detect_workspaces(root)
            assert workspaces == []

    def test_deduplicates_by_root_and_pack(self) -> None:
        """Same root/pack should not appear twice."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Both markers for pytest
            (root / "pytest.ini").write_text("")
            (root / "conftest.py").write_text("")

            workspaces = detect_workspaces(root)

            # Should deduplicate
            keys = [(ws.root, ws.pack.pack_id) for ws in workspaces]
            assert len(keys) == len(set(keys))


# =============================================================================
# DetectedWorkspace
# =============================================================================


class TestDetectedWorkspace:
    """Tests for DetectedWorkspace dataclass."""

    def test_create(self) -> None:
        from codeplane.testing.runner_pack import runner_registry

        pack = runner_registry.get("python.pytest")
        assert pack is not None

        ws = DetectedWorkspace(
            root=Path("/repo"),
            pack=pack,
            confidence=0.95,
        )

        assert ws.root == Path("/repo")
        assert ws.pack.pack_id == "python.pytest"
        assert ws.confidence == 0.95


# =============================================================================
# ActiveRun
# =============================================================================


class TestActiveRun:
    """Tests for ActiveRun dataclass."""

    def test_create(self) -> None:
        from codeplane.testing.models import TargetProgress, TestCaseProgress, TestProgress

        # Create a mock task
        async def dummy_coro():
            return None

        task = asyncio.get_event_loop().create_task(dummy_coro())
        cancel_event = asyncio.Event()

        run = ActiveRun(
            run_id="run-123",
            task=task,
            start_time=1234567890.0,
            progress=TestProgress(
                targets=TargetProgress(),
                cases=TestCaseProgress(),
            ),
            failures=[],
            cancel_event=cancel_event,
            artifact_dir=Path("/artifacts"),
        )

        assert run.run_id == "run-123"
        assert run.start_time == 1234567890.0
        assert run.artifact_dir == Path("/artifacts")

        # Cleanup
        task.cancel()


# =============================================================================
# Integration-style tests for detect_workspaces
# =============================================================================


class TestDetectWorkspacesIntegration:
    """Integration-style tests for workspace detection."""

    def test_detect_multiple_languages(self) -> None:
        """Detect workspaces across multiple languages."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Python project at root
            (root / "pytest.ini").write_text("")

            # Go project at root (same directory, different pack)
            (root / "go.mod").write_text("module test")

            workspaces = detect_workspaces(root)

            pack_ids = {ws.pack.pack_id for ws in workspaces}
            assert "python.pytest" in pack_ids
            assert "go.gotest" in pack_ids

    def test_detect_pnpm_workspaces(self) -> None:
        """Detect pnpm workspace packages."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n")
            (root / "apps" / "web").mkdir(parents=True)
            (root / "apps" / "web" / "package.json").write_text(
                '{"devDependencies": {"vitest": "1.0"}}'
            )
            (root / "apps" / "web" / "vitest.config.ts").write_text("")

            workspaces = detect_workspaces(root)

            # Should detect vitest in apps/web
            pack_ids = {ws.pack.pack_id for ws in workspaces}
            assert "js.vitest" in pack_ids

    def test_confidence_preserved(self) -> None:
        """Confidence scores should be preserved."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # High confidence marker
            (root / "pytest.ini").write_text("")

            workspaces = detect_workspaces(root)

            pytest_ws = next((ws for ws in workspaces if ws.pack.pack_id == "python.pytest"), None)
            assert pytest_ws is not None
            assert pytest_ws.confidence == 1.0  # pytest.ini gives 1.0
