"""Tests for map_repo tool."""

from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from codeplane.index.models import (
    Context,
    DefFact,
    ExportEntry,
    ExportSurface,
    File,
    ImportFact,
    ProbeStatus,
    SQLModel,
)
from codeplane.tools.map_repo import RepoMapper


@pytest.fixture
def db_session() -> Session:
    """Create an in-memory database session with schema."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


class TestRepoMapper:
    """Tests for RepoMapper."""

    def test_given_indexed_files_when_map_structure_then_returns_tree(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Map structure returns directory tree from index with line counts."""
        # Given - indexed files in DB
        ctx = Context(
            language_family="python",
            root_path="src",
            probe_status=ProbeStatus.VALID.value,
        )
        db_session.add(ctx)
        db_session.flush()

        files = [
            File(path="src/main.py", language_family="python", line_count=100),
            File(path="src/utils.py", language_family="python", line_count=50),
            File(path="tests/test_main.py", language_family="python", line_count=75),
        ]
        for f in files:
            db_session.add(f)
        db_session.commit()

        mapper = RepoMapper(db_session, tmp_path)

        # When
        result = mapper.map(include=["structure"])

        # Then
        assert result.structure is not None
        assert result.structure.file_count == 3
        assert "src" in result.structure.contexts

        # Verify line_count is present on file nodes
        src_dir = next((n for n in result.structure.tree if n.name == "src"), None)
        assert src_dir is not None
        main_file = next((n for n in src_dir.children if n.name == "main.py"), None)
        assert main_file is not None
        assert main_file.line_count == 100

    def test_given_indexed_files_when_map_languages_then_groups_by_family(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Map languages groups by language_family."""
        # Given
        files = [
            File(path="app.py", language_family="python"),
            File(path="util.py", language_family="python"),
            File(path="index.js", language_family="javascript"),
        ]
        for f in files:
            db_session.add(f)
        db_session.commit()

        mapper = RepoMapper(db_session, tmp_path)

        # When
        result = mapper.map(include=["languages"])

        # Then
        assert result.languages is not None
        assert len(result.languages) == 2
        py_stats = next(s for s in result.languages if s.language == "python")
        assert py_stats.file_count == 2

    def test_given_import_facts_when_map_dependencies_then_extracts_modules(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Map dependencies extracts from ImportFact."""
        # Given
        ctx = Context(
            language_family="python",
            root_path=".",
            probe_status=ProbeStatus.VALID.value,
        )
        db_session.add(ctx)
        db_session.flush()

        f = File(path="app.py", language_family="python")
        db_session.add(f)
        db_session.flush()

        imports = [
            ImportFact(
                import_uid="imp1",
                file_id=f.id,
                unit_id=ctx.id,
                imported_name="click",
                source_literal="click",
                import_kind="python_import",
            ),
            ImportFact(
                import_uid="imp2",
                file_id=f.id,
                unit_id=ctx.id,
                imported_name="pydantic",
                source_literal="pydantic",
                import_kind="python_import",
            ),
            ImportFact(
                import_uid="imp3",
                file_id=f.id,
                unit_id=ctx.id,
                imported_name="utils",
                source_literal=".utils",  # Relative import
                import_kind="python_from",
            ),
        ]
        for imp in imports:
            db_session.add(imp)
        db_session.commit()

        mapper = RepoMapper(db_session, tmp_path)

        # When
        result = mapper.map(include=["dependencies"])

        # Then
        assert result.dependencies is not None
        assert "click" in result.dependencies.external_modules
        assert "pydantic" in result.dependencies.external_modules
        # Relative imports excluded
        assert ".utils" not in result.dependencies.external_modules

    def test_given_test_files_when_map_test_layout_then_finds_tests(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Map test_layout finds test files by path pattern."""
        # Given
        files = [
            File(path="tests/test_foo.py", language_family="python"),
            File(path="tests/test_bar.py", language_family="python"),
            File(path="src/main.py", language_family="python"),
        ]
        for f in files:
            db_session.add(f)
        db_session.commit()

        mapper = RepoMapper(db_session, tmp_path)

        # When
        result = mapper.map(include=["test_layout"])

        # Then
        assert result.test_layout is not None
        assert result.test_layout.test_count == 2
        assert "tests/test_foo.py" in result.test_layout.test_files

    def test_given_def_facts_when_map_entry_points_then_finds_main(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Map entry_points finds main/app definitions."""
        # Given
        ctx = Context(
            language_family="python",
            root_path=".",
            probe_status=ProbeStatus.VALID.value,
        )
        db_session.add(ctx)
        db_session.flush()

        f = File(path="app.py", language_family="python")
        db_session.add(f)
        db_session.flush()

        defs = [
            DefFact(
                def_uid="def1",
                file_id=f.id,
                unit_id=ctx.id,
                kind="function",
                name="main",
                lexical_path="app.main",
                start_line=1,
                start_col=0,
                end_line=5,
                end_col=0,
            ),
            DefFact(
                def_uid="def2",
                file_id=f.id,
                unit_id=ctx.id,
                kind="function",
                name="helper",  # Not an entry point name
                lexical_path="app.helper",
                start_line=10,
                start_col=0,
                end_line=15,
                end_col=0,
            ),
        ]
        for d in defs:
            db_session.add(d)
        db_session.commit()

        mapper = RepoMapper(db_session, tmp_path)

        # When
        result = mapper.map(include=["entry_points"])

        # Then
        assert result.entry_points is not None
        names = [ep.name for ep in result.entry_points]
        assert "main" in names
        assert "helper" not in names

    def test_given_export_entries_when_map_public_api_then_extracts_symbols(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """Map public_api extracts from ExportEntry."""
        # Given
        ctx = Context(
            language_family="python",
            root_path=".",
            probe_status=ProbeStatus.VALID.value,
        )
        db_session.add(ctx)
        db_session.flush()

        surface = ExportSurface(unit_id=ctx.id)
        db_session.add(surface)
        db_session.flush()

        entries = [
            ExportEntry(
                surface_id=surface.surface_id,
                exported_name="MyClass",
                def_uid="def1",
                certainty="certain",
                evidence_kind="__all__literal",
            ),
            ExportEntry(
                surface_id=surface.surface_id,
                exported_name="helper_func",
                def_uid="def2",
                certainty="certain",
                evidence_kind="__all__literal",
            ),
        ]
        for e in entries:
            db_session.add(e)
        db_session.commit()

        mapper = RepoMapper(db_session, tmp_path)

        # When
        result = mapper.map(include=["public_api"])

        # Then
        assert result.public_api is not None
        assert len(result.public_api) == 2
        names = [s.name for s in result.public_api]
        assert "MyClass" in names
        assert "helper_func" in names
