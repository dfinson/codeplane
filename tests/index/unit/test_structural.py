"""Unit tests for structural indexer (structural.py).

Tests cover:
- Extraction of all Tier 1 fact types (DefFact, RefFact, ScopeFact, ImportFact, LocalBindFact, DynamicAccessSite)
- Integration with Tree-sitter parser
- Batch processing and error handling
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.db import Database
from codeplane.index._internal.indexing.structural import (
    BatchResult,
    ExtractionResult,
    StructuralIndexer,
    _compute_def_uid,
    _extract_file,
    _find_containing_scope,
)
from codeplane.index._internal.parsing import SyntacticScope
from codeplane.index.models import Context, DefFact, RefFact, RefTier, Role


@pytest.fixture
def db(temp_dir: Path) -> Database:
    """Create a test database with schema."""
    from codeplane.index._internal.db import create_additional_indexes

    db_path = temp_dir / "test_structural.db"
    db = Database(db_path)
    db.create_all()
    create_additional_indexes(db.engine)
    return db


@pytest.fixture
def indexer(db: Database, temp_dir: Path) -> StructuralIndexer:
    """Create a StructuralIndexer instance."""
    return StructuralIndexer(db, temp_dir)


class TestDefUidComputation:
    """Tests for def_uid computation."""

    def test_compute_def_uid_basic(self) -> None:
        """def_uid should be deterministic."""
        uid1 = _compute_def_uid(1, "src/foo.py", "function", "foo", None)
        uid2 = _compute_def_uid(1, "src/foo.py", "function", "foo", None)
        assert uid1 == uid2

    def test_compute_def_uid_different_inputs(self) -> None:
        """Different inputs should produce different def_uids."""
        uid1 = _compute_def_uid(1, "src/foo.py", "function", "foo", None)
        uid2 = _compute_def_uid(1, "src/foo.py", "function", "bar", None)
        uid3 = _compute_def_uid(2, "src/foo.py", "function", "foo", None)  # Different unit_id
        uid4 = _compute_def_uid(1, "src/bar.py", "function", "foo", None)  # Different file
        assert uid1 != uid2
        assert uid1 != uid3
        assert uid1 != uid4

    def test_compute_def_uid_with_signature(self) -> None:
        """Signature hash should affect def_uid."""
        uid1 = _compute_def_uid(1, "src/foo.py", "function", "foo", "abc123")
        uid2 = _compute_def_uid(1, "src/foo.py", "function", "foo", "def456")
        assert uid1 != uid2

    def test_compute_def_uid_length(self) -> None:
        """def_uid should be 16 characters (truncated SHA256)."""
        uid = _compute_def_uid(1, "src/foo.py", "function", "foo", None)
        assert len(uid) == 16


class TestFindContainingScope:
    """Tests for scope containment."""

    def test_file_scope_default(self) -> None:
        """Should return file scope (0) when no scopes contain position."""
        scopes: list[SyntacticScope] = []
        result = _find_containing_scope(scopes, 10, 5)
        assert result == 0

    def test_find_containing_scope_basic(self) -> None:
        """Should find the scope containing a position."""
        scopes = [
            SyntacticScope(
                scope_id=0,
                parent_scope_id=None,
                kind="file",
                start_line=1,
                start_col=0,
                end_line=100,
                end_col=0,
            ),
            SyntacticScope(
                scope_id=1,
                parent_scope_id=0,
                kind="function",
                start_line=5,
                start_col=0,
                end_line=20,
                end_col=0,
            ),
        ]
        result = _find_containing_scope(scopes, 10, 5)
        assert result == 1  # Inside function scope

    def test_find_innermost_scope(self) -> None:
        """Should return innermost scope when multiple scopes contain position."""
        scopes = [
            SyntacticScope(
                scope_id=0,
                parent_scope_id=None,
                kind="file",
                start_line=1,
                start_col=0,
                end_line=100,
                end_col=0,
            ),
            SyntacticScope(
                scope_id=1,
                parent_scope_id=0,
                kind="class",
                start_line=5,
                start_col=0,
                end_line=50,
                end_col=0,
            ),
            SyntacticScope(
                scope_id=2,
                parent_scope_id=1,
                kind="function",
                start_line=10,
                start_col=4,
                end_line=30,
                end_col=0,
            ),
        ]
        result = _find_containing_scope(scopes, 15, 8)
        assert result == 2  # Innermost (function) scope


class TestExtractFile:
    """Tests for single file extraction."""

    def test_extract_python_file(self, temp_dir: Path) -> None:
        """Should extract facts from Python file."""
        content = """
