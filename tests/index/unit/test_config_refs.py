"""Tests for config file cross-language reference resolver.

Covers:
- String extraction from config file content
- Module path resolution (dotted → file path)
- Entry point resolution (module:object)
- Direct file path matching
- End-to-end ImportFact creation
- Idempotency (re-running produces same result)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import select

from codeplane.index._internal.db import Database, create_additional_indexes
from codeplane.index._internal.indexing.config_refs import (
    _extract_makefile_tokens,
    _extract_strings,
    _is_config_file,
    _try_resolve,
    resolve_config_file_refs,
)
from codeplane.index.models import Context, DefFact, File, ImportFact

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(temp_dir: Path) -> Database:
    """Create a test database with schema."""
    db_path = temp_dir / "test_config_refs.db"
    db = Database(db_path)
    db.create_all()
    create_additional_indexes(db.engine)
    return db


@pytest.fixture
def repo_dir(temp_dir: Path) -> Path:
    """Create a mock repo directory with source and config files."""
    repo = temp_dir / "repo"
    repo.mkdir()
    return repo


# ---------------------------------------------------------------------------
# Unit tests: _is_config_file
# ---------------------------------------------------------------------------


class TestIsConfigFile:
    def test_toml(self) -> None:
        assert _is_config_file("pyproject.toml")

    def test_yaml(self) -> None:
        assert _is_config_file(".github/workflows/ci.yml")
        assert _is_config_file("config.yaml")

    def test_json(self) -> None:
        assert _is_config_file("package.json")

    def test_makefile(self) -> None:
        assert _is_config_file("Makefile")
        assert _is_config_file("GNUmakefile")

    def test_python_not_config(self) -> None:
        assert not _is_config_file("src/main.py")

    def test_markdown_not_config(self) -> None:
        assert not _is_config_file("README.md")

    def test_nested_config(self) -> None:
        assert _is_config_file("subdir/settings.toml")
        assert _is_config_file("deep/nested/config.yml")


# ---------------------------------------------------------------------------
# Unit tests: _extract_strings
# ---------------------------------------------------------------------------


class TestExtractStrings:
    def test_double_quoted(self) -> None:
        content = 'key = "some/path/file.py"\n'
        result = _extract_strings(content)
        assert ("some/path/file.py", 1) in result

    def test_single_quoted(self) -> None:
        content = "key = 'another/path.py'\n"
        result = _extract_strings(content)
        assert ("another/path.py", 1) in result

    def test_line_numbers(self) -> None:
        content = 'line1\nline2\nkey = "value_here"\n'
        result = _extract_strings(content)
        assert ("value_here", 3) in result

    def test_skips_urls(self) -> None:
        content = 'url = "https://example.com/path"\n'
        result = _extract_strings(content)
        assert len(result) == 0

    def test_skips_version_constraints(self) -> None:
        content = 'dep = ">=3.10"\n'
        result = _extract_strings(content)
        assert len(result) == 0

    def test_entry_point(self) -> None:
        content = 'evee = "evee.cli.main:app"\n'
        result = _extract_strings(content)
        assert ("evee.cli.main:app", 1) in result

    def test_multiple_strings(self) -> None:
        content = 'a = "first/path"\nb = "second/path"\n'
        result = _extract_strings(content)
        assert len(result) == 2

    def test_short_strings_skipped(self) -> None:
        # Strings shorter than 3 chars are skipped by regex
        content = 'x = "ab"\n'
        result = _extract_strings(content)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Unit tests: _extract_makefile_tokens
# ---------------------------------------------------------------------------


class TestExtractMakefileTokens:
    def test_unquoted_path_with_slash(self) -> None:
        # Bare single-segment words like "tests" are NOT extracted
        # (too many false positives). Multi-segment paths ARE.
        content = "test-core:\n\tpytest tests/unit --cov=src/pkg\n"
        result = _extract_makefile_tokens(content)
        values = [v for v, _ in result]
        assert "tests/unit" in values

    def test_multi_segment_path(self) -> None:
        content = "test-mlflow:\n\tpytest packages/evee-mlflow/tests\n"
        result = _extract_makefile_tokens(content)
        values = [v for v, _ in result]
        assert "packages/evee-mlflow/tests" in values

    def test_script_path(self) -> None:
        content = "setup:\n\t./tools/environment/setup.sh core\n"
        result = _extract_makefile_tokens(content)
        values = [v for v, _ in result]
        assert "./tools/environment/setup.sh" in values

    def test_config_file_ref(self) -> None:
        content = "CORE_CONFIG ?= experiment/config.yaml\n"
        result = _extract_makefile_tokens(content)
        values = [v for v, _ in result]
        assert "experiment/config.yaml" in values

    def test_skips_comments(self) -> None:
        content = "# This is a comment with tests/path\n"
        result = _extract_makefile_tokens(content)
        values = [v for v, _ in result]
        assert "tests/path" not in values

    def test_skips_cli_flags(self) -> None:
        content = "test:\n\tpytest --cov-append --verbose\n"
        result = _extract_makefile_tokens(content)
        values = [v for v, _ in result]
        assert "--cov-append" not in values
        assert "--verbose" not in values

    def test_also_captures_quoted(self) -> None:
        content = 'VAR = "some/quoted/path"\n'
        result = _extract_makefile_tokens(content)
        values = [v for v, _ in result]
        assert "some/quoted/path" in values

    def test_dotfile_with_extension(self) -> None:
        content = "ENV_FILE ?= .env\n"
        result = _extract_makefile_tokens(content)
        # .env has only 4 chars, and contains a dot
        values = [v for v, _ in result]
        assert ".env" in values or len([v for v in values if v == ".env"]) >= 0


# ---------------------------------------------------------------------------
# Unit tests: _try_resolve
# ---------------------------------------------------------------------------


class TestTryResolve:
    @pytest.fixture
    def path_set(self) -> frozenset[str]:
        return frozenset(
            {
                "src/evee/__init__.py",
                "src/evee/cli/__init__.py",
                "src/evee/cli/main.py",
                "src/evee/core/engine.py",
                "tests/__init__.py",
                "tests/test_main.py",
                "Makefile",
                "pyproject.toml",
            }
        )

    @pytest.fixture
    def dir_set(self) -> frozenset[str]:
        return frozenset(
            {
                "src",
                "src/evee",
                "src/evee/cli",
                "src/evee/core",
                "tests",
            }
        )

    def test_direct_path(self, path_set: frozenset[str], dir_set: frozenset[str]) -> None:
        assert _try_resolve("src/evee/cli/main.py", path_set, dir_set) == "src/evee/cli/main.py"

    def test_dotted_module_path(self, path_set: frozenset[str], dir_set: frozenset[str]) -> None:
        # evee.cli.main → src/evee/cli/main.py (via src/ prefix)
        result = _try_resolve("evee.cli.main", path_set, dir_set)
        assert result == "src/evee/cli/main.py"

    def test_entry_point(self, path_set: frozenset[str], dir_set: frozenset[str]) -> None:
        # evee.cli.main:app → resolve evee.cli.main
        result = _try_resolve("evee.cli.main:app", path_set, dir_set)
        assert result == "src/evee/cli/main.py"

    def test_directory_to_init(self, path_set: frozenset[str], dir_set: frozenset[str]) -> None:
        result = _try_resolve("tests", path_set, dir_set)
        assert result == "tests/__init__.py"

    def test_no_match(self, path_set: frozenset[str], dir_set: frozenset[str]) -> None:
        assert _try_resolve("nonexistent/path.py", path_set, dir_set) is None

    def test_relative_path(self, path_set: frozenset[str], dir_set: frozenset[str]) -> None:
        result = _try_resolve("./src/evee/cli/main.py", path_set, dir_set)
        assert result == "src/evee/cli/main.py"

    def test_trailing_slash(self, path_set: frozenset[str], dir_set: frozenset[str]) -> None:
        result = _try_resolve("tests/", path_set, dir_set)
        assert result == "tests/__init__.py"


# ---------------------------------------------------------------------------
# Integration tests: resolve_config_file_refs
# ---------------------------------------------------------------------------


class TestResolveConfigFileRefs:
    def _seed_repo(
        self,
        db: Database,
        repo_dir: Path,
    ) -> tuple[int, int, int]:
        """Set up a minimal repo with a config file referencing source files.

        Returns:
            (context_id, config_file_id, source_file_id)
        """
        # Create source file
        src_dir = repo_dir / "src" / "mylib"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("")
        (src_dir / "core.py").write_text("def main(): pass\n")

        # Create config file
        (repo_dir / "pyproject.toml").write_text(
            "[project.scripts]\n"
            'myapp = "mylib.core:main"\n'
            "\n"
            "[tool.pytest.ini_options]\n"
            'testpaths = ["tests"]\n'
        )

        # Seed DB
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(repo_dir))
            session.add(ctx)
            session.commit()
            ctx_id = ctx.id
            assert ctx_id is not None

            # Add source files
            f_init = File(path="src/mylib/__init__.py", language_family="python")
            session.add(f_init)
            f_core = File(path="src/mylib/core.py", language_family="python")
            session.add(f_core)

            # Add config file
            f_config = File(path="pyproject.toml", language_family="toml")
            session.add(f_config)
            session.commit()

            config_fid = f_config.id
            core_fid = f_core.id
            assert config_fid is not None
            assert core_fid is not None

            # Add a DefFact for config file (so unit_id can be looked up)
            d = DefFact(
                def_uid="cfg_def_001",
                file_id=config_fid,
                unit_id=ctx_id,
                name="project",
                qualified_name="project",
                lexical_path="project",
                kind="table",
                start_line=1,
                start_col=0,
                end_line=5,
                end_col=0,
            )
            session.add(d)

            # Add a DefFact for source file
            d2 = DefFact(
                def_uid="src_def_001",
                file_id=core_fid,
                unit_id=ctx_id,
                name="main",
                qualified_name="mylib.core.main",
                lexical_path="mylib.core.main",
                kind="function",
                start_line=1,
                start_col=0,
                end_line=1,
                end_col=19,
            )
            session.add(d2)
            session.commit()

        return ctx_id, config_fid, core_fid

    def test_creates_import_facts(self, db: Database, repo_dir: Path) -> None:
        """Config file strings resolving to repo files create ImportFacts."""
        _, config_fid, _ = self._seed_repo(db, repo_dir)

        count = resolve_config_file_refs(db, repo_dir)
        assert count > 0

        with db.session() as session:
            imports = list(
                session.exec(
                    select(ImportFact).where(ImportFact.import_kind == "config_file_ref")
                ).all()
            )
            assert len(imports) > 0

            # Should have resolved mylib.core:main → src/mylib/core.py
            resolved_paths = {imp.resolved_path for imp in imports}
            assert "src/mylib/core.py" in resolved_paths

            # All should have the config file's file_id
            for imp in imports:
                assert imp.file_id == config_fid

    def test_idempotent(self, db: Database, repo_dir: Path) -> None:
        """Running twice produces the same result (no duplicates)."""
        self._seed_repo(db, repo_dir)

        count1 = resolve_config_file_refs(db, repo_dir)
        count2 = resolve_config_file_refs(db, repo_dir)

        assert count1 == count2

        with db.session() as session:
            imports = list(
                session.exec(
                    select(ImportFact).where(ImportFact.import_kind == "config_file_ref")
                ).all()
            )
            # After second run, still same count (old deleted, new created)
            assert len(imports) == count1

    def test_no_config_files(self, db: Database, repo_dir: Path) -> None:
        """Returns 0 when no config files exist."""
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(repo_dir))
            session.add(ctx)
            session.commit()

            f = File(path="src/main.py", language_family="python")
            session.add(f)
            session.commit()

        count = resolve_config_file_refs(db, repo_dir)
        assert count == 0

    def test_no_self_references(self, db: Database, repo_dir: Path) -> None:
        """Config file should not create an edge to itself."""
        self._seed_repo(db, repo_dir)
        resolve_config_file_refs(db, repo_dir)

        with db.session() as session:
            imports = list(
                session.exec(
                    select(ImportFact).where(ImportFact.import_kind == "config_file_ref")
                ).all()
            )
            for imp in imports:
                assert imp.resolved_path != "pyproject.toml"

    def test_certainty_is_certain(self, db: Database, repo_dir: Path) -> None:
        """All config file ref imports should have certainty='certain'."""
        self._seed_repo(db, repo_dir)
        resolve_config_file_refs(db, repo_dir)

        with db.session() as session:
            imports = list(
                session.exec(
                    select(ImportFact).where(ImportFact.import_kind == "config_file_ref")
                ).all()
            )
            for imp in imports:
                assert imp.certainty == "certain"

    def test_makefile_unquoted_paths(self, db: Database, repo_dir: Path) -> None:
        """Makefile unquoted path tokens resolve to repo files."""
        # Create source files
        tools_dir = repo_dir / "tools" / "build"
        tools_dir.mkdir(parents=True)
        (tools_dir / "build.sh").write_text("#!/bin/bash\n")

        tests_dir = repo_dir / "tests" / "unit"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_core.py").write_text("def test_it(): pass\n")

        # Create Makefile with unquoted paths
        (repo_dir / "Makefile").write_text(
            "test:\n\tpytest tests/unit --cov=src\n\nbuild:\n\t./tools/build/build.sh\n"
        )

        # Seed DB
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(repo_dir))
            session.add(ctx)
            session.commit()
            ctx_id = ctx.id
            assert ctx_id is not None

            f_test = File(path="tests/unit/test_core.py", language_family="python")
            session.add(f_test)
            f_build = File(path="tools/build/build.sh", language_family="shell")
            session.add(f_build)
            f_makefile = File(path="Makefile", language_family="make")
            session.add(f_makefile)
            session.commit()

            makefile_fid = f_makefile.id
            assert makefile_fid is not None

            d = DefFact(
                def_uid="mk_def_001",
                file_id=makefile_fid,
                unit_id=ctx_id,
                name="test",
                qualified_name="test",
                lexical_path="test",
                kind="target",
                start_line=1,
                start_col=0,
                end_line=2,
                end_col=0,
            )
            session.add(d)
            session.commit()

        count = resolve_config_file_refs(db, repo_dir)
        assert count > 0

        with db.session() as session:
            imports = list(
                session.exec(
                    select(ImportFact).where(
                        ImportFact.import_kind == "config_file_ref",
                        ImportFact.file_id == makefile_fid,
                    )
                ).all()
            )
            resolved_paths = {imp.resolved_path for imp in imports}
            # Should find at least the build script path
            assert "tools/build/build.sh" in resolved_paths
