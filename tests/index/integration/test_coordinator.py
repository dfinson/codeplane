"""Integration tests for IndexCoordinator initialization.

Tests the full initialization flow:
Discovery → Authority → Membership → Probe → Router → Index
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index.ops import (
    IndexCoordinator,
    IndexStats,
    InitResult,
    SearchMode,
    SearchResult,
)


class TestCoordinatorInitialization:
    """Tests for IndexCoordinator.initialize()."""

    @pytest.mark.asyncio
    async def test_initialize_python_project(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should initialize a Python project."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            result = await coordinator.initialize()

            assert isinstance(result, InitResult)
            assert result.contexts_discovered >= 1  # At least Python context
            assert result.files_indexed >= 1
            assert len(result.errors) == 0
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_initialize_creates_database(
        self, integration_repo: Path, tmp_path: Path
    ) -> None:
        """Should create database file."""
        db_path = tmp_path / "new_index.db"
        tantivy_path = tmp_path / "tantivy"

        assert not db_path.exists()

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize()
            assert db_path.exists()
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_initialize_creates_tantivy_index(
        self, integration_repo: Path, tmp_path: Path
    ) -> None:
        """Should create Tantivy index directory."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy_new"

        assert not tantivy_path.exists()

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize()
            assert tantivy_path.exists()
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_initialize_indexes_all_python_files(
        self, integration_repo: Path, tmp_path: Path
    ) -> None:
        """Should index all Python files in the project."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            result = await coordinator.initialize()

            # Should have indexed at least the main files
            # src/__init__.py, src/main.py, src/utils.py, tests/__init__.py, tests/test_main.py
            assert result.files_indexed >= 4
        finally:
            coordinator.close()


class TestCoordinatorSearch:
    """Tests for IndexCoordinator search operations."""

    @pytest.mark.asyncio
    async def test_search_text(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should search file content."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize()

            # Search for content that exists
            results = await coordinator.search("Hello", mode=SearchMode.TEXT)

            assert len(results) >= 1
            assert all(isinstance(r, SearchResult) for r in results)
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_search_symbols(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should search by symbol name."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize()

            # Search for function name
            results = await coordinator.search("helper", mode=SearchMode.SYMBOL)

            assert len(results) >= 1
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_search_path(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should search by file path."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize()

            # Search for path pattern
            results = await coordinator.search("utils", mode=SearchMode.PATH)

            assert len(results) >= 1
            assert any("utils" in r.path for r in results)
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_search_no_results(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should return empty list when no matches."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize()

            results = await coordinator.search("xyznonexistent123")

            assert len(results) == 0
        finally:
            coordinator.close()


class TestCoordinatorReindex:
    """Tests for IndexCoordinator reindex operations."""

    @pytest.mark.asyncio
    async def test_reindex_incremental(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should perform incremental reindex."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize()

            # Modify a file
            (integration_repo / "src" / "main.py").write_text('''"""Modified main."""

def main():
    print("Modified!")


def new_function():
    """New function added."""
    return 42
''')

            # Reindex incrementally
            stats = await coordinator.reindex_incremental([Path("src/main.py")])

            assert isinstance(stats, IndexStats)
            assert stats.files_processed == 1
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_reindex_full(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should perform full reindex."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize()

            # Add a new file
            (integration_repo / "src" / "new_module.py").write_text('''"""New module."""

def new_func():
    return "new"
''')

            # Full reindex
            stats = await coordinator.reindex_full()

            assert isinstance(stats, IndexStats)
            assert stats.files_processed >= 1
        finally:
            coordinator.close()


class TestCoordinatorMonorepo:
    """Tests for IndexCoordinator with monorepo structure."""

    @pytest.mark.asyncio
    async def test_initialize_monorepo(self, integration_monorepo: Path, tmp_path: Path) -> None:
        """Should discover multiple contexts in monorepo."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_monorepo, db_path, tantivy_path)

        try:
            result = await coordinator.initialize()

            # Should discover JavaScript contexts for packages
            assert result.contexts_discovered >= 2  # pkg-a and pkg-b
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_search_across_packages(self, integration_monorepo: Path, tmp_path: Path) -> None:
        """Should search across all packages."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_monorepo, db_path, tantivy_path)

        try:
            await coordinator.initialize()

            # Search for something in both packages
            results = await coordinator.search("hello")

            assert len(results) >= 1
        finally:
            coordinator.close()
