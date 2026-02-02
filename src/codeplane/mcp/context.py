"""Application context for MCP handlers.

Single object passed to all tool handlers with access to ops classes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codeplane.files.ops import FileOps
    from codeplane.git.ops import GitOps
    from codeplane.index.ops import IndexCoordinator
    from codeplane.mcp.session import SessionManager
    from codeplane.mutation.ops import MutationOps
    from codeplane.refactor.ops import RefactorOps
    from codeplane.testing.ops import TestOps


@dataclass
class AppContext:
    """Context object passed to all MCP tool handlers.

    Provides access to all ops classes and shared state.
    """

    repo_root: Path
    git_ops: GitOps
    coordinator: IndexCoordinator
    file_ops: FileOps
    mutation_ops: MutationOps
    refactor_ops: RefactorOps
    test_ops: TestOps
    session_manager: SessionManager

    @classmethod
    def create(
        cls,
        repo_root: Path,
        db_path: Path,
        tantivy_path: Path,
        coordinator: IndexCoordinator | None = None,
    ) -> AppContext:
        """Factory to create context with all ops wired together.

        Args:
            repo_root: Repository root path
            db_path: Path to SQLite database
            tantivy_path: Path to Tantivy index directory
            coordinator: Optional existing IndexCoordinator (reuses if provided)
        """
        from codeplane.files.ops import FileOps
        from codeplane.git.ops import GitOps
        from codeplane.index.ops import IndexCoordinator as IC
        from codeplane.mcp.session import SessionManager
        from codeplane.mutation.ops import MutationOps
        from codeplane.refactor.ops import RefactorOps
        from codeplane.testing.ops import TestOps

        git_ops = GitOps(repo_root)

        # Reuse existing coordinator or create new one
        if coordinator is None:
            coordinator = IC(repo_root, db_path, tantivy_path)

        file_ops = FileOps(repo_root)

        # MutationOps triggers reindex on mutation
        def on_mutation(paths: list[Path]) -> None:
            import asyncio

            asyncio.create_task(coordinator.reindex_incremental(paths))

        mutation_ops = MutationOps(repo_root, on_mutation=on_mutation)
        refactor_ops = RefactorOps(repo_root, coordinator)
        test_ops = TestOps(repo_root, coordinator)
        session_manager = SessionManager()

        return cls(
            repo_root=repo_root,
            git_ops=git_ops,
            coordinator=coordinator,
            file_ops=file_ops,
            mutation_ops=mutation_ops,
            refactor_ops=refactor_ops,
            test_ops=test_ops,
            session_manager=session_manager,
        )
