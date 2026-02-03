"""Comprehensive tests for TestOps operations."""

import asyncio
import contextlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeplane.testing.models import (
    TargetProgress,
    TestCaseProgress,
    TestProgress,
)
from codeplane.testing.ops import (
    ActiveRun,
    DetectedWorkspace,
    TestOps,
    _is_prunable_path,
    detect_workspaces,
)
from codeplane.testing.runner_pack import runner_registry


def create_mock_coordinator() -> MagicMock:
    """Create a mock IndexCoordinator for testing."""
    coordinator = MagicMock()
    coordinator.get_file_stats = AsyncMock(return_value={"python": 10})
    coordinator.get_indexed_file_count = AsyncMock(return_value=10)
    coordinator.get_indexed_files = AsyncMock(return_value=["src/foo.py", "src/bar.py"])
    coordinator.get_contexts = AsyncMock(return_value=[])
    return coordinator


# =============================================================================
# _is_prunable_path()
# =============================================================================


class TestIsPrunablePath:
    """Tests for the prunable path checker."""

    def test_node_modules_is_prunable(self) -> None:
        assert _is_prunable_path(Path("src/node_modules/lib")) is True

    def test_venv_is_prunable(self) -> None:
        assert _is_prunable_path(Path(".venv/lib")) is True
        assert _is_prunable_path(Path("venv/lib")) is True

    def test_packages_at_root_not_prunable(self) -> None:
        # packages at root level is a common JS monorepo pattern
        assert _is_prunable_path(Path("packages/app")) is False

    def test_nested_packages_prunable(self) -> None:
        # packages nested in prunable dir is prunable
        assert _is_prunable_path(Path("node_modules/packages")) is True

    def test_normal_path_not_prunable(self) -> None:
        assert _is_prunable_path(Path("src/app")) is False
        assert _is_prunable_path(Path("tests/unit")) is False


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

    def test_npm_workspaces_array_format(self) -> None:
        """Test npm workspaces with array format."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
            (root / "packages" / "app").mkdir(parents=True)
            (root / "packages" / "app" / "package.json").write_text(
                '{"devDependencies": {"jest": "1.0"}}'
            )
            (root / "packages" / "app" / "jest.config.js").write_text("")

            workspaces = detect_workspaces(root)

            pack_ids = {ws.pack.pack_id for ws in workspaces}
            assert "js.jest" in pack_ids

    def test_npm_workspaces_object_format(self) -> None:
        """Test npm workspaces with object format."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "package.json").write_text(
                json.dumps({"workspaces": {"packages": ["packages/*"]}})
            )
            (root / "packages" / "lib").mkdir(parents=True)
            (root / "packages" / "lib" / "package.json").write_text(
                '{"devDependencies": {"vitest": "1.0"}}'
            )
            (root / "packages" / "lib" / "vitest.config.ts").write_text("")

            workspaces = detect_workspaces(root)

            pack_ids = {ws.pack.pack_id for ws in workspaces}
            assert "js.vitest" in pack_ids


# =============================================================================
# DetectedWorkspace
# =============================================================================


class TestDetectedWorkspace:
    """Tests for DetectedWorkspace dataclass."""

    def test_create(self) -> None:
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

    @pytest.mark.asyncio
    async def test_create(self) -> None:
        # Create a mock task
        async def dummy_coro():
            return None

        task = asyncio.create_task(dummy_coro())
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
        with contextlib.suppress(asyncio.CancelledError):
            await task


# =============================================================================
# TestOps.discover()
# =============================================================================


