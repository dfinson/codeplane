"""Tests for scope_resolver module.

Tests the scope resolution utilities used by search context:
- find_enclosing_scope: Find structural scope for a line
- resolve_scope_region: Get scope region with content
- resolve_scope_region_for_path: Convenience wrapper by path
- ScopeRegion: Dataclass for resolved regions
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from codeplane.index._internal.indexing.scope_resolver import (
    ScopeRegion,
    find_enclosing_scope,
    resolve_scope_region,
    resolve_scope_region_for_path,
)
from codeplane.index.models import Context, File, ScopeFact, ScopeKind

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database


class TestScopeRegion:
    """Tests for ScopeRegion dataclass."""

    def test_structural_scope(self) -> None:
        """Structural scope with resolved=True."""
        region = ScopeRegion(
            start_line=10,
            end_line=20,
            kind="function",
            resolved=True,
        )
        assert region.start_line == 10
        assert region.end_line == 20
        assert region.kind == "function"
        assert region.resolved is True

    def test_fallback_region(self) -> None:
        """Fallback region with resolved=False."""
        region = ScopeRegion(
            start_line=5,
            end_line=15,
            kind="lines",
            resolved=False,
        )
        assert region.kind == "lines"
        assert region.resolved is False

    def test_class_scope(self) -> None:
        """Class scope region."""
        region = ScopeRegion(
            start_line=1,
            end_line=100,
            kind="class",
            resolved=True,
        )
        assert region.kind == "class"


class TestFindEnclosingScope:
    """Tests for find_enclosing_scope function."""

    @pytest.fixture
    def setup_scopes(self, temp_db: Database, temp_repo: Path) -> tuple[int, Database]:
        """Create test file with nested scopes."""
        with temp_db.session() as session:
            # Create context
            context = Context(
                name="test",
                language_family="python",
                root_path=str(temp_repo),
            )
            session.add(context)
            session.commit()
            session.refresh(context)
            context_id = context.id

            # Create file
            file = File(
                path="src/example.py",
                language_family="python",
                line_count=100,
            )
            session.add(file)
            session.commit()
            session.refresh(file)
            file_id = file.id
            assert file_id is not None  # type guard after commit

            # Create nested scopes:
            # - File scope (1-100)
            #   - Class scope (5-50)
            #     - Function scope (10-20)
            #   - Function scope (60-80)
            #     - Block scope (65-75)
            scopes = [
                ScopeFact(
                    file_id=file_id,
                    unit_id=context_id,
                    kind=ScopeKind.FILE.value,
                    start_line=1,
                    start_col=0,
                    end_line=100,
                    end_col=0,
                ),
                ScopeFact(
                    file_id=file_id,
                    unit_id=context_id,
                    kind=ScopeKind.CLASS.value,
                    start_line=5,
                    start_col=0,
                    end_line=50,
                    end_col=0,
                ),
                ScopeFact(
                    file_id=file_id,
                    unit_id=context_id,
                    kind=ScopeKind.FUNCTION.value,
                    start_line=10,
                    start_col=4,
                    end_line=20,
                    end_col=0,
                    parent_scope_id=None,
                ),
                ScopeFact(
                    file_id=file_id,
                    unit_id=context_id,
                    kind=ScopeKind.FUNCTION.value,
                    start_line=60,
                    start_col=0,
                    end_line=80,
                    end_col=0,
                ),
                ScopeFact(
                    file_id=file_id,
                    unit_id=context_id,
                    kind=ScopeKind.BLOCK.value,
                    start_line=65,
                    start_col=4,
                    end_line=75,
                    end_col=0,
                ),
            ]
            for scope in scopes:
                session.add(scope)
            session.commit()

        return file_id, temp_db

    def test_finds_function_scope(self, setup_scopes: tuple[int, Database]) -> None:
        """Finds enclosing function scope."""
        file_id, db = setup_scopes
        with db.session() as session:
            scope = find_enclosing_scope(session, file_id, line=15, preference="function")
            assert scope is not None
            assert scope.kind == ScopeKind.FUNCTION.value
            assert scope.start_line == 10
            assert scope.end_line == 20

    def test_finds_class_scope(self, setup_scopes: tuple[int, Database]) -> None:
        """Finds enclosing class scope when preferred."""
        file_id, db = setup_scopes
        with db.session() as session:
            scope = find_enclosing_scope(session, file_id, line=15, preference="class")
            assert scope is not None
            assert scope.kind == ScopeKind.CLASS.value
            assert scope.start_line == 5
            assert scope.end_line == 50

    def test_finds_block_scope(self, setup_scopes: tuple[int, Database]) -> None:
        """Finds enclosing block scope when preferred."""
        file_id, db = setup_scopes
        with db.session() as session:
            scope = find_enclosing_scope(session, file_id, line=70, preference="block")
            assert scope is not None
            assert scope.kind == ScopeKind.BLOCK.value
            assert scope.start_line == 65
            assert scope.end_line == 75

    def test_falls_back_to_smallest_non_file_scope(
        self, setup_scopes: tuple[int, Database]
    ) -> None:
        """Falls back to smallest non-file scope when preferred not found."""
        file_id, db = setup_scopes
        with db.session() as session:
            # Line 30 is inside class but not function - prefer block but none exists
            scope = find_enclosing_scope(session, file_id, line=30, preference="block")
            assert scope is not None
            # Should get class scope (smallest non-file)
            assert scope.kind == ScopeKind.CLASS.value

    def test_returns_file_scope_as_last_resort(self, setup_scopes: tuple[int, Database]) -> None:
        """Returns file scope when only file scope contains line."""
        file_id, db = setup_scopes
        with db.session() as session:
            # Line 90 is only in file scope
            scope = find_enclosing_scope(session, file_id, line=90, preference="function")
            assert scope is not None
            assert scope.kind == ScopeKind.FILE.value

    def test_returns_none_for_invalid_file(self, temp_db: Database) -> None:
        """Returns None for non-existent file."""
        with temp_db.session() as session:
            scope = find_enclosing_scope(session, file_id=99999, line=10)
            assert scope is None

    def test_prefers_lambda_for_function_preference(
        self, temp_db: Database, temp_repo: Path
    ) -> None:
        """Lambda scopes count as function preference."""
        with temp_db.session() as session:
            # Create minimal setup with lambda scope
            context = Context(
                name="test",
                language_family="python",
                root_path=str(temp_repo),
            )
            session.add(context)
            session.commit()
            session.refresh(context)

            file = File(path="lambda.py", language_family="python", line_count=10)
            session.add(file)
            session.commit()
            session.refresh(file)

            lambda_scope = ScopeFact(
                file_id=file.id,
                unit_id=context.id,
                kind=ScopeKind.LAMBDA.value,
                start_line=1,
                start_col=0,
                end_line=5,
                end_col=0,
            )
            session.add(lambda_scope)
            session.commit()

            assert file.id is not None  # type guard
            scope = find_enclosing_scope(session, file.id, line=3, preference="function")
            assert scope is not None
            assert scope.kind == ScopeKind.LAMBDA.value


class TestResolveScopeRegion:
    """Tests for resolve_scope_region function."""

    @pytest.fixture
    def setup_file_with_scope(
        self, temp_db: Database, temp_repo: Path
    ) -> tuple[int, Path, Database]:
        """Create file with content and scopes."""
        # Create actual file
        src_dir = temp_repo / "src"
        src_dir.mkdir(exist_ok=True)
        file_path = src_dir / "resolver_test.py"
        content = '"""Module docstring."""\n\ndef hello():\n    """Say hello."""\n    print("Hello")\n    return True\n\n\ndef world():\n    """Say world."""\n    print("World")\n    return False\n'
        file_path.write_text(content)

        with temp_db.session() as session:
            context = Context(
                name="test",
                language_family="python",
                root_path=str(temp_repo),
            )
            session.add(context)
            session.commit()
            session.refresh(context)

            file = File(
                path="src/resolver_test.py",
                language_family="python",
                line_count=12,
            )
            session.add(file)
            session.commit()
            session.refresh(file)
            file_id = file.id
            assert file_id is not None  # type guard after commit

            # Add function scopes
            scope1 = ScopeFact(
                file_id=file_id,
                unit_id=context.id,
                kind=ScopeKind.FUNCTION.value,
                start_line=3,
                start_col=0,
                end_line=6,
                end_col=0,
            )
            scope2 = ScopeFact(
                file_id=file_id,
                unit_id=context.id,
                kind=ScopeKind.FUNCTION.value,
                start_line=9,
                start_col=0,
                end_line=12,
                end_col=0,
            )
            session.add(scope1)
            session.add(scope2)
            session.commit()

        return file_id, temp_repo, temp_db

    def test_resolves_structural_scope(
        self, setup_file_with_scope: tuple[int, Path, Database]
    ) -> None:
        """Returns structural scope region with content."""
        file_id, repo_root, db = setup_file_with_scope
        with db.session() as session:
            region, content = resolve_scope_region(
                session, repo_root, file_id, line=4, preference="function"
            )
            assert region.resolved is True
            assert region.kind == ScopeKind.FUNCTION.value
            assert region.start_line == 3
            assert region.end_line == 6
            assert "def hello()" in content
            assert "Hello" in content

    def test_falls_back_to_lines(self, setup_file_with_scope: tuple[int, Path, Database]) -> None:
        """Falls back to line-based context when no matching scope."""
        file_id, repo_root, db = setup_file_with_scope
        with db.session() as session:
            # Line 1 is outside any function scope
            region, content = resolve_scope_region(
                session, repo_root, file_id, line=1, preference="function", fallback_lines=3
            )
            assert region.resolved is False
            assert region.kind == "lines"
            assert region.start_line == 1  # max(1, 1-3) = 1
            assert content  # Should have some content

    def test_handles_missing_file_id(self, temp_db: Database, temp_repo: Path) -> None:
        """Returns empty region for missing file."""
        with temp_db.session() as session:
            region, content = resolve_scope_region(session, temp_repo, file_id=99999, line=1)
            assert region.resolved is False
            assert content == ""

    def test_handles_unreadable_file(self, temp_db: Database, temp_repo: Path) -> None:
        """Returns empty region for unreadable file."""
        with temp_db.session() as session:
            context = Context(
                name="test",
                language_family="python",
                root_path=str(temp_repo),
            )
            session.add(context)
            session.commit()

            # File record exists but file doesn't
            file = File(
                path="nonexistent/file.py",
                language_family="python",
                line_count=10,
            )
            session.add(file)
            session.commit()
            session.refresh(file)

            assert file.id is not None  # type guard
            region, content = resolve_scope_region(session, temp_repo, file.id, line=5)
            assert region.resolved is False
            assert content == ""


class TestResolveScopeRegionForPath:
    """Tests for resolve_scope_region_for_path function."""

    @pytest.fixture
    def setup_indexed_file(self, temp_db: Database, temp_repo: Path) -> tuple[str, Path, Database]:
        """Create indexed file with scopes."""
        file_path = temp_repo / "indexed.py"
        content = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        file_path.write_text(content)

        with temp_db.session() as session:
            context = Context(
                name="test",
                language_family="python",
                root_path=str(temp_repo),
            )
            session.add(context)
            session.commit()
            session.refresh(context)

            file = File(
                path="indexed.py",
                language_family="python",
                line_count=5,
            )
            session.add(file)
            session.commit()
            session.refresh(file)

            scope = ScopeFact(
                file_id=file.id,
                unit_id=context.id,
                kind=ScopeKind.FUNCTION.value,
                start_line=1,
                start_col=0,
                end_line=2,
                end_col=0,
            )
            session.add(scope)
            session.commit()

        return "indexed.py", temp_repo, temp_db

    def test_resolves_indexed_file(self, setup_indexed_file: tuple[str, Path, Database]) -> None:
        """Resolves scope for indexed file."""
        file_path, repo_root, db = setup_indexed_file
        with db.session() as session:
            region, content = resolve_scope_region_for_path(
                session, repo_root, file_path, line=1, preference="function"
            )
            assert region.resolved is True
            assert region.kind == ScopeKind.FUNCTION.value
            assert "def foo()" in content

    def test_falls_back_for_unindexed_file(self, temp_db: Database, temp_repo: Path) -> None:
        """Falls back to line-based for unindexed file."""
        # Create file but don't index it
        file_path = temp_repo / "unindexed.py"
        content = "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\n"
        file_path.write_text(content)

        with temp_db.session() as session:
            region, content_str = resolve_scope_region_for_path(
                session, temp_repo, "unindexed.py", line=5, fallback_lines=2
            )
            assert region.resolved is False
            assert region.kind == "lines"
            assert region.start_line == 3  # max(1, 5-2)
            assert region.end_line == 7  # min(10, 5+2)

    def test_handles_nonexistent_file(self, temp_db: Database, temp_repo: Path) -> None:
        """Returns empty for nonexistent file."""
        with temp_db.session() as session:
            region, content = resolve_scope_region_for_path(
                session, temp_repo, "does/not/exist.py", line=1
            )
            assert region.resolved is False
            assert content == ""

    def test_fallback_clamps_to_file_bounds(self, temp_db: Database, temp_repo: Path) -> None:
        """Fallback respects file boundaries."""
        file_path = temp_repo / "short.py"
        file_path.write_text("a\nb\nc\n")  # 3 lines

        with temp_db.session() as session:
            region, content = resolve_scope_region_for_path(
                session, temp_repo, "short.py", line=2, fallback_lines=10
            )
            assert region.start_line == 1  # max(1, 2-10)
            assert region.end_line == 4  # min(4, 2+10) - trailing newline creates 4 elements
