"""Integration tests for the full indexing pipeline.

These tests exercise the complete Tier 0 + Tier 1 indexing flow:
1. Create realistic Python project structures
2. Run discovery, probe, and indexing
3. Verify fact extraction (defs, refs, scopes, imports)
4. Test incremental updates
5. Test query APIs
"""

from __future__ import annotations

import time
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import select

from codeplane.index._internal.db import Database, create_additional_indexes
from codeplane.index._internal.indexing import FactQueries, LexicalIndex, StructuralIndexer
from codeplane.index.models import (
    Context,
    DefFact,
    File,
    ImportFact,
    ScopeFact,
)


def rel(path: Path, root: Path) -> str:
    """Get relative path string for index_files."""
    return str(path.relative_to(root))


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Database, None, None]:
    """Create a test database with schema and context."""
    db = Database(tmp_path / "test.db")
    db.create_all()
    create_additional_indexes(db.engine)

    # Create a context (required for foreign key constraints)
    with db.session() as session:
        ctx = Context(
            name="test-context",
            language_family="python",
            root_path=str(tmp_path),
        )
        session.add(ctx)
        session.commit()

    yield db


class TestFullIndexingPipeline:
    """Integration tests for the complete indexing flow."""

    def test_index_simple_python_file(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Index a simple Python file and verify facts."""
        # Create a simple Python file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        main_py = src_dir / "main.py"
        main_py.write_text('''"""Main module."""

def greet(name: str) -> str:
    """Return a greeting."""
    message = f"Hello, {name}!"
    return message

class Greeter:
    """A greeter class."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def greet(self, name: str) -> str:
        return f"{self.prefix} {name}"

if __name__ == "__main__":
    result = greet("World")
    print(result)
''')

        # Index the file
        indexer = StructuralIndexer(test_db, tmp_path)
        result = indexer.index_files([rel(main_py, tmp_path)], context_id=1)

        # Verify indexing succeeded
        assert result.files_processed == 1
        assert result.errors == []
        assert result.defs_extracted > 0
        assert result.refs_extracted > 0

        # Verify definitions were extracted
        with test_db.session() as session:
            facts = FactQueries(session)

            # Should have function definitions
            defs = facts.list_defs_by_name(unit_id=1, name="greet", limit=10)
            assert len(defs) >= 1

            # Should have class definition
            class_defs = facts.list_defs_by_name(unit_id=1, name="Greeter", limit=10)
            assert len(class_defs) >= 1

    def test_index_multi_file_project(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Index a multi-file project with imports."""
        # Create project structure
        pkg_dir = tmp_path / "mypackage"
        pkg_dir.mkdir()

        # __init__.py
        init_py = pkg_dir / "__init__.py"
        init_py.write_text('"""My package."""\n\nfrom mypackage.core import Calculator\n')

        # core.py
        core_py = pkg_dir / "core.py"
        core_py.write_text('''"""Core module."""

class Calculator:
    """A simple calculator."""

    def add(self, a: int, b: int) -> int:
        return a + b

    def subtract(self, a: int, b: int) -> int:
        return a - b
''')

        # main.py
        main_py = pkg_dir / "main.py"
        main_py.write_text('''"""Main entry point."""

from mypackage.core import Calculator

def main() -> None:
    calc = Calculator()
    result = calc.add(1, 2)
    print(result)

if __name__ == "__main__":
    main()
''')

        # Index all files
        indexer = StructuralIndexer(test_db, tmp_path)
        result = indexer.index_files(
            [rel(init_py, tmp_path), rel(core_py, tmp_path), rel(main_py, tmp_path)],
            context_id=1,
        )

        assert result.files_processed == 3
        assert result.errors == []

        # Verify cross-file facts
        with test_db.session() as session:
            # Should have Calculator definition
            calc_defs = session.exec(select(DefFact).where(DefFact.name == "Calculator")).all()
            assert len(calc_defs) >= 1

            # Should have import facts
            imports = session.exec(select(ImportFact)).all()
            assert len(imports) >= 1

    def test_incremental_index_update(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test that re-indexing a file updates facts correctly."""
        # Create initial file
        test_py = tmp_path / "test.py"
        test_py.write_text("""def original_func():
    pass
""")

        indexer = StructuralIndexer(test_db, tmp_path)

        # Initial index
        result1 = indexer.index_files([rel(test_py, tmp_path)], context_id=1)
        assert result1.defs_extracted >= 1

        with test_db.session() as session:
            defs1 = session.exec(select(DefFact).where(DefFact.name == "original_func")).all()
            assert len(defs1) >= 1

        # Modify file
        test_py.write_text("""def modified_func():
    pass

def new_func():
    return 42
""")

        # Re-index
        result2 = indexer.index_files([rel(test_py, tmp_path)], context_id=1)
        assert result2.defs_extracted >= 2

        with test_db.session() as session:
            # Should have new function
            new_defs = session.exec(select(DefFact).where(DefFact.name == "new_func")).all()
            assert len(new_defs) >= 1

    def test_scope_extraction(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test that scopes are correctly extracted."""
        test_py = tmp_path / "scopes.py"
        test_py.write_text("""class Outer:
    class Inner:
        def method(self):
            x = 1

            def nested():
                y = 2
                return y

            return nested()
""")

        indexer = StructuralIndexer(test_db, tmp_path)
        result = indexer.index_files([rel(test_py, tmp_path)], context_id=1)

        assert result.scopes_extracted >= 3  # class, class, method, nested

        with test_db.session() as session:
            scopes = session.exec(select(ScopeFact)).all()
            assert len(scopes) >= 3

    def test_reference_extraction(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test that references are extracted."""
        test_py = tmp_path / "refs.py"
        test_py.write_text("""def local_func():
    x = 1
    y = x + 1
    return y

class MyClass:
    def method(self):
        self.value = 10
        return self.value
""")

        indexer = StructuralIndexer(test_db, tmp_path)
        result = indexer.index_files([rel(test_py, tmp_path)], context_id=1)

        assert result.refs_extracted >= 1


class TestLexicalIndex:
    """Integration tests for Tantivy lexical index."""

    def test_index_and_search(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test indexing files and searching."""
        lexical_index = LexicalIndex(tmp_path / "tantivy")

        # Create test files
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        file1 = src_dir / "module_a.py"
        file1.write_text('''"""Module A with unique_identifier_abc."""

def unique_identifier_abc():
    return "hello"
''')

        file2 = src_dir / "module_b.py"
        file2.write_text('''"""Module B with different content."""

def another_function():
    return "world"
''')

        # First ensure files exist in database
        with test_db.session() as session:
            f1 = File(
                path=rel(file1, tmp_path),
                content_hash="hash1",
                indexed_at=time.time(),
            )
            f2 = File(
                path=rel(file2, tmp_path),
                content_hash="hash2",
                indexed_at=time.time(),
            )
            session.add(f1)
            session.add(f2)
            session.commit()
            file1_id = f1.id
            file2_id = f2.id

        # Index in Tantivy (using add_file API)
        lexical_index.add_file(
            file_path=rel(file1, tmp_path),
            content=file1.read_text(),
            context_id=1,
            file_id=file1_id or 0,
        )
        lexical_index.add_file(
            file_path=rel(file2, tmp_path),
            content=file2.read_text(),
            context_id=1,
            file_id=file2_id or 0,
        )
        lexical_index.reload()  # Reload to see newly added documents

        # Search for unique content
        search_results = lexical_index.search("unique_identifier_abc", limit=10)
        assert len(search_results.results) >= 1
        assert any("module_a" in r.file_path for r in search_results.results)

    def test_delete_and_reindex(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test deleting and re-indexing files."""
        lexical_index = LexicalIndex(tmp_path / "tantivy")
        file_path = tmp_path / "dynamic.py"
        file_path.write_text("def old_function(): pass")

        with test_db.session() as session:
            f = File(
                path="dynamic.py",
                content_hash="hash1",
                indexed_at=time.time(),
            )
            session.add(f)
            session.commit()
            file_id = f.id

        # Initial index
        lexical_index.add_file(
            file_path="dynamic.py",
            content="def old_function(): pass",
            context_id=1,
            file_id=file_id or 0,
        )
        lexical_index.reload()  # Reload to see newly added documents

        # Verify old content searchable
        results = lexical_index.search("old_function", limit=10)
        assert len(results.results) >= 1

        # Remove and re-index with new content
        lexical_index.remove_file("dynamic.py")
        lexical_index.add_file(
            file_path="dynamic.py",
            content="def new_function(): pass",
            context_id=1,
            file_id=file_id or 0,
        )
        lexical_index.reload()  # Reload to see changes

        # Old content should not be found
        old_results = lexical_index.search("old_function", limit=10)
        assert len(old_results.results) == 0

        # New content should be found
        new_results = lexical_index.search("new_function", limit=10)
        assert len(new_results.results) >= 1


class TestFactQueries:
    """Integration tests for bounded fact queries."""

    def test_list_defs_respects_limit(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Verify query limits are enforced."""
        # Create file with many definitions
        test_py = tmp_path / "many_defs.py"
        test_py.write_text("\n".join([f"def func_{i}(): pass" for i in range(20)]))

        indexer = StructuralIndexer(test_db, tmp_path)
        indexer.index_files([rel(test_py, tmp_path)], context_id=1)

        with test_db.session() as session:
            facts = FactQueries(session)
            all_defs = facts.list_defs_in_file(file_id=1, limit=1000)
            limited_defs = facts.list_defs_in_file(file_id=1, limit=5)

            assert len(limited_defs) <= 5
            assert len(all_defs) >= 20

    def test_get_def_by_uid(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test retrieving definition by UID."""
        test_py = tmp_path / "test.py"
        test_py.write_text("def my_function(): pass")

        indexer = StructuralIndexer(test_db, tmp_path)
        indexer.index_files([rel(test_py, tmp_path)], context_id=1)

        with test_db.session() as session:
            facts = FactQueries(session)
            defs = facts.list_defs_by_name(unit_id=1, name="my_function", limit=1)
            assert len(defs) >= 1

            # Get by UID
            def_fact = facts.get_def(defs[0].def_uid)
            assert def_fact is not None
            assert def_fact.name == "my_function"


class TestRealWorldPatterns:
    """Tests for common real-world code patterns."""

    def test_decorator_functions(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test indexing decorated functions."""
        test_py = tmp_path / "decorators.py"
        test_py.write_text("""from functools import wraps

def my_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

@my_decorator
def decorated_function():
    return "decorated"
""")

        indexer = StructuralIndexer(test_db, tmp_path)
        result = indexer.index_files([rel(test_py, tmp_path)], context_id=1)

        assert result.defs_extracted >= 3  # decorator, wrapper, decorated

    def test_async_functions(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test indexing async functions."""
        test_py = tmp_path / "async_code.py"
        test_py.write_text("""import asyncio

async def fetch_data(url: str) -> str:
    await asyncio.sleep(0.1)
    return f"Data from {url}"

async def main():
    result = await fetch_data("http://example.com")
    print(result)
""")

        indexer = StructuralIndexer(test_db, tmp_path)
        result = indexer.index_files([rel(test_py, tmp_path)], context_id=1)

        assert result.defs_extracted >= 2

        with test_db.session() as session:
            facts = FactQueries(session)
            async_defs = facts.list_defs_by_name(unit_id=1, name="fetch_data", limit=10)
            assert len(async_defs) >= 1

    def test_dataclass_patterns(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test indexing dataclasses."""
        test_py = tmp_path / "dataclasses_example.py"
        test_py.write_text("""from dataclasses import dataclass, field
from typing import List

@dataclass
class Person:
    name: str
    age: int
    tags: List[str] = field(default_factory=list)

    def greet(self) -> str:
        return f"Hello, I am {self.name}"

@dataclass(frozen=True)
class Point:
    x: float
    y: float
""")

        indexer = StructuralIndexer(test_db, tmp_path)
        indexer.index_files([rel(test_py, tmp_path)], context_id=1)

        with test_db.session() as session:
            facts = FactQueries(session)

            # Should find dataclass definitions
            person = facts.list_defs_by_name(unit_id=1, name="Person", limit=1)
            assert len(person) >= 1

            point = facts.list_defs_by_name(unit_id=1, name="Point", limit=1)
            assert len(point) >= 1

    def test_type_hints_and_generics(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test indexing code with complex type hints."""
        test_py = tmp_path / "typed_code.py"
        test_py.write_text("""from typing import TypeVar, Generic, Optional, Union, Callable

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")

class Container(Generic[T]):
    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value

def process(
    items: list[int],
    callback: Callable[[int], str],
    default: Optional[str] = None,
) -> Union[str, None]:
    if not items:
        return default
    return callback(items[0])
""")

        indexer = StructuralIndexer(test_db, tmp_path)
        result = indexer.index_files([rel(test_py, tmp_path)], context_id=1)

        assert result.defs_extracted >= 2

    def test_context_managers(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test indexing context managers."""
        test_py = tmp_path / "context_managers.py"
        test_py.write_text("""from contextlib import contextmanager
from typing import Generator

@contextmanager
def managed_resource(name: str) -> Generator[str, None, None]:
    print(f"Acquiring {name}")
    try:
        yield name
    finally:
        print(f"Releasing {name}")

class FileManager:
    def __init__(self, path: str) -> None:
        self.path = path

    def __enter__(self):
        print(f"Opening {self.path}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        print(f"Closing {self.path}")
        return False
""")

        indexer = StructuralIndexer(test_db, tmp_path)
        indexer.index_files([rel(test_py, tmp_path)], context_id=1)

        with test_db.session() as session:
            facts = FactQueries(session)

            # Should find context manager
            cm = facts.list_defs_by_name(unit_id=1, name="managed_resource", limit=1)
            assert len(cm) >= 1

            # Should find class-based context manager
            fm = facts.list_defs_by_name(unit_id=1, name="FileManager", limit=1)
            assert len(fm) >= 1

    def test_module_level_code(
        self,
        test_db: Database,
        tmp_path: Path,
    ) -> None:
        """Test indexing module-level definitions."""
        test_py = tmp_path / "module_level.py"
        test_py.write_text('''"""Module with various module-level definitions."""

# Constants
MAX_SIZE = 1000
DEFAULT_NAME = "unnamed"

# Type alias
StringList = list[str]

# Module-level function
def helper() -> int:
    return MAX_SIZE // 2

# Main guard
if __name__ == "__main__":
    print(helper())
''')

        indexer = StructuralIndexer(test_db, tmp_path)
        result = indexer.index_files([rel(test_py, tmp_path)], context_id=1)

        assert result.defs_extracted >= 1  # At least the function