def hello():
    return "Hello"

class Greeter:
    def greet(self, name):
        return f"Hello, {name}"
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        assert result.error is None
        assert len(result.defs) >= 3  # hello, Greeter, greet
        assert len(result.refs) > 0  # At least definition refs
        assert len(result.scopes) >= 1  # At least file scope

    def test_extract_with_imports(self, temp_dir: Path) -> None:
        """Should extract import facts."""
        content = """
import os
from pathlib import Path
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        assert result.error is None
        assert len(result.imports) >= 2  # os and Path

        # Check import structure
        import_names = [i["imported_name"] for i in result.imports]
        assert "os" in import_names
        assert "Path" in import_names

    def test_extract_with_local_binds(self, temp_dir: Path) -> None:
        """Should extract local binding facts."""
        content = """
def foo():
    x = 1
    return x
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        assert result.error is None
        # Should have binding for function definition
        assert len(result.binds) >= 1

    def test_extract_nonexistent_file(self, temp_dir: Path) -> None:
        """Should return error for nonexistent file."""
        result = _extract_file("nonexistent.py", str(temp_dir), unit_id=1)

        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_extract_unsupported_extension(self, temp_dir: Path) -> None:
        """Should gracefully skip unsupported file types."""
        file_path = temp_dir / "test.unknown"
        file_path.write_text("content")

        result = _extract_file("test.unknown", str(temp_dir), unit_id=1)

        # Unsupported files are skipped (no error), but marked as no-grammar
        assert result.error is None
        assert result.skipped_no_grammar is True

    def test_extract_content_hash(self, temp_dir: Path) -> None:
        """Should compute content hash."""
        content = "def foo(): pass"
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        assert result.content_hash is not None
        assert len(result.content_hash) == 64  # SHA256 hex

    def test_extract_dynamic_access(self, temp_dir: Path) -> None:
        """Should extract dynamic access sites."""
        content = """
x = getattr(obj, "foo")
y = obj["key"]
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        assert result.error is None
        # Should detect getattr and bracket access
        assert len(result.dynamic_sites) >= 2


class TestExtractionResult:
    """Tests for ExtractionResult structure."""

    def test_extraction_result_defaults(self) -> None:
        """ExtractionResult should have correct defaults."""
        result = ExtractionResult(file_path="test.py")

        assert result.file_path == "test.py"
        assert result.defs == []
        assert result.refs == []
        assert result.scopes == []
        assert result.imports == []
        assert result.binds == []
        assert result.dynamic_sites == []
        assert result.error is None


class TestBatchResult:
    """Tests for BatchResult structure."""

    def test_batch_result_defaults(self) -> None:
        """BatchResult should have correct defaults."""
        result = BatchResult()

        assert result.files_processed == 0
        assert result.defs_extracted == 0
        assert result.refs_extracted == 0
        assert result.scopes_extracted == 0
        assert result.imports_extracted == 0
        assert result.binds_extracted == 0
        assert result.dynamic_sites_extracted == 0
        assert result.errors == []
        assert result.duration_ms == 0


class TestStructuralIndexer:
    """Tests for StructuralIndexer."""

    def test_indexer_creation(self, indexer: StructuralIndexer) -> None:
        """Should create indexer instance."""
        assert indexer is not None
        assert indexer._parser is not None

    def test_index_single_file(
        self, db: Database, indexer: StructuralIndexer, temp_dir: Path
    ) -> None:
        """Should index a single file."""
        content = """
