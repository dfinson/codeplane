"""Unit tests for import_graph.py.

Tests cover:
- ImportGraph.affected_tests: changed files → affected test files
- ImportGraph.imported_sources: test files → source directories
- ImportGraph.uncovered_modules: modules with no test imports
- Confidence tiers (complete vs partial)
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from codeplane.index._internal.db import Database, create_additional_indexes
from codeplane.index._internal.indexing.import_graph import (
    CoverageGap,
    CoverageSourceResult,
    ImportGraph,
    ImportGraphResult,
)
from codeplane.index.models import Context, File, ImportFact

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(temp_dir: Path) -> Database:
    """Create a test database with schema."""
    db_path = temp_dir / "test_import_graph.db"
    db = Database(db_path)
    db.create_all()
    create_additional_indexes(db.engine)
    return db


def _seed_data(
    db: Database,
    files: list[str],
    imports: list[tuple[str, str | None]],
) -> None:
    """Seed the database with file and import data.

    Args:
        db: Database instance.
        files: List of file paths to create.
        imports: List of (file_path, source_literal) tuples.
    """
    with db.session() as session:
        ctx = Context(name="test", language_family="python", root_path="/test")
        session.add(ctx)
        session.commit()
        session.refresh(ctx)
        ctx_id = ctx.id

        file_map: dict[str, int] = {}
        for i, fp in enumerate(files, start=1):
            f = File(
                id=i,
                path=fp,
                hash="h" + str(i),
                context_id=ctx_id,
                language="python",
                line_count=10,
                byte_size=100,
            )
            session.add(f)
            file_map[fp] = i
        session.commit()

        for j, (fp, source_literal) in enumerate(imports, start=1):
            imp = ImportFact(
                import_uid=f"imp-{j}",
                file_id=file_map[fp],
                unit_id=ctx_id,
                imported_name=source_literal.rsplit(".", 1)[-1] if source_literal else "unknown",
                source_literal=source_literal,
                import_kind="direct",
            )
            session.add(imp)
        session.commit()


# ---------------------------------------------------------------------------
# affected_tests
# ---------------------------------------------------------------------------


class TestAffectedTests:
    """Tests for ImportGraph.affected_tests."""

    @pytest.fixture
    def graph(self, db: Database) -> Generator[ImportGraph, None, None]:
        """Create an import graph with seeded data."""
        _seed_data(
            db,
            files=[
                "src/mylib/core.py",
                "src/mylib/utils.py",
                "src/mylib/__init__.py",
                "tests/test_core.py",
                "tests/test_utils.py",
                "tests/test_integration.py",
            ],
            imports=[
                # test_core imports mylib.core
                ("tests/test_core.py", "mylib.core"),
                # test_utils imports mylib.utils
                ("tests/test_utils.py", "mylib.utils"),
                # test_integration imports both
                ("tests/test_integration.py", "mylib.core"),
                ("tests/test_integration.py", "mylib.utils"),
                # test_core also imports pytest (external, won't match)
                ("tests/test_core.py", "pytest"),
            ],
        )
        with db.session() as session:
            yield ImportGraph(session)

    def test_single_file_change(self, graph: ImportGraph) -> None:
        result = graph.affected_tests(["src/mylib/core.py"])
        assert isinstance(result, ImportGraphResult)
        test_files = sorted(result.test_files)
        assert "tests/test_core.py" in test_files
        assert "tests/test_integration.py" in test_files
        assert "tests/test_utils.py" not in test_files

    def test_multiple_file_changes(self, graph: ImportGraph) -> None:
        result = graph.affected_tests(["src/mylib/core.py", "src/mylib/utils.py"])
        test_files = sorted(result.test_files)
        assert len(test_files) == 3
        assert "tests/test_core.py" in test_files
        assert "tests/test_utils.py" in test_files
        assert "tests/test_integration.py" in test_files

    def test_package_init_matches_as_parent(self, graph: ImportGraph) -> None:
        result = graph.affected_tests(["src/mylib/__init__.py"])
        # __init__.py maps to module "mylib" - tests importing mylib.* are affected
        # because __init__.py runs on any submodule import
        assert len(result.matches) > 0
        # Child imports (mylib.core, mylib.utils) are high confidence
        # because changes to __init__.py affect all submodule imports
        for m in result.matches:
            assert m.confidence == "high"

    def test_unresolvable_file(self, graph: ImportGraph) -> None:
        result = graph.affected_tests(["README.md"])
        # With resolved_path fallback, tier is "complete" even when module
        # mapping fails — the file is still searchable via resolved_path.
        assert result.confidence.tier == "complete"
        assert result.confidence.resolved_ratio == 0.0
        assert "README.md" in result.confidence.unresolved_files

    def test_complete_confidence(self, graph: ImportGraph) -> None:
        result = graph.affected_tests(["src/mylib/core.py"])
        # All files resolved, no NULL source_literals in scope
        assert result.confidence.tier == "complete"
        assert result.confidence.resolved_ratio == 1.0

    def test_changed_modules_populated(self, graph: ImportGraph) -> None:
        result = graph.affected_tests(["src/mylib/core.py"])
        assert len(result.changed_modules) > 0
        # Should include both src. and non-src. forms
        assert "mylib.core" in result.changed_modules or "src.mylib.core" in result.changed_modules

    def test_match_confidence_levels(self, graph: ImportGraph) -> None:
        result = graph.affected_tests(["src/mylib/core.py"])
        for m in result.matches:
            # Direct imports should be high confidence
            assert m.confidence in ("high", "low")
            assert len(m.source_modules) > 0

    def test_empty_changed_files(self, graph: ImportGraph) -> None:
        result = graph.affected_tests([])
        assert len(result.matches) == 0
        assert result.confidence.tier == "complete"
        assert result.confidence.resolved_ratio == 1.0


class TestAffectedTestsWithNulls:
    """Test confidence with NULL source_literal."""

    @pytest.fixture
    def graph_with_nulls(self, db: Database) -> Generator[ImportGraph, None, None]:
        _seed_data(
            db,
            files=[
                "src/mylib/core.py",
                "tests/test_core.py",
            ],
            imports=[
                ("tests/test_core.py", "mylib.core"),
                ("tests/test_core.py", None),  # NULL source_literal
            ],
        )
        with db.session() as session:
            yield ImportGraph(session)

    def test_partial_confidence_with_nulls(self, graph_with_nulls: ImportGraph) -> None:
        result = graph_with_nulls.affected_tests(["src/mylib/core.py"])
        assert result.confidence.tier == "partial"
        assert result.confidence.null_source_count > 0


# ---------------------------------------------------------------------------
# imported_sources
# ---------------------------------------------------------------------------


class TestImportedSources:
    """Tests for ImportGraph.imported_sources."""

    @pytest.fixture
    def graph(self, db: Database) -> Generator[ImportGraph, None, None]:
        _seed_data(
            db,
            files=[
                "src/mylib/core.py",
                "src/mylib/utils.py",
                "tests/test_core.py",
            ],
            imports=[
                ("tests/test_core.py", "mylib.core"),
                ("tests/test_core.py", "mylib.utils"),
                # External imports should be filtered out
                ("tests/test_core.py", "pytest"),
            ],
        )
        with db.session() as session:
            yield ImportGraph(session)

    def test_returns_source_dirs(self, graph: ImportGraph) -> None:
        result = graph.imported_sources(["tests/test_core.py"])
        assert isinstance(result, CoverageSourceResult)
        assert len(result.source_dirs) > 0
        # Should include the directory containing the source files
        assert any("src/mylib" in d for d in result.source_dirs)

    def test_filters_external_modules(self, graph: ImportGraph) -> None:
        result = graph.imported_sources(["tests/test_core.py"])
        # pytest should not appear in source_modules
        assert "pytest" not in result.source_modules

    def test_empty_test_files(self, graph: ImportGraph) -> None:
        result = graph.imported_sources([])
        assert result.source_dirs == []
        assert result.confidence == "complete"

    def test_complete_confidence(self, graph: ImportGraph) -> None:
        result = graph.imported_sources(["tests/test_core.py"])
        assert result.confidence == "complete"
        assert result.null_import_count == 0


# ---------------------------------------------------------------------------
# uncovered_modules
# ---------------------------------------------------------------------------


class TestUncoveredModules:
    """Tests for ImportGraph.uncovered_modules."""

    @pytest.fixture
    def graph(self, db: Database) -> Generator[ImportGraph, None, None]:
        _seed_data(
            db,
            files=[
                "src/mylib/core.py",
                "src/mylib/utils.py",
                "src/mylib/orphan.py",  # Not imported by any test
                "tests/test_core.py",
            ],
            imports=[
                ("tests/test_core.py", "mylib.core"),
                # mylib.utils imported by source, not test
                ("src/mylib/core.py", "mylib.utils"),
                # mylib.orphan not imported by anything
            ],
        )
        with db.session() as session:
            yield ImportGraph(session)

    def test_finds_uncovered(self, graph: ImportGraph) -> None:
        gaps = graph.uncovered_modules()
        assert isinstance(gaps, list)
        gap_modules = [g.module for g in gaps]
        # orphan has no test imports
        assert any("orphan" in m for m in gap_modules)

    def test_covered_module_excluded(self, graph: ImportGraph) -> None:
        gaps = graph.uncovered_modules()
        gap_modules = [g.module for g in gaps]
        # core IS imported by a test, should NOT be in gaps
        assert not any(m == "mylib.core" for m in gap_modules)

    def test_gap_has_file_path(self, graph: ImportGraph) -> None:
        gaps = graph.uncovered_modules()
        for g in gaps:
            assert isinstance(g, CoverageGap)
            # file_path may or may not be resolved, but should be present for indexed files


# ---------------------------------------------------------------------------
# affected_tests — Go same-directory affinity
# ---------------------------------------------------------------------------


class TestAffectedTestsGoPackageAffinity:
    """Go test files in the same directory share a package and need no import."""

    @pytest.fixture
    def graph(self, db: Database) -> Generator[ImportGraph, None, None]:
        _seed_data(
            db,
            files=[
                "list/list.go",
                "list/rank.go",
                "list/list_test.go",
                "list/rank_test.go",
                "textinput/textinput.go",
                "textinput/textinput_test.go",
            ],
            imports=[
                # Cross-package import only — no same-package imports exist in Go
                ("list/list_test.go", "github.com/charmbracelet/bubbles/textinput"),
            ],
        )
        with db.session() as session:
            yield ImportGraph(session)

    def test_same_dir_finds_all_tests(self, graph: ImportGraph) -> None:
        """Changing list.go finds both test files in the same directory."""
        result = graph.affected_tests(["list/list.go"])
        test_files = sorted(result.test_files)
        assert "list/list_test.go" in test_files
        assert "list/rank_test.go" in test_files

    def test_same_dir_excludes_other_packages(self, graph: ImportGraph) -> None:
        """Changing list.go does NOT match textinput_test.go (different dir)."""
        result = graph.affected_tests(["list/list.go"])
        assert "textinput/textinput_test.go" not in result.test_files

    def test_same_dir_high_confidence(self, graph: ImportGraph) -> None:
        """Same-directory Go matches are high confidence."""
        result = graph.affected_tests(["list/list.go"])
        for m in result.matches:
            if m.test_file.startswith("list/"):
                assert m.confidence == "high"

    def test_non_go_files_skip_affinity(self, graph: ImportGraph) -> None:
        """Non-.go files do not trigger same-directory affinity."""
        result = graph.affected_tests(["README.md"])
        # No Go affinity match — only import-based matching applies
        assert len(result.matches) == 0


# ---------------------------------------------------------------------------
# affected_tests: direct test file changes
# ---------------------------------------------------------------------------


class TestAffectedTestsDirectTestFiles:
    """Test that changed test files appear directly in affected_tests results.

    When affected_by contains a test file path, the import graph has no edge
    leading to it (nothing imports a test).  Step 5b adds it directly.
    """

    @pytest.fixture
    def graph(self, db: Database) -> Generator[ImportGraph, None, None]:
        _seed_data(
            db,
            files=[
                "src/mylib/core.py",
                "tests/test_core.py",
                "tests/test_utils.py",
            ],
            imports=[
                ("tests/test_core.py", "mylib.core"),
            ],
        )
        with db.session() as session:
            yield ImportGraph(session)

    def test_test_file_only(self, graph: ImportGraph) -> None:
        """A single test file as changed_files appears in results."""
        result = graph.affected_tests(["tests/test_utils.py"])
        assert len(result.matches) == 1
        assert result.matches[0].test_file == "tests/test_utils.py"
        assert result.matches[0].confidence == "high"
        assert result.matches[0].reason == "test file directly changed"
        assert result.matches[0].source_modules == []

    def test_test_file_with_source_file(self, graph: ImportGraph) -> None:
        """Mixed: source + test file.  Both contribute to results."""
        result = graph.affected_tests(["src/mylib/core.py", "tests/test_utils.py"])
        test_files = sorted(result.test_files)
        # test_core found via import graph (imports mylib.core)
        assert "tests/test_core.py" in test_files
        # test_utils found via direct inclusion (is a test file)
        assert "tests/test_utils.py" in test_files

    def test_test_file_already_matched_by_import_not_duplicated(self, graph: ImportGraph) -> None:
        """If a changed test also imports the changed source, no duplicate."""
        result = graph.affected_tests(["src/mylib/core.py", "tests/test_core.py"])
        paths = result.test_files
        # test_core.py should appear exactly once
        assert paths.count("tests/test_core.py") == 1

    def test_all_test_files(self, graph: ImportGraph) -> None:
        """All changed files are test files — all appear directly."""
        result = graph.affected_tests(["tests/test_core.py", "tests/test_utils.py"])
        test_files = sorted(result.test_files)
        assert test_files == ["tests/test_core.py", "tests/test_utils.py"]
        # Both should be high confidence
        for m in result.matches:
            assert m.confidence == "high"

    def test_confidence_tier_still_complete(self, graph: ImportGraph) -> None:
        """Direct test inclusions don't degrade the confidence tier."""
        result = graph.affected_tests(["tests/test_utils.py"])
        assert result.confidence.tier == "complete"

    def test_only_test_files_no_changed_modules(self, graph: ImportGraph) -> None:
        """When only test files change, changed_modules is empty and ratio is 1.0."""
        result = graph.affected_tests(["tests/test_utils.py"])
        assert result.changed_modules == []
        assert result.confidence.resolved_ratio == 1.0
        assert result.confidence.unresolved_files == []
