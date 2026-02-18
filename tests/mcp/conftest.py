"""Shared fixtures for MCP tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeplane.mcp.registry import ToolRegistry, registry

if TYPE_CHECKING:
    pass


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


@pytest.fixture
def clean_registry() -> Generator[ToolRegistry, None, None]:
    """Clear and yield the global registry, restore after test."""
    # Store existing registrations
    original_tools = dict(registry._tools)
    registry.clear()
    yield registry
    # Restore
    registry._tools = original_tools


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOps with common methods configured."""
    mock = MagicMock()
    mock.repo = MagicMock()
    mock.repo.workdir = "/tmp/test-repo"

    # Status methods
    mock.status.return_value = {}
    mock.head.return_value = MagicMock(target_sha="abc1234567890", is_detached=False)
    mock.state.return_value = 0
    mock.current_branch.return_value = "main"

    # Diff
    mock.diff.return_value = MagicMock(
        files_changed=0,
        total_additions=0,
        total_deletions=0,
        files=[],
        patch="",
    )

    # Log
    mock.log.return_value = []

    # Branch operations
    mock.branches.return_value = []
    mock.create_branch.return_value = MagicMock(name="new-branch", target="HEAD")

    return mock


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create a mock IndexCoordinator."""
    mock = MagicMock()
    mock._initialized = True
    mock.get_current_epoch.return_value = 1
    mock.search = AsyncMock(return_value=[])
    mock.get_def = AsyncMock(return_value=None)
    mock.get_references = AsyncMock(return_value=[])
    mock.get_lint_tools = AsyncMock(return_value=[])
    mock.get_test_targets = AsyncMock(return_value=[])
    mock.map_repo = AsyncMock(
        return_value=MagicMock(
            structure=None,
            languages=[],
            entry_points=[],
            dependencies=None,
            test_layout=None,
            public_api=[],
        )
    )
    mock.db = MagicMock()
    return mock


@pytest.fixture
def mock_file_ops() -> MagicMock:
    """Create a mock FileOps."""
    mock = MagicMock()
    mock.read_source.return_value = MagicMock(files=[])
    mock.list_files.return_value = MagicMock(
        path="",
        entries=[],
        total=0,
        truncated=False,
    )
    return mock


@pytest.fixture
def mock_mutation_ops() -> MagicMock:
    """Create a mock MutationOps."""
    mock = MagicMock()
    mock.write_source.return_value = MagicMock(
        applied=True,
        dry_run=False,
        delta=MagicMock(
            mutation_id="mut_123",
            files_changed=0,
            insertions=0,
            deletions=0,
            files=[],
        ),
        dry_run_info=None,
    )
    return mock


@pytest.fixture
def mock_refactor_ops() -> MagicMock:
    """Create a mock RefactorOps."""
    mock = MagicMock()
    mock.rename = AsyncMock(
        return_value=MagicMock(
            refactor_id="ref_123",
            status="pending",
            preview=None,
            divergence=None,
        )
    )
    mock.move = AsyncMock(
        return_value=MagicMock(
            refactor_id="ref_456",
            status="pending",
            preview=None,
            divergence=None,
        )
    )
    mock.delete = AsyncMock(
        return_value=MagicMock(
            refactor_id="ref_789",
            status="pending",
            preview=None,
            divergence=None,
        )
    )
    mock.apply = AsyncMock(
        return_value=MagicMock(
            refactor_id="ref_123",
            status="applied",
            preview=None,
            divergence=None,
        )
    )
    mock.cancel = AsyncMock(
        return_value=MagicMock(
            refactor_id="ref_123",
            status="cancelled",
            preview=None,
            divergence=None,
        )
    )
    mock.inspect = AsyncMock(
        return_value=MagicMock(
            path="test.py",
            matches=[],
        )
    )
    return mock


@pytest.fixture
def mock_test_ops() -> MagicMock:
    """Create a mock TestOps."""
    mock = MagicMock()
    mock.discover = AsyncMock(
        return_value=MagicMock(
            action="discover",
            targets=[],
            agentic_hint=None,
        )
    )
    mock.run = AsyncMock(
        return_value=MagicMock(
            action="run",
            run_status=MagicMock(
                run_id="run_123",
                status="completed",
                duration_seconds=1.5,
                artifact_dir=None,
                progress=None,
                failures=None,
                diagnostics=None,
                coverage=None,
            ),
            agentic_hint=None,
        )
    )
    mock.status = AsyncMock(
        return_value=MagicMock(
            action="status",
            run_status=MagicMock(
                run_id="run_123",
                status="running",
                duration_seconds=0.5,
                artifact_dir=None,
                progress=None,
                failures=None,
                diagnostics=None,
                coverage=None,
            ),
            agentic_hint=None,
        )
    )
    mock.cancel = AsyncMock(
        return_value=MagicMock(
            action="cancel",
            run_status=MagicMock(
                run_id="run_123",
                status="cancelled",
                duration_seconds=0.2,
                artifact_dir=None,
                progress=None,
                failures=None,
                diagnostics=None,
                coverage=None,
            ),
            agentic_hint=None,
        )
    )
    return mock


@pytest.fixture
def mock_lint_ops() -> MagicMock:
    """Create a mock LintOps."""
    mock = MagicMock()
    mock._repo_root = Path("/tmp/test-repo")
    mock.check = AsyncMock(
        return_value=MagicMock(
            action="check",
            dry_run=False,
            status="clean",
            total_diagnostics=0,
            total_files_modified=0,
            duration_seconds=0.5,
            tools_run=[],
            agentic_hint=None,
        )
    )
    return mock


@pytest.fixture
def mock_session_manager() -> MagicMock:
    """Create a mock SessionManager."""
    return MagicMock()


@pytest.fixture
def mock_context(
    tmp_path: Path,
    mock_git_ops: MagicMock,
    mock_coordinator: MagicMock,
    mock_file_ops: MagicMock,
    mock_mutation_ops: MagicMock,
    mock_refactor_ops: MagicMock,
    mock_test_ops: MagicMock,
    mock_lint_ops: MagicMock,
    mock_session_manager: MagicMock,
) -> MagicMock:
    """Create a fully mocked AppContext."""
    ctx = MagicMock()
    ctx.repo_root = tmp_path
    ctx.git_ops = mock_git_ops
    ctx.coordinator = mock_coordinator
    ctx.file_ops = mock_file_ops
    ctx.mutation_ops = mock_mutation_ops
    ctx.refactor_ops = mock_refactor_ops
    ctx.test_ops = mock_test_ops
    ctx.lint_ops = mock_lint_ops
    ctx.session_manager = mock_session_manager
    return ctx