def hello():
    return "Hello"
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        db.create_all()
        # Create a context first
        with db.session() as session:
            ctx = Context(
                name="test",
                language_family="python",
                root_path=str(temp_dir),
            )
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        result = indexer.index_files(["test.py"], context_id=context_id or 1)

        assert result.files_processed == 1
        assert result.defs_extracted >= 1  # hello function
        assert result.errors == []

    def test_index_multiple_files(
        self, db: Database, indexer: StructuralIndexer, temp_dir: Path
    ) -> None:
        """Should index multiple files."""
        (temp_dir / "a.py").write_text("def foo(): pass")
        (temp_dir / "b.py").write_text("def bar(): pass")

        db.create_all()
        with db.session() as session:
            ctx = Context(
                name="test",
                language_family="python",
                root_path=str(temp_dir),
            )
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        result = indexer.index_files(["a.py", "b.py"], context_id=context_id or 1)

        assert result.files_processed == 2
        assert result.defs_extracted >= 2  # foo and bar

    def test_index_with_errors(
        self, db: Database, indexer: StructuralIndexer, temp_dir: Path
    ) -> None:
        """Should handle files with errors gracefully."""
        (temp_dir / "good.py").write_text("def foo(): pass")

        db.create_all()
        with db.session() as session:
            ctx = Context(
                name="test",
                language_family="python",
                root_path=str(temp_dir),
            )
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        result = indexer.index_files(["good.py", "nonexistent.py"], context_id=context_id or 1)

        assert result.files_processed == 2
        assert result.defs_extracted >= 1  # From good.py
        assert len(result.errors) >= 1  # From nonexistent.py


class TestRefTierAssignment:
    """Tests for RefTier assignment during extraction."""

    def test_definition_refs_are_proven(self, temp_dir: Path) -> None:
        """Definition sites should have PROVEN ref_tier."""
        content = "def foo(): pass"
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        # Find the definition ref for foo
        def_refs = [r for r in result.refs if r["role"] == Role.DEFINITION.value]
        assert len(def_refs) >= 1
        assert all(r["ref_tier"] == RefTier.PROVEN.value for r in def_refs)

    def test_same_file_refs_are_proven(self, temp_dir: Path) -> None:
        """References to same-file definitions should be PROVEN."""
        content = """
def foo():
    return 1

x = foo()
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        # Find reference to foo (not the definition)
        usage_refs = [
            r for r in result.refs if r["token_text"] == "foo" and r["role"] == Role.REFERENCE.value
        ]
        # Should have at least one PROVEN reference
        proven_refs = [r for r in usage_refs if r["ref_tier"] == RefTier.PROVEN.value]
        assert len(proven_refs) >= 1

    def test_import_refs_are_unknown_or_strong(self, temp_dir: Path) -> None:
        """Import statements should have UNKNOWN or STRONG ref_tier."""
        content = """