class TestTestOpsDiscover:
    """Tests for TestOps.discover()."""

    @pytest.mark.asyncio
    async def test_discover_returns_test_result(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")
            (root / "tests").mkdir()
            (root / "tests" / "test_example.py").write_text("def test_foo(): pass")

            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            result = await ops.discover()

            assert result.action == "discover"
            assert result.targets is not None

    @pytest.mark.asyncio
    async def test_discover_with_paths_filter(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")
            (root / "tests").mkdir()
            (root / "tests" / "test_a.py").write_text("def test_a(): pass")
            (root / "tests" / "test_b.py").write_text("def test_b(): pass")

            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            result = await ops.discover(paths=["tests/test_a.py"])

            assert result.action == "discover"

    @pytest.mark.asyncio
    async def test_discover_empty_repo_provides_agentic_hint(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            result = await ops.discover()

            assert result.action == "discover"
            # With no workspaces, should have agentic hint
            assert result.agentic_hint is not None or result.targets == []

    @pytest.mark.asyncio
    async def test_discover_uses_index_contexts(self) -> None:
        """Test that discover tries to use index contexts first."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()

            # Mock contexts returning a python project
            mock_context = MagicMock()
            mock_context.root_path = ""
            coordinator.get_contexts = AsyncMock(return_value=[mock_context])

            (root / "pytest.ini").write_text("")

            ops = TestOps(root, coordinator)
            result = await ops.discover()

            assert result.action == "discover"
            coordinator.get_contexts.assert_called_once()

    @pytest.mark.asyncio
    async def test_discover_falls_back_to_filesystem_on_index_error(self) -> None:
        """Test fallback when index fails."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            coordinator.get_contexts = AsyncMock(side_effect=Exception("Index error"))

            (root / "pytest.ini").write_text("")
            (root / "tests").mkdir()
            (root / "tests" / "test_x.py").write_text("def test_x(): pass")

            ops = TestOps(root, coordinator)
            result = await ops.discover()

            # Should still work via filesystem fallback
            assert result.action == "discover"


# =============================================================================
# TestOps._generate_agentic_hint()
# =============================================================================


class TestAgenticHint:
    """Tests for agentic hint generation."""

    @pytest.mark.asyncio
    async def test_hint_includes_python(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            coordinator.get_file_stats = AsyncMock(return_value={"python": 10})

            ops = TestOps(root, coordinator)
            hint = await ops._generate_agentic_hint()

            assert "pytest" in hint.lower()

    @pytest.mark.asyncio
    async def test_hint_includes_javascript(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            coordinator.get_file_stats = AsyncMock(return_value={"javascript": 10})

            ops = TestOps(root, coordinator)
            hint = await ops._generate_agentic_hint()

            assert "npm test" in hint.lower() or "jest" in hint.lower()

    @pytest.mark.asyncio
    async def test_hint_includes_go(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            coordinator.get_file_stats = AsyncMock(return_value={"go": 5})

            ops = TestOps(root, coordinator)
            hint = await ops._generate_agentic_hint()

            assert "go test" in hint.lower()

    @pytest.mark.asyncio
    async def test_hint_includes_rust(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            coordinator.get_file_stats = AsyncMock(return_value={"rust": 5})

            ops = TestOps(root, coordinator)
            hint = await ops._generate_agentic_hint()

            assert "cargo test" in hint.lower()

    @pytest.mark.asyncio
    async def test_hint_multiple_languages(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            coordinator.get_file_stats = AsyncMock(return_value={"python": 10, "go": 5})

            ops = TestOps(root, coordinator)
            hint = await ops._generate_agentic_hint()

            assert "pytest" in hint.lower()
            assert "go test" in hint.lower()

    @pytest.mark.asyncio
    async def test_hint_no_languages(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            coordinator.get_file_stats = AsyncMock(return_value={})

            ops = TestOps(root, coordinator)
            hint = await ops._generate_agentic_hint()

            assert "no test framework detected" in hint.lower()

    @pytest.mark.asyncio
    async def test_hint_handles_index_error(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            coordinator.get_file_stats = AsyncMock(side_effect=Exception("Index error"))

            ops = TestOps(root, coordinator)
            hint = await ops._generate_agentic_hint()

            # Should still return a hint, just without language-specific suggestions
            assert hint is not None
            assert "no test framework detected" in hint.lower()


# =============================================================================
# TestOps.run()
# =============================================================================


class TestTestOpsRun:
    """Tests for TestOps.run()."""

    @pytest.mark.asyncio
    async def test_run_returns_running_status(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")
            (root / "tests").mkdir()
            (root / "tests" / "test_x.py").write_text("def test_x(): pass")

            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            result = await ops.run()

            assert result.action == "run"
            assert result.run_status is not None
            assert result.run_status.status == "running"
            assert result.run_status.run_id is not None

            # Cleanup - cancel the running task
            if result.run_status.run_id in ops._active_runs:
                ops._active_runs[result.run_status.run_id].cancel_event.set()
                ops._active_runs[result.run_status.run_id].task.cancel()

    @pytest.mark.asyncio
    async def test_run_creates_artifact_directory(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")

            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            result = await ops.run()

            assert result.run_status is not None
            artifact_dir = result.run_status.artifact_dir
            assert artifact_dir is not None
            assert Path(artifact_dir).exists()

            # Cleanup
            if result.run_status.run_id in ops._active_runs:
                ops._active_runs[result.run_status.run_id].cancel_event.set()
                ops._active_runs[result.run_status.run_id].task.cancel()

    @pytest.mark.asyncio
    async def test_run_with_specific_targets(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")
            (root / "tests").mkdir()
            (root / "tests" / "test_a.py").write_text("def test_a(): pass")

            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            # First discover to get target IDs
            discover_result = await ops.discover()
            if discover_result.targets:
                target_id = discover_result.targets[0].target_id
                result = await ops.run(targets=[target_id])

                assert result.action == "run"
                assert result.run_status is not None

                # Cleanup
                if result.run_status.run_id in ops._active_runs:
                    ops._active_runs[result.run_status.run_id].cancel_event.set()
                    ops._active_runs[result.run_status.run_id].task.cancel()


# =============================================================================
# TestOps.status()
# =============================================================================


class TestTestOpsStatus:
    """Tests for TestOps.status()."""

    @pytest.mark.asyncio
    async def test_status_of_active_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")

            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            # Start a run
            run_result = await ops.run()
            assert run_result.run_status is not None
            run_id = run_result.run_status.run_id

            # Get status
            status_result = await ops.status(run_id)

            assert status_result.action == "status"
            assert status_result.run_status is not None
            assert status_result.run_status.run_id == run_id

            # Cleanup
            if run_id in ops._active_runs:
                ops._active_runs[run_id].cancel_event.set()
                ops._active_runs[run_id].task.cancel()

    @pytest.mark.asyncio
    async def test_status_of_unknown_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            # Get status of non-existent run
            status_result = await ops.status("unknown-run-id")

            assert status_result.action == "status"
            assert status_result.run_status is not None
            assert status_result.run_status.status == "not_found"

    @pytest.mark.asyncio
    async def test_status_loads_persisted_result(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            # Create a fake persisted result
            run_id = "persisted-run"
            artifact_dir = ops._artifacts_base / run_id
            artifact_dir.mkdir(parents=True)
            result_file = artifact_dir / "result.json"
            result_file.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "status": "completed",
                        "progress": {
                            "targets": {"total": 5, "completed": 5, "failed": 0, "running": 0},
                            "cases": {
                                "total": 10,
                                "passed": 10,
                                "failed": 0,
                                "skipped": 0,
                                "errors": 0,
                            },
                        },
                        "failures": [],
                        "diagnostics": [],
                    }
                )
            )

            # Get status should load from artifact
            status_result = await ops.status(run_id)

            assert status_result.action == "status"
            assert status_result.run_status is not None
            assert status_result.run_status.status == "completed"
            assert status_result.run_status.progress is not None
            assert status_result.run_status.progress.targets.total == 5


# =============================================================================
# TestOps.cancel()
# =============================================================================


class TestTestOpsCancel:
    """Tests for TestOps.cancel()."""

    @pytest.mark.asyncio
    async def test_cancel_active_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")

            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            # Start a run
            run_result = await ops.run()
            assert run_result.run_status is not None
            run_id = run_result.run_status.run_id

            # Cancel it
            cancel_result = await ops.cancel(run_id)

            assert cancel_result.action == "cancel"
            assert cancel_result.run_status is not None
            assert cancel_result.run_status.status == "cancelled"
            assert run_id not in ops._active_runs

    @pytest.mark.asyncio
    async def test_cancel_unknown_run(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            # Cancel non-existent run
            cancel_result = await ops.cancel("unknown-run-id")

            assert cancel_result.action == "cancel"
            assert cancel_result.run_status is not None
            assert cancel_result.run_status.status == "cancelled"


# =============================================================================
# TestOps._persist_result() and _load_result()
# =============================================================================


class TestPersistAndLoadResult:
    """Tests for result persistence."""

    def test_persist_and_load_roundtrip(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            from codeplane.testing.models import TestRunStatus

            artifact_dir = root / "artifacts" / "test-run"
            artifact_dir.mkdir(parents=True)

            status = TestRunStatus(
                run_id="test-run",
                status="completed",
                progress=TestProgress(
                    targets=TargetProgress(total=3, completed=3, failed=1),
                    cases=TestCaseProgress(total=10, passed=8, failed=2, skipped=0, errors=0),
                ),
                failures=[],
                duration_seconds=5.5,
            )

            ops._persist_result(artifact_dir, status)

            loaded = ops._load_result(artifact_dir)

            assert loaded is not None
            assert loaded.run_id == "test-run"
            assert loaded.status == "completed"
            assert loaded.progress.targets.total == 3
            assert loaded.progress.cases.passed == 8

    def test_load_result_returns_none_for_missing_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            artifact_dir = root / "nonexistent"

            loaded = ops._load_result(artifact_dir)

            assert loaded is None

    def test_load_result_returns_none_for_invalid_json(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            ops = TestOps(root, coordinator)

            artifact_dir = root / "artifacts" / "bad-run"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "result.json").write_text("not valid json")

            loaded = ops._load_result(artifact_dir)

            assert loaded is None


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
