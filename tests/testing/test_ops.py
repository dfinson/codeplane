"""Comprehensive tests for TestOps operations."""

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
    coordinator.get_test_targets = AsyncMock(
        return_value=[]
    )  # Index-first: return empty by default
    coordinator.get_context_runtime = AsyncMock(return_value=None)
    coordinator.get_coverage_capability = AsyncMock(return_value={})
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
        pack_class = runner_registry.get("python.pytest")
        assert pack_class is not None
        pack = pack_class()  # Instantiate

        ws = DetectedWorkspace(
            root=Path("/repo"),
            pack=pack,
            confidence=0.95,
        )

        assert ws.root == Path("/repo")
        assert ws.pack.pack_id == "python.pytest"
        assert ws.confidence == 0.95


# =============================================================================
# TestOps.discover()
# =============================================================================


class TestTestOpsDiscover:
    """Tests for TestOps.discover().

    Note: discover() is now index-first. Tests mock the coordinator to return
    indexed test targets rather than expecting filesystem discovery.
    """

    @pytest.mark.asyncio
    async def test_discover_returns_test_result(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")
            (root / "tests").mkdir()
            (root / "tests" / "test_example.py").write_text("def test_foo(): pass")

            coordinator = create_mock_coordinator()
            # Mock indexed test targets
            mock_target = MagicMock()
            mock_target.target_id = "test:tests/test_example.py"
            mock_target.selector = "tests/test_example.py"
            mock_target.kind = "file"
            mock_target.language = "python"
            mock_target.runner_pack_id = "python.pytest"
            mock_target.workspace_root = str(root)
            mock_target.test_count = None
            coordinator.get_test_targets = AsyncMock(return_value=[mock_target])

            ops = TestOps(root, coordinator)

            result = await ops.discover()

            assert result.action == "discover"
            assert result.targets is not None
            assert len(result.targets) == 1

    @pytest.mark.asyncio
    async def test_discover_with_paths_filter(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            coordinator = create_mock_coordinator()
            # Mock two indexed targets
            mock_target_a = MagicMock()
            mock_target_a.target_id = "test:tests/test_a.py"
            mock_target_a.selector = "tests/test_a.py"
            mock_target_a.kind = "file"
            mock_target_a.language = "python"
            mock_target_a.runner_pack_id = "python.pytest"
            mock_target_a.workspace_root = str(root)
            mock_target_a.test_count = None

            mock_target_b = MagicMock()
            mock_target_b.target_id = "test:tests/test_b.py"
            mock_target_b.selector = "tests/test_b.py"
            mock_target_b.kind = "file"
            mock_target_b.language = "python"
            mock_target_b.runner_pack_id = "python.pytest"
            mock_target_b.workspace_root = str(root)
            mock_target_b.test_count = None

            coordinator.get_test_targets = AsyncMock(return_value=[mock_target_a, mock_target_b])

            ops = TestOps(root, coordinator)

            result = await ops.discover(paths=["tests/test_a.py"])

            assert result.action == "discover"
            # Should filter to just test_a
            assert len(result.targets) == 1  # type: ignore
            assert result.targets[0].selector == "tests/test_a.py"  # type: ignore

    @pytest.mark.asyncio
    async def test_discover_empty_repo_provides_agentic_hint(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            # Empty index - no test targets
            coordinator.get_test_targets = AsyncMock(return_value=[])

            ops = TestOps(root, coordinator)

            result = await ops.discover()

            assert result.action == "discover"
            # With no indexed targets, should have agentic hint
            assert result.agentic_hint is not None
            assert result.targets == []

    @pytest.mark.asyncio
    async def test_discover_queries_index(self) -> None:
        """Test that discover queries the index for test targets."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            coordinator = create_mock_coordinator()
            coordinator.get_test_targets = AsyncMock(return_value=[])

            ops = TestOps(root, coordinator)
            result = await ops.discover()

            assert result.action == "discover"
            # Should have called get_test_targets
            coordinator.get_test_targets.assert_called_once()

    @pytest.mark.asyncio
    async def test_discover_index_first_no_filesystem_fallback(self) -> None:
        """Test that discover does not fall back to filesystem - index is authoritative."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Set up actual test file on filesystem
            (root / "pytest.ini").write_text("")
            (root / "tests").mkdir()
            (root / "tests" / "test_x.py").write_text("def test_x(): pass")

            coordinator = create_mock_coordinator()
            # Index returns empty - discover should return empty, not fall back to filesystem
            coordinator.get_test_targets = AsyncMock(return_value=[])

            ops = TestOps(root, coordinator)
            result = await ops.discover()

            assert result.action == "discover"
            # Should return empty targets from index, not discover from filesystem
            assert result.targets == []
            # Should provide agentic hint when no targets found
            assert result.agentic_hint is not None


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
    """Tests for TestOps.run().

    Note: run() is now blocking and index-first. Tests mock the coordinator
    to return indexed test targets. run() always awaits test completion.
    """

    @pytest.mark.asyncio
    async def test_run_returns_completed_status(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")
            (root / "tests").mkdir()
            (root / "tests" / "test_x.py").write_text("def test_x(): pass")

            coordinator = create_mock_coordinator()
            mock_target = MagicMock()
            mock_target.target_id = "test:tests/test_x.py"
            mock_target.selector = "tests/test_x.py"
            mock_target.kind = "file"
            mock_target.language = "python"
            mock_target.runner_pack_id = "python.pytest"
            mock_target.workspace_root = str(root)
            mock_target.test_count = None
            coordinator.get_test_targets = AsyncMock(return_value=[mock_target])

            ops = TestOps(root, coordinator)

            result = await ops.run()

            assert result.action == "run"
            assert result.run_status is not None
            # Blocking run returns terminal status
            assert result.run_status.status in ("completed", "failed")
            assert result.run_status.run_id is not None

    @pytest.mark.asyncio
    async def test_run_creates_artifact_directory(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")

            coordinator = create_mock_coordinator()
            mock_target = MagicMock()
            mock_target.target_id = "test:tests/test_x.py"
            mock_target.selector = "tests/test_x.py"
            mock_target.kind = "file"
            mock_target.language = "python"
            mock_target.runner_pack_id = "python.pytest"
            mock_target.workspace_root = str(root)
            mock_target.test_count = None
            coordinator.get_test_targets = AsyncMock(return_value=[mock_target])

            ops = TestOps(root, coordinator)

            result = await ops.run()

            assert result.run_status is not None
            artifact_dir = result.run_status.artifact_dir
            assert artifact_dir is not None
            assert Path(artifact_dir).exists()

    @pytest.mark.asyncio
    async def test_run_with_specific_targets(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pytest.ini").write_text("")
            (root / "tests").mkdir()
            (root / "tests" / "test_a.py").write_text("def test_a(): pass")

            coordinator = create_mock_coordinator()
            mock_target = MagicMock()
            mock_target.target_id = "test:tests/test_a.py"
            mock_target.selector = "tests/test_a.py"
            mock_target.kind = "file"
            mock_target.language = "python"
            mock_target.runner_pack_id = "python.pytest"
            mock_target.workspace_root = str(root)
            mock_target.test_count = None
            coordinator.get_test_targets = AsyncMock(return_value=[mock_target])

            ops = TestOps(root, coordinator)

            result = await ops.run(targets=["test:tests/test_a.py"])

            assert result.action == "run"
            assert result.run_status is not None



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
            assert loaded.progress is not None
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