import os
os.path.exists(".")
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        # Find import ref
        import_refs = [r for r in result.refs if r["role"] == Role.IMPORT.value]
        assert len(import_refs) >= 1
        # Import statements are UNKNOWN until cross-file resolution
        assert all(
            r["ref_tier"] in (RefTier.UNKNOWN.value, RefTier.STRONG.value) for r in import_refs
        )

    def test_csharp_using_creates_import_facts(self, temp_dir: Path) -> None:
        """C# using directives should be extracted as ImportFacts."""
        content = """using System;
using Newtonsoft.Json;

namespace Test {
    class Foo { }
}
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)

        result = _extract_file("test.cs", str(temp_dir), unit_id=1)

        import_names = [i["imported_name"] for i in result.imports]
        assert "System" in import_names
        assert "Newtonsoft.Json" in import_names

    def test_csharp_aliased_using_creates_strong_ref(self, temp_dir: Path) -> None:
        """Aliased C# usings should produce STRONG refs via import_uid_by_alias."""
        content = """using MyAlias = System.Collections.Generic.List;

namespace Test {
    class Foo {
        MyAlias x;
    }
}
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)

        result = _extract_file("test.cs", str(temp_dir), unit_id=1)

        # The identifier 'MyAlias' should be STRONG (matched via import_uid_by_alias)
        alias_refs = [
            r
            for r in result.refs
            if r["token_text"] == "MyAlias" and r["role"] == Role.REFERENCE.value
        ]
        strong_refs = [r for r in alias_refs if r["ref_tier"] == RefTier.STRONG.value]
        assert len(strong_refs) >= 1

    def test_csharp_namespace_type_map_populated(self, temp_dir: Path) -> None:
        """C# extraction should populate namespace_type_map."""
        content = """using System;

namespace Foo.Bar {
    class Baz { }
    interface IBaz { }
}
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)

        result = _extract_file("test.cs", str(temp_dir), unit_id=1)

        assert "Foo.Bar" in result.namespace_type_map
        assert "Baz" in result.namespace_type_map["Foo.Bar"]
        assert "IBaz" in result.namespace_type_map["Foo.Bar"]

    def test_python_wildcard_import_extracted(self, temp_dir: Path) -> None:
        """Python wildcard imports should be extracted as ImportFacts."""
        content = "from os.path import *\n"
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        star_imports = [i for i in result.imports if i["imported_name"] == "*"]
        assert len(star_imports) == 1
        assert star_imports[0]["import_kind"] == "python_from"


class TestCrossFileResolution:
    """Tests for DB-backed cross-file ref_tier resolution (Pass 1.5)."""

    def test_csharp_namespace_using_upgrades_to_strong(self, db: Database, temp_dir: Path) -> None:
        """UNKNOWN refs matching project-internal namespace types should become STRONG."""
        # File 1: defines the type in a namespace
        (temp_dir / "Resolver.cs").write_text(
            """namespace Newtonsoft.Json.Serialization {
    public class DefaultContractResolver { }
}
"""
        )
        # File 2: uses the type via a using directive
        (temp_dir / "Client.cs").write_text(
            """using Newtonsoft.Json.Serialization;

