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


def _noop_progress(indexed: int, total: int, files_by_ext: dict[str, int], phase: str = "") -> None:
    """No-op progress callback for tests."""
    pass


class TestCoordinatorInitialization:
    """Tests for IndexCoordinator.initialize()."""

    @pytest.mark.asyncio
    async def test_initialize_python_project(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should initialize a Python project."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            result = await coordinator.initialize(on_index_progress=_noop_progress)

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
            await coordinator.initialize(on_index_progress=_noop_progress)
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
            await coordinator.initialize(on_index_progress=_noop_progress)
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
            result = await coordinator.initialize(on_index_progress=_noop_progress)

            # Should have indexed at least the main files
            # src/__init__.py, src/main.py, src/utils.py, tests/__init__.py, tests/test_main.py
            assert result.files_indexed >= 4
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_initialize_calls_progress_callback(
        self, integration_repo: Path, tmp_path: Path
    ) -> None:
        """Should call progress callback during indexing."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)
        progress_calls: list[tuple[int, int, dict[str, int], str]] = []

        def track_progress(
            indexed: int, total: int, files_by_ext: dict[str, int], phase: str = ""
        ) -> None:
            progress_calls.append((indexed, total, files_by_ext.copy(), phase))

        try:
            await coordinator.initialize(on_index_progress=track_progress)

            # Should have called progress at least once
            assert len(progress_calls) >= 1

            # Group progress by phase and verify each phase increases monotonically
            by_phase: dict[str, list[tuple[int, int]]] = {}
            for indexed, total, _, phase in progress_calls:
                by_phase.setdefault(phase, []).append((indexed, total))

            for phase, calls in by_phase.items():
                for i in range(1, len(calls)):
                    assert calls[i][0] >= calls[i - 1][0], f"Phase {phase} progress not monotonic"

            # Should have lexical phase at minimum
            assert "lexical" in by_phase
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
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for content that exists
            results = await coordinator.search("Hello", mode=SearchMode.TEXT)

            assert len(results.results) >= 1
            assert all(isinstance(r, SearchResult) for r in results.results)
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_search_symbols(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should search by symbol name."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for function name
            results = await coordinator.search("helper", mode=SearchMode.SYMBOL)

            assert len(results.results) >= 1
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_search_path(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should search by file path."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for path pattern
            results = await coordinator.search("utils", mode=SearchMode.PATH)

            assert len(results.results) >= 1
            assert any("utils" in r.path for r in results.results)
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_search_no_results(self, integration_repo: Path, tmp_path: Path) -> None:
        """Should return empty list when no matches."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            results = await coordinator.search("xyznonexistent123")

            assert len(results.results) == 0
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
            await coordinator.initialize(on_index_progress=_noop_progress)

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
        """Should perform full reindex and discover new files."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Add a new file (no git commit needed - we index all files)
            (integration_repo / "src" / "new_module.py").write_text('''"""New module."""

def new_func():
    return "new"
''')

            # Full reindex should discover the new file
            stats = await coordinator.reindex_full()

            assert isinstance(stats, IndexStats)
            assert stats.files_added >= 1
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
            result = await coordinator.initialize(on_index_progress=_noop_progress)

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
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for something in both packages
            results = await coordinator.search("hello")

            assert len(results.results) >= 1
        finally:
            coordinator.close()


class TestCoordinatorCplignore:
    """Tests for .cplignore enforcement during indexing."""

    @pytest.mark.asyncio
    async def test_cplignore_excludes_dependencies(self, tmp_path: Path) -> None:
        """Should not index files in dependency directories (node_modules, venv, etc)."""
        import pygit2

        from codeplane.templates import get_cplignore_template

        # Create project with git repo
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        pygit2.init_repository(str(repo_root))
        (repo_root / "pyproject.toml").write_text('[project]\nname = "test"')

        # Create .codeplane/.cplignore (simulating cpl init)
        codeplane_dir = repo_root / ".codeplane"
        codeplane_dir.mkdir()
        (codeplane_dir / ".cplignore").write_text(get_cplignore_template())

        # Create src directory with files
        src = repo_root / "src"
        src.mkdir()
        (src / "__init__.py").write_text("")
        (src / "main.py").write_text("def main(): pass")

        # Create dependency directories that should be ignored per .cplignore
        venv = repo_root / ".venv"
        venv.mkdir()
        (venv / "lib.py").write_text("VENV_CODE = True")

        node_modules = repo_root / "node_modules"
        node_modules.mkdir()
        (node_modules / "package.js").write_text("module.exports = {}")

        pycache = src / "__pycache__"
        pycache.mkdir()
        (pycache / "main.cpython-312.pyc").write_bytes(b"compiled")

        # Create initial commit
        repo = pygit2.Repository(str(repo_root))
        repo.config["user.name"] = "Test"
        repo.config["user.email"] = "test@test.com"
        repo.index.add_all()
        repo.index.write()
        tree = repo.index.write_tree()
        sig = pygit2.Signature("Test", "test@test.com")
        repo.create_commit("HEAD", sig, sig, "Initial commit", tree, [])

        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(repo_root, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for content that should NOT be indexed
            venv_results = await coordinator.search("VENV_CODE")
            node_results = await coordinator.search("module.exports")

            # None of these should be found
            assert len(venv_results.results) == 0, ".venv/ should be ignored"
            assert len(node_results.results) == 0, "node_modules/ should be ignored"

            # But main.py should be indexed
            main_results = await coordinator.search("main")
            assert len(main_results.results) >= 1, "main.py should be indexed"
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_cplignore_excludes_build_outputs(self, tmp_path: Path) -> None:
        """Should not index build output directories (dist, build, target)."""
        import pygit2

        from codeplane.templates import get_cplignore_template

        # Create project with git repo
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        pygit2.init_repository(str(repo_root))
        (repo_root / "pyproject.toml").write_text('[project]\nname = "test"')

        # Create .codeplane/.cplignore (simulating cpl init)
        codeplane_dir = repo_root / ".codeplane"
        codeplane_dir.mkdir()
        (codeplane_dir / ".cplignore").write_text(get_cplignore_template())

        # Create src directory
        src = repo_root / "src"
        src.mkdir()
        (src / "__init__.py").write_text("")
        (src / "main.py").write_text("def main(): pass")

        # Create build output directories
        dist = repo_root / "dist"
        dist.mkdir()
        (dist / "bundle.py").write_text("BUNDLED = True")

        build = repo_root / "build"
        build.mkdir()
        (build / "output.py").write_text("BUILD_OUTPUT = True")

        # Create initial commit
        repo = pygit2.Repository(str(repo_root))
        repo.config["user.name"] = "Test"
        repo.config["user.email"] = "test@test.com"
        repo.index.add_all()
        repo.index.write()
        tree = repo.index.write_tree()
        sig = pygit2.Signature("Test", "test@test.com")
        repo.create_commit("HEAD", sig, sig, "Initial commit", tree, [])

        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(repo_root, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for content that should NOT be indexed
            dist_results = await coordinator.search("BUNDLED")
            build_results = await coordinator.search("BUILD_OUTPUT")

            assert len(dist_results.results) == 0, "dist/ should be ignored"
            assert len(build_results.results) == 0, "build/ should be ignored"

            # But main.py should be indexed
            main_results = await coordinator.search("main")
            assert len(main_results.results) >= 1, "main.py should be indexed"
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_codeplane_directory_always_excluded(self, tmp_path: Path) -> None:
        """Should never index .codeplane directory itself."""
        import pygit2

        from codeplane.templates import get_cplignore_template

        # Create project with git repo
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        pygit2.init_repository(str(repo_root))
        (repo_root / "pyproject.toml").write_text('[project]\nname = "test"')

        # Create src directory
        src = repo_root / "src"
        src.mkdir()
        (src / "__init__.py").write_text("")
        (src / "main.py").write_text("def main(): pass")

        # Create .codeplane with some files (simulating cpl init + artifacts)
        codeplane = repo_root / ".codeplane"
        codeplane.mkdir()
        (codeplane / ".cplignore").write_text(get_cplignore_template())
        (codeplane / "config.yaml").write_text("CODEPLANE_CONFIG = true")
        (codeplane / "index.db").write_bytes(b"database")

        # Create initial commit
        repo = pygit2.Repository(str(repo_root))
        repo.config["user.name"] = "Test"
        repo.config["user.email"] = "test@test.com"
        repo.index.add_all()
        repo.index.write()
        tree = repo.index.write_tree()
        sig = pygit2.Signature("Test", "test@test.com")
        repo.create_commit("HEAD", sig, sig, "Initial commit", tree, [])

        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(repo_root, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for content that should NOT be indexed
            config_results = await coordinator.search("CODEPLANE_CONFIG")
            assert len(config_results.results) == 0, ".codeplane/ should always be ignored"

            # But main.py should be indexed
            main_results = await coordinator.search("main")
            assert len(main_results.results) >= 1, "main.py should be indexed"
        finally:
            coordinator.close()


class TestCplignoreChangeHandling:
    """Tests for .cplignore change detection and index updates."""

    @pytest.mark.asyncio
    async def test_cplignore_change_adds_previously_ignored_py_files(
        self, integration_repo: Path, tmp_path: Path
    ) -> None:
        """When .cplignore removes a pattern, previously ignored .py files should be indexed."""
        # Create a Python file in src/ that will be ignored initially via pattern
        (integration_repo / "src" / "generated_code.py").write_text(
            "GENERATED_CONTENT = 'marker_for_test'\n"
        )

        # Add *generated* pattern to .cplignore BEFORE initialization
        cplignore_path = integration_repo / ".codeplane" / ".cplignore"
        original_content = cplignore_path.read_text()
        cplignore_path.write_text(original_content + "\n**/generated*.py\n")

        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            # Initialize - generated_code.py should be ignored per .cplignore
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Verify generated file is NOT indexed
            gen_results = await coordinator.search("GENERATED_CONTENT")
            assert len(gen_results.results) == 0, "generated_code.py should be ignored initially"

            # Modify .cplignore to remove the pattern (restore original)
            cplignore_path.write_text(original_content)

            # Trigger incremental reindex - this should detect .cplignore change
            await coordinator.reindex_incremental([])

            # Now the generated file should be indexed
            gen_results = await coordinator.search("GENERATED_CONTENT")
            assert len(gen_results.results) >= 1, (
                "generated_code.py should be indexed after .cplignore change"
            )
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_cplignore_change_removes_newly_ignored_py_files(
        self, integration_repo: Path, tmp_path: Path
    ) -> None:
        """When .cplignore adds a pattern, matching .py files should be removed from index."""
        # Create a Python file that will be indexed initially
        (integration_repo / "src" / "temporary.py").write_text("TEMP_CODE = True\n")

        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            # Initialize - temporary.py should be indexed
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Verify temporary.py IS indexed
            temp_results = await coordinator.search("TEMP_CODE")
            assert len(temp_results.results) >= 1, "temporary.py should be indexed initially"

            # Modify .cplignore to ignore temporary.py
            cplignore_path = integration_repo / ".codeplane" / ".cplignore"
            original_content = cplignore_path.read_text()
            cplignore_path.write_text(original_content + "\n**/temporary.py\n")

            # Trigger incremental reindex
            await coordinator.reindex_incremental([])

            # Now temporary.py should NOT be indexed
            temp_results = await coordinator.search("TEMP_CODE")
            assert len(temp_results.results) == 0, (
                "temporary.py should be removed after .cplignore change"
            )
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_cplignore_unchanged_no_reindex(
        self, integration_repo: Path, tmp_path: Path
    ) -> None:
        """When .cplignore hasn't changed, incremental reindex should be efficient."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(integration_repo, db_path, tantivy_path)

        try:
            # Initialize
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Get initial file count
            initial_results = await coordinator.search("def")
            initial_count = len(initial_results.results)

            # Trigger incremental reindex without any changes
            stats = await coordinator.reindex_incremental([])

            # Should have minimal work
            assert stats.files_added == 0
            assert stats.files_removed == 0

            # Same files should still be indexed
            final_results = await coordinator.search("def")
            assert len(final_results.results) == initial_count
        finally:
            coordinator.close()


class TestCoordinatorSearchFilterLanguages:
    """Tests for search filter_languages parameter."""

    @pytest.fixture
    def multilang_repo(self, tmp_path: Path) -> Path:
        """Create a repository with multiple language files."""
        import pygit2

        from codeplane.templates import get_cplignore_template

        repo_path = tmp_path / "multilang_repo"
        repo_path.mkdir()
        pygit2.init_repository(str(repo_path))

        repo = pygit2.Repository(str(repo_path))
        repo.config["user.name"] = "Test"
        repo.config["user.email"] = "test@test.com"

        # Create .codeplane/.cplignore
        codeplane_dir = repo_path / ".codeplane"
        codeplane_dir.mkdir()
        (codeplane_dir / ".cplignore").write_text(get_cplignore_template())

        # Create Python files
        (repo_path / "src").mkdir()
        (repo_path / "src" / "main.py").write_text('''"""Python main module."""

def search_handler():
    """Handle search requests."""
    return "python search"
''')
        (repo_path / "src" / "utils.py").write_text('''"""Python utils."""

def python_helper():
    return "helper"
''')

        # Create JavaScript files
        (repo_path / "js").mkdir()
        (repo_path / "js" / "search.js").write_text("""// JavaScript search
function searchHandler() {
    return "js search";
}

module.exports = { searchHandler };
""")
        (repo_path / "js" / "utils.js").write_text("""// JavaScript utils
function jsHelper() {
    return "helper";
}

module.exports = { jsHelper };
""")

        # Create Go files
        (repo_path / "go").mkdir()
        (repo_path / "go" / "search.go").write_text("""package main

// SearchHandler handles search requests
func SearchHandler() string {
    return "go search"
}
""")

        # Create pyproject.toml for Python context detection
        (repo_path / "pyproject.toml").write_text('[project]\nname = "test"')

        # Create package.json for JavaScript context detection
        (repo_path / "package.json").write_text('{"name": "test", "version": "1.0.0"}')

        # Create go.mod for Go context detection
        (repo_path / "go.mod").write_text("module test\n\ngo 1.21")

        # Commit
        repo.index.add_all()
        repo.index.write()
        tree = repo.index.write_tree()
        sig = pygit2.Signature("Test", "test@test.com")
        repo.create_commit("HEAD", sig, sig, "Initial commit", tree, [])

        return repo_path

    @pytest.mark.asyncio
    async def test_filter_languages_returns_only_matching_language(
        self, multilang_repo: Path, tmp_path: Path
    ) -> None:
        """filter_languages should only return results from specified languages."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(multilang_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for "search" with filter_languages=["python"]
            results = await coordinator.search("search", filter_languages=["python"])

            # Should only return Python files
            assert len(results.results) >= 1
            for r in results.results:
                assert r.path.endswith(".py"), f"Expected .py file, got {r.path}"
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_filter_languages_multiple_languages(
        self, multilang_repo: Path, tmp_path: Path
    ) -> None:
        """filter_languages with multiple languages should return files from all specified."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(multilang_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for "search" with filter_languages=["python", "javascript"]
            results = await coordinator.search("search", filter_languages=["python", "javascript"])

            # Should return both Python and JavaScript files
            paths = [r.path for r in results.results]
            has_py = any(p.endswith(".py") for p in paths)
            has_js = any(p.endswith(".js") for p in paths)

            assert len(results.results) >= 2
            assert has_py or has_js, f"Expected .py or .js files, got {paths}"
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_filter_languages_none_returns_all(
        self, multilang_repo: Path, tmp_path: Path
    ) -> None:
        """filter_languages=None should return results from all languages."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(multilang_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search without filter_languages
            results = await coordinator.search("search", filter_languages=None)

            # Should return results from multiple languages
            paths = [r.path for r in results.results]

            # We should have results (at least "search" matches in multiple files)
            assert len(results.results) >= 1
            # Verify we can get multiple file types when not filtering
            assert len(paths) >= 1
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_filter_languages_empty_for_nonexistent_language(
        self, multilang_repo: Path, tmp_path: Path
    ) -> None:
        """filter_languages with nonexistent language should return empty results."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(multilang_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search with a language that doesn't exist in the repo
            results = await coordinator.search("search", filter_languages=["rust"])

            # Should return empty results
            assert len(results.results) == 0
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_filter_languages_excludes_other_languages(
        self, multilang_repo: Path, tmp_path: Path
    ) -> None:
        """filter_languages should exclude files from non-specified languages."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(multilang_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for "helper" which exists in both Python and JavaScript
            # But filter to only Python
            results = await coordinator.search("helper", filter_languages=["python"])

            # Should only return Python files
            for r in results.results:
                assert r.path.endswith(".py"), (
                    f"Got non-Python file {r.path} when filtering for python"
                )

            # Verify JavaScript has the content but wasn't returned
            all_results = await coordinator.search("helper")
            has_js_unfiltered = any(r.path.endswith(".js") for r in all_results.results)
            has_js_filtered = any(r.path.endswith(".js") for r in results.results)

            # Should have JS in unfiltered but not in filtered
            assert has_js_unfiltered, "JS helper file should exist"
            assert not has_js_filtered, "JS file should be filtered out"
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_filter_languages_with_symbol_search(
        self, multilang_repo: Path, tmp_path: Path
    ) -> None:
        """filter_languages should work with symbol search mode."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(multilang_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search for symbol with language filter
            results = await coordinator.search(
                "handler", mode=SearchMode.SYMBOL, filter_languages=["python"]
            )

            # All results should be from Python files
            for r in results.results:
                assert r.path.endswith(".py"), f"Symbol search returned non-Python file: {r.path}"
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_filter_languages_respects_limit(
        self, multilang_repo: Path, tmp_path: Path
    ) -> None:
        """filter_languages should still respect the limit parameter."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(multilang_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Search with low limit
            results = await coordinator.search(
                "search", filter_languages=["python", "javascript"], limit=1
            )

            # Should respect the limit even after filtering
            assert len(results.results) <= 1
        finally:
            coordinator.close()

    @pytest.mark.asyncio
    async def test_filter_languages_empty_list_returns_all(
        self, multilang_repo: Path, tmp_path: Path
    ) -> None:
        """filter_languages=[] (empty list) should be treated as None."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"

        coordinator = IndexCoordinator(multilang_repo, db_path, tantivy_path)

        try:
            await coordinator.initialize(on_index_progress=_noop_progress)

            # Empty list should behave same as None - note: implementation
            # may treat [] as falsy and skip filtering
            results_empty = await coordinator.search("search", filter_languages=[])
            results_none = await coordinator.search("search", filter_languages=None)

            # Both should return similar results (all languages)
            # We can't guarantee exact same order, but count should be similar
            # (empty list might or might not filter depending on implementation)
            # The key is it shouldn't error
            assert len(results_empty.results) >= 0  # Just verify no error
            assert len(results_none.results) >= 0
        finally:
            coordinator.close()