namespace App {
    class Client {
        DefaultContractResolver resolver;
    }
}
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="dotnet", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["Resolver.cs", "Client.cs"], context_id=context_id or 1)

        # Before resolution: the ref to DefaultContractResolver in Client.cs is UNKNOWN
        with db.session() as session:
            from sqlmodel import select

            from codeplane.index.models import File

            client_file = session.exec(select(File).where(File.path == "Client.cs")).first()
            assert client_file is not None
            dcr_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == client_file.id,
                    RefFact.token_text == "DefaultContractResolver",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert len(dcr_refs) >= 1
            assert all(r.ref_tier == RefTier.UNKNOWN.value for r in dcr_refs)

        # Run DB-backed resolution (Pass 1.5)
        from codeplane.index._internal.indexing.resolver import resolve_namespace_refs

        stats = resolve_namespace_refs(db)
        assert stats.refs_upgraded >= 1

        # After resolution: should be STRONG with target_def_uid linked
        with db.session() as session:
            dcr_refs_after = session.exec(
                select(RefFact).where(
                    RefFact.file_id == client_file.id,
                    RefFact.token_text == "DefaultContractResolver",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert all(r.ref_tier == RefTier.STRONG.value for r in dcr_refs_after)

            # target_def_uid must be set for rename to discover these refs
            resolver_def = session.exec(
                select(DefFact).where(DefFact.name == "DefaultContractResolver")
            ).first()
            assert resolver_def is not None
            assert all(r.target_def_uid == resolver_def.def_uid for r in dcr_refs_after), (
                "target_def_uid must link to the DefFact for rename discovery"
            )

    def test_csharp_external_namespace_stays_unknown(self, db: Database, temp_dir: Path) -> None:
        """Refs to types from external namespaces (not in project) stay UNKNOWN."""
        (temp_dir / "Client.cs").write_text(
            """using System.Collections.Generic;

namespace App {
    class Client {
        List items;
    }
}
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="dotnet", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["Client.cs"], context_id=context_id or 1)

        from codeplane.index._internal.indexing.resolver import resolve_namespace_refs

        resolve_namespace_refs(db)

        # 'List' comes from System.Collections.Generic (external) — stays UNKNOWN
        with db.session() as session:
            from sqlmodel import select

            from codeplane.index.models import File

            client_file = session.exec(select(File).where(File.path == "Client.cs")).first()
            assert client_file is not None
            list_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == client_file.id,
                    RefFact.token_text == "List",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            if list_refs:
                assert all(r.ref_tier == RefTier.UNKNOWN.value for r in list_refs)

    def test_python_star_import_upgrades_to_strong(self, db: Database, temp_dir: Path) -> None:
        """Python star imports from project-internal modules should upgrade refs to STRONG."""
        # Module with exported definitions
        (temp_dir / "utils.py").write_text(
            """def helper():
    pass

class Utility:
    pass
"""
        )
        # File that star-imports from utils
        (temp_dir / "main.py").write_text(
            """from utils import *

x = helper()
y = Utility()
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["utils.py", "main.py"], context_id=context_id or 1)

        # Before resolution: helper and Utility refs should be UNKNOWN
        with db.session() as session:
            from sqlmodel import select

            from codeplane.index.models import File

            main_file = session.exec(select(File).where(File.path == "main.py")).first()
            assert main_file is not None
            helper_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == main_file.id,
                    RefFact.token_text == "helper",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert len(helper_refs) >= 1
            assert all(r.ref_tier == RefTier.UNKNOWN.value for r in helper_refs)

        # Run DB-backed resolution (Pass 1.5)
        from codeplane.index._internal.indexing.resolver import resolve_star_import_refs

        stats = resolve_star_import_refs(db)
        assert stats.refs_upgraded >= 1

        # After resolution: should be STRONG with target_def_uid linked
        with db.session() as session:
            helper_refs_after = session.exec(
                select(RefFact).where(
                    RefFact.file_id == main_file.id,
                    RefFact.token_text == "helper",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert all(r.ref_tier == RefTier.STRONG.value for r in helper_refs_after)

            # target_def_uid must be set for rename to discover these refs
            helper_def = session.exec(select(DefFact).where(DefFact.name == "helper")).first()
            assert helper_def is not None
            assert all(r.target_def_uid == helper_def.def_uid for r in helper_refs_after), (
                "target_def_uid must link to DefFact for rename discovery"
            )

            utility_refs_after = session.exec(
                select(RefFact).where(
                    RefFact.file_id == main_file.id,
                    RefFact.token_text == "Utility",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert all(r.ref_tier == RefTier.STRONG.value for r in utility_refs_after)

            utility_def = session.exec(select(DefFact).where(DefFact.name == "Utility")).first()
            assert utility_def is not None
            assert all(r.target_def_uid == utility_def.def_uid for r in utility_refs_after), (
                "target_def_uid must link to DefFact for rename discovery"
            )

    def test_python_star_import_external_stays_unknown(self, db: Database, temp_dir: Path) -> None:
        """Star imports from external modules should leave refs as UNKNOWN."""
        (temp_dir / "main.py").write_text(
            """from os.path import *

x = exists("/tmp")
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["main.py"], context_id=context_id or 1)

        from codeplane.index._internal.indexing.resolver import resolve_star_import_refs

        resolve_star_import_refs(db)

        # 'exists' comes from os.path (external) — stays UNKNOWN
        with db.session() as session:
            from sqlmodel import select

            from codeplane.index.models import File

            main_file = session.exec(select(File).where(File.path == "main.py")).first()
            assert main_file is not None
            exists_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == main_file.id,
                    RefFact.token_text == "exists",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            if exists_refs:
                assert all(r.ref_tier == RefTier.UNKNOWN.value for r in exists_refs)

    def test_csharp_multiple_files_same_namespace(self, db: Database, temp_dir: Path) -> None:
        """Types from the same namespace across multiple files should all resolve."""
        (temp_dir / "A.cs").write_text(
            """namespace Shared {
    public class TypeA { }
}
"""
        )
        (temp_dir / "B.cs").write_text(
            """namespace Shared {
    public class TypeB { }
}
"""
        )
        (temp_dir / "Consumer.cs").write_text(
            """using Shared;

namespace App {
    class Consumer {
        TypeA a;
        TypeB b;
    }
}
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="dotnet", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        # Critically: index in SEPARATE batches (simulates the 25-file batching)
        indexer.index_files(["A.cs"], context_id=context_id or 1)
        indexer.index_files(["B.cs"], context_id=context_id or 1)
        indexer.index_files(["Consumer.cs"], context_id=context_id or 1)

        from codeplane.index._internal.indexing.resolver import resolve_namespace_refs

        stats = resolve_namespace_refs(db)
        assert stats.refs_upgraded >= 1
        with db.session() as session:
            from sqlmodel import select

            from codeplane.index.models import File

            consumer_file = session.exec(select(File).where(File.path == "Consumer.cs")).first()
            assert consumer_file is not None
            for type_name in ("TypeA", "TypeB"):
                refs = session.exec(
                    select(RefFact).where(
                        RefFact.file_id == consumer_file.id,
                        RefFact.token_text == type_name,
                        RefFact.role == Role.REFERENCE.value,
                    )
                ).all()
                assert len(refs) >= 1, f"No refs found for {type_name}"
                assert all(r.ref_tier == RefTier.STRONG.value for r in refs), (
                    f"{type_name} should be STRONG after DB-backed resolution"
                )

    def test_csharp_namespace_on_def_facts(self, db: Database, temp_dir: Path) -> None:
        """C# extraction should populate namespace on DefFacts."""
        (temp_dir / "test.cs").write_text(
            """namespace Foo.Bar {
    class Baz { }
    interface IBaz { }
}
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="dotnet", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["test.cs"], context_id=context_id or 1)

        with db.session() as session:
            from sqlmodel import select

            defs = session.exec(select(DefFact)).all()
            baz_def = next((d for d in defs if d.name == "Baz"), None)
            ibaz_def = next((d for d in defs if d.name == "IBaz"), None)
            assert baz_def is not None
            assert baz_def.namespace == "Foo.Bar"
            assert ibaz_def is not None
            assert ibaz_def.namespace == "Foo.Bar"

    def test_csharp_same_namespace_upgrades_to_strong(self, db: Database, temp_dir: Path) -> None:
        """Types in the same namespace should resolve without a using directive."""
        # File 1: defines JsonSerializer in Newtonsoft.Json
        (temp_dir / "JsonSerializer.cs").write_text(
            """namespace Newtonsoft.Json {
    public class JsonSerializer { }
}
"""
        )
        # File 2: in the SAME namespace — no using needed
        (temp_dir / "JsonConvert.cs").write_text(
            """namespace Newtonsoft.Json {
    class JsonConvert {
        JsonSerializer serializer;
    }
}
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="dotnet", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["JsonSerializer.cs", "JsonConvert.cs"], context_id=context_id or 1)

        # Before resolution: the ref should be UNKNOWN (no using directive)
        with db.session() as session:
            from sqlmodel import select

            from codeplane.index.models import File

            convert_file = session.exec(select(File).where(File.path == "JsonConvert.cs")).first()
            assert convert_file is not None
            js_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == convert_file.id,
                    RefFact.token_text == "JsonSerializer",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert len(js_refs) >= 1
            assert all(r.ref_tier == RefTier.UNKNOWN.value for r in js_refs)

        # namespace-using resolution should NOT match (no using directive)
        from codeplane.index._internal.indexing.resolver import (
            resolve_namespace_refs,
            resolve_same_namespace_refs,
        )

        ns_stats = resolve_namespace_refs(db)
        assert ns_stats.refs_upgraded == 0  # No using directive → no match

        # Same-namespace resolution SHOULD match
        same_stats = resolve_same_namespace_refs(db)
        assert same_stats.refs_upgraded >= 1

        # After resolution: STRONG with target_def_uid
        with db.session() as session:
            js_refs_after = session.exec(
                select(RefFact).where(
                    RefFact.file_id == convert_file.id,
                    RefFact.token_text == "JsonSerializer",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert all(r.ref_tier == RefTier.STRONG.value for r in js_refs_after)

            serializer_def = session.exec(
                select(DefFact).where(DefFact.name == "JsonSerializer")
            ).first()
            assert serializer_def is not None
            assert all(r.target_def_uid == serializer_def.def_uid for r in js_refs_after), (
                "target_def_uid must link to DefFact for rename discovery"
            )

    def test_csharp_parent_namespace_upgrades_to_strong(self, db: Database, temp_dir: Path) -> None:
        """Types in a parent namespace should resolve without a using directive."""
        # File 1: defines JsonSerializer in Newtonsoft.Json
        (temp_dir / "JsonSerializer.cs").write_text(
            """namespace Newtonsoft.Json {
    public class JsonSerializer { }
}
"""
        )
        # File 2: in a CHILD namespace — parent types visible without using
        (temp_dir / "RegexConverter.cs").write_text(
            """namespace Newtonsoft.Json.Converters {
    class RegexConverter {
        JsonSerializer serializer;
    }
}
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="dotnet", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["JsonSerializer.cs", "RegexConverter.cs"], context_id=context_id or 1)

        # Before: UNKNOWN (no using directive)
        with db.session() as session:
            from sqlmodel import select

            from codeplane.index.models import File

            converter_file = session.exec(
                select(File).where(File.path == "RegexConverter.cs")
            ).first()
            assert converter_file is not None
            js_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == converter_file.id,
                    RefFact.token_text == "JsonSerializer",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert len(js_refs) >= 1
            assert all(r.ref_tier == RefTier.UNKNOWN.value for r in js_refs)

        from codeplane.index._internal.indexing.resolver import resolve_same_namespace_refs

        stats = resolve_same_namespace_refs(db)
        assert stats.refs_upgraded >= 1

        # After: STRONG with target_def_uid linked
        with db.session() as session:
            js_refs_after = session.exec(
                select(RefFact).where(
                    RefFact.file_id == converter_file.id,
                    RefFact.token_text == "JsonSerializer",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert all(r.ref_tier == RefTier.STRONG.value for r in js_refs_after)

            serializer_def = session.exec(
                select(DefFact).where(DefFact.name == "JsonSerializer")
            ).first()
            assert serializer_def is not None
            assert all(r.target_def_uid == serializer_def.def_uid for r in js_refs_after), (
                "target_def_uid must link to DefFact for rename discovery"
            )

    def test_csharp_unrelated_namespace_stays_unknown(self, db: Database, temp_dir: Path) -> None:
        """Types in unrelated namespaces should NOT resolve via same-namespace."""
        # File 1: defines Foo in Namespace.A
        (temp_dir / "A.cs").write_text(
            """namespace Namespace.A {
    public class Foo { }
}
"""
        )
        # File 2: in Namespace.B — NOT a parent/child — should stay UNKNOWN
        (temp_dir / "B.cs").write_text(
            """namespace Namespace.B {
    class Bar {
        Foo x;
    }
}
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="dotnet", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["A.cs", "B.cs"], context_id=context_id or 1)

        from codeplane.index._internal.indexing.resolver import resolve_same_namespace_refs

        resolve_same_namespace_refs(db)

        # Foo in Namespace.A is NOT visible from Namespace.B
        with db.session() as session:
            from sqlmodel import select

            from codeplane.index.models import File

            b_file = session.exec(select(File).where(File.path == "B.cs")).first()
            assert b_file is not None
            foo_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == b_file.id,
                    RefFact.token_text == "Foo",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            if foo_refs:
                assert all(r.ref_tier == RefTier.UNKNOWN.value for r in foo_refs)
