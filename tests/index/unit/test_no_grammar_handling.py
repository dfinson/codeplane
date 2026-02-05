"""Tests for handling languages without tree-sitter grammars.

These tests verify that languages like F#, VB.NET, Erlang, and PowerShell are
gracefully skipped by the structural indexer (since no PyPI tree-sitter grammar
exists), while still being tracked for lexical search.

Covers:
- _has_grammar_for_file detection
- ExtractionResult.skipped_no_grammar flag
- BatchResult.files_skipped_no_grammar counter
- _extract_file behavior for no-grammar files
- StructuralIndexer.index_files integration
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from codeplane.core.languages import detect_language_family, has_grammar
from codeplane.index._internal.indexing.structural import (
    BatchResult,
    ExtractionResult,
    StructuralIndexer,
    _extract_file,
    _has_grammar_for_file,
)

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database


# =============================================================================
# Tests for _has_grammar_for_file
# =============================================================================


class TestHasGrammarForFile:
    """Tests for _has_grammar_for_file function."""

    @pytest.mark.parametrize(
        "file_path",
        [
            "main.py",
            "lib/module.py",
            "src/types.pyi",
        ],
    )
    def test_python_has_grammar(self, file_path: str) -> None:
        """Python files should have grammar support."""
        assert _has_grammar_for_file(file_path) is True

    @pytest.mark.parametrize(
        "file_path",
        [
            "app.js",
            "components/Button.jsx",
            "utils/helper.ts",
            "pages/index.tsx",
            "config.mjs",
        ],
    )
    def test_javascript_typescript_has_grammar(self, file_path: str) -> None:
        """JavaScript/TypeScript files should have grammar support."""
        assert _has_grammar_for_file(file_path) is True

    @pytest.mark.parametrize(
        "file_path",
        [
            "main.go",
            "pkg/server/handler.go",
        ],
    )
    def test_go_has_grammar(self, file_path: str) -> None:
        """Go files should have grammar support."""
        assert _has_grammar_for_file(file_path) is True

    @pytest.mark.parametrize(
        "file_path",
        [
            "main.rs",
            "src/lib.rs",
            "tests/integration.rs",
        ],
    )
    def test_rust_has_grammar(self, file_path: str) -> None:
        """Rust files should have grammar support."""
        assert _has_grammar_for_file(file_path) is True

    @pytest.mark.parametrize(
        "file_path",
        [
            "Program.fs",
            "src/Library.fs",
            "scripts/build.fsx",
            "interfaces.fsi",
        ],
    )
    def test_fsharp_no_grammar(self, file_path: str) -> None:
        """F# files should NOT have grammar support (no PyPI grammar)."""
        assert _has_grammar_for_file(file_path) is False

    @pytest.mark.parametrize(
        "file_path",
        [
            "Program.vb",
            "src/Module.vb",
        ],
    )
    def test_vbnet_no_grammar(self, file_path: str) -> None:
        """VB.NET files should NOT have grammar support (no PyPI grammar)."""
        assert _has_grammar_for_file(file_path) is False

    @pytest.mark.parametrize(
        "file_path",
        [
            "server.erl",
            "include/records.hrl",
        ],
    )
    def test_erlang_no_grammar(self, file_path: str) -> None:
        """Erlang files should NOT have grammar support (no PyPI grammar)."""
        assert _has_grammar_for_file(file_path) is False

    @pytest.mark.parametrize(
        "file_path",
        [
            "script.ps1",
            "module.psm1",
            "manifest.psd1",
        ],
    )
    def test_powershell_no_grammar(self, file_path: str) -> None:
        """PowerShell files should NOT have grammar support (no PyPI grammar)."""
        assert _has_grammar_for_file(file_path) is False

    @pytest.mark.parametrize(
        "file_path",
        [
            "unknown.xyz",
            "file.abc123",
            "noextension",
        ],
    )
    def test_unknown_extension_no_grammar(self, file_path: str) -> None:
        """Unknown file extensions should NOT have grammar support."""
        assert _has_grammar_for_file(file_path) is False

    @pytest.mark.parametrize(
        "file_path,expected",
        [
            ("core.clj", False),  # Clojure - no PyPI grammar
            ("app.dart", False),  # Dart - no PyPI grammar
            ("lib.nim", False),  # Nim - no PyPI grammar
            ("main.rb", True),  # Ruby - has grammar
            ("app.java", True),  # Java - has grammar
            ("main.c", True),  # C - has grammar
            ("main.cpp", True),  # C++ - has grammar
        ],
    )
    def test_various_languages(self, file_path: str, expected: bool) -> None:
        """Test various languages with and without grammars."""
        assert _has_grammar_for_file(file_path) is expected


# =============================================================================
# Tests for ExtractionResult.skipped_no_grammar
# =============================================================================


class TestExtractionResultSkippedFlag:
    """Tests for ExtractionResult.skipped_no_grammar flag."""

    def test_default_value_is_false(self) -> None:
        """Default value of skipped_no_grammar should be False."""
        result = ExtractionResult(file_path="test.py")
        assert result.skipped_no_grammar is False

    def test_can_be_set_to_true(self) -> None:
        """skipped_no_grammar can be set to True."""
        result = ExtractionResult(file_path="test.fs", skipped_no_grammar=True)
        assert result.skipped_no_grammar is True

    def test_skipped_result_has_no_error(self) -> None:
        """A skipped result should not have an error."""
        result = ExtractionResult(
            file_path="test.fs",
            skipped_no_grammar=True,
            error=None,
        )
        assert result.error is None
        assert result.skipped_no_grammar is True


# =============================================================================
# Tests for BatchResult.files_skipped_no_grammar
# =============================================================================


class TestBatchResultSkippedCounter:
    """Tests for BatchResult.files_skipped_no_grammar counter."""

    def test_default_value_is_zero(self) -> None:
        """Default value of files_skipped_no_grammar should be 0."""
        result = BatchResult()
        assert result.files_skipped_no_grammar == 0

    def test_can_be_incremented(self) -> None:
        """files_skipped_no_grammar can be incremented."""
        result = BatchResult()
        result.files_skipped_no_grammar += 1
        assert result.files_skipped_no_grammar == 1
        result.files_skipped_no_grammar += 2
        assert result.files_skipped_no_grammar == 3

    def test_separate_from_errors(self) -> None:
        """Skipped files are tracked separately from errors."""
        result = BatchResult(
            files_processed=5,
            errors=["error1"],
            files_skipped_no_grammar=2,
        )
        assert len(result.errors) == 1
        assert result.files_skipped_no_grammar == 2


# =============================================================================
# Tests for _extract_file with no-grammar files
# =============================================================================


class TestExtractFileNoGrammar:
    """Tests for _extract_file function with files lacking grammar support."""

    def test_fsharp_file_skipped_no_error(self, tmp_path: Path) -> None:
        """F# files should be skipped without error."""
        fs_file = tmp_path / "Program.fs"
        fs_file.write_text(
            """module Program

let main argv =
    printfn "Hello, World!"
    0
"""
        )

        result = _extract_file("Program.fs", str(tmp_path), unit_id=1)

        assert result.skipped_no_grammar is True
        assert result.error is None
        assert result.file_path == "Program.fs"

    def test_vbnet_file_skipped_no_error(self, tmp_path: Path) -> None:
        """VB.NET files should be skipped without error."""
        vb_file = tmp_path / "Program.vb"
        vb_file.write_text(
            """Module Program
    Sub Main(args As String())
        Console.WriteLine("Hello World!")
    End Sub
End Module
"""
        )

        result = _extract_file("Program.vb", str(tmp_path), unit_id=1)

        assert result.skipped_no_grammar is True
        assert result.error is None
        assert result.file_path == "Program.vb"

    def test_skipped_file_has_content_hash(self, tmp_path: Path) -> None:
        """Skipped files should still have content_hash computed."""
        fs_file = tmp_path / "Library.fs"
        content = "let x = 42\n"
        fs_file.write_text(content)

        result = _extract_file("Library.fs", str(tmp_path), unit_id=1)

        assert result.skipped_no_grammar is True
        assert result.content_hash is not None
        assert len(result.content_hash) == 64  # SHA-256 hex digest

    def test_skipped_file_has_line_count(self, tmp_path: Path) -> None:
        """Skipped files should still have line_count computed."""
        fs_file = tmp_path / "Library.fs"
        content = "line1\nline2\nline3\n"
        fs_file.write_text(content)

        result = _extract_file("Library.fs", str(tmp_path), unit_id=1)

        assert result.skipped_no_grammar is True
        assert result.line_count == 3

    def test_skipped_file_no_defs_extracted(self, tmp_path: Path) -> None:
        """Skipped files should have no defs extracted."""
        fs_file = tmp_path / "Library.fs"
        fs_file.write_text(
            """module Library

let add x y = x + y
let multiply x y = x * y
"""
        )

        result = _extract_file("Library.fs", str(tmp_path), unit_id=1)

        assert result.skipped_no_grammar is True
        assert result.defs == []

    def test_skipped_file_no_refs_extracted(self, tmp_path: Path) -> None:
        """Skipped files should have no refs extracted."""
        vb_file = tmp_path / "Module.vb"
        vb_file.write_text(
            """Module TestModule
    Dim x As Integer = 10
    Dim y As Integer = x + 5
End Module
"""
        )

        result = _extract_file("Module.vb", str(tmp_path), unit_id=1)

        assert result.skipped_no_grammar is True
        assert result.refs == []

    def test_skipped_file_no_scopes_extracted(self, tmp_path: Path) -> None:
        """Skipped files should have no scopes extracted."""
        erl_file = tmp_path / "server.erl"
        erl_file.write_text(
            """-module(server).
-export([start/0]).

start() ->
    io:format("Starting~n").
"""
        )

        result = _extract_file("server.erl", str(tmp_path), unit_id=1)

        assert result.skipped_no_grammar is True
        assert result.scopes == []

    def test_skipped_file_no_imports_extracted(self, tmp_path: Path) -> None:
        """Skipped files should have no imports extracted."""
        ps_file = tmp_path / "script.ps1"
        ps_file.write_text(
            """Import-Module ActiveDirectory
$users = Get-ADUser -Filter *
"""
        )

        result = _extract_file("script.ps1", str(tmp_path), unit_id=1)

        assert result.skipped_no_grammar is True
        assert result.imports == []

    def test_skipped_file_has_parse_time(self, tmp_path: Path) -> None:
        """Skipped files should still record parse time."""
        fs_file = tmp_path / "Quick.fs"
        fs_file.write_text("let x = 1\n")

        result = _extract_file("Quick.fs", str(tmp_path), unit_id=1)

        assert result.skipped_no_grammar is True
        assert result.parse_time_ms >= 0

    def test_python_file_not_skipped(self, tmp_path: Path) -> None:
        """Python files should NOT be skipped (has grammar)."""
        py_file = tmp_path / "module.py"
        py_file.write_text("def hello(): pass\n")

        result = _extract_file("module.py", str(tmp_path), unit_id=1)

        assert result.skipped_no_grammar is False
        assert result.error is None
        assert len(result.defs) > 0  # Should have extracted defs

    def test_missing_file_reports_error_not_skipped(self, tmp_path: Path) -> None:
        """Missing files should report error, not skipped_no_grammar."""
        result = _extract_file("nonexistent.fs", str(tmp_path), unit_id=1)

        assert result.error is not None
        assert "not found" in result.error.lower()
        # skipped_no_grammar should be False for errors
        assert result.skipped_no_grammar is False


# =============================================================================
# Tests for StructuralIndexer.index_files
# =============================================================================


class TestStructuralIndexerNoGrammar:
    """Tests for StructuralIndexer handling of no-grammar files."""

    @pytest.fixture
    def in_memory_db(self, tmp_path: Path) -> Database:
        """Create a test database for testing."""
        from codeplane.index._internal.db import Database, create_additional_indexes

        db_path = tmp_path / "test_no_grammar.db"
        db = Database(db_path)
        db.create_all()
        create_additional_indexes(db.engine)
        return db

    def _create_context(self, db: Database, root_path: Path) -> int:
        """Create a Context record and return its id."""
        from codeplane.index.models import Context

        with db.session() as session:
            ctx = Context(
                name="test",
                language_family="python",
                root_path=str(root_path),
            )
            session.add(ctx)
            session.commit()
            return ctx.id or 1

    def test_tracks_skipped_files_in_counter(self, tmp_path: Path, in_memory_db: Database) -> None:
        """Indexer should track skipped files in files_skipped_no_grammar."""
        # Create F# and VB.NET files
        (tmp_path / "Program.fs").write_text("let x = 1\n")
        (tmp_path / "Module.vb").write_text("Module M\nEnd Module\n")

        indexer = StructuralIndexer(in_memory_db, tmp_path)
        result = indexer.index_files(
            ["Program.fs", "Module.vb"],
            context_id=1,
        )

        assert result.files_skipped_no_grammar == 2
        assert result.files_processed == 2
        assert len(result.errors) == 0

    def test_skipped_files_not_reported_as_errors(
        self, tmp_path: Path, in_memory_db: Database
    ) -> None:
        """Skipped files should not be reported as errors."""
        (tmp_path / "script.ps1").write_text("Write-Host 'Hello'\n")
        (tmp_path / "main.erl").write_text("-module(main).\n")

        indexer = StructuralIndexer(in_memory_db, tmp_path)
        result = indexer.index_files(
            ["script.ps1", "main.erl"],
            context_id=1,
        )

        assert result.files_skipped_no_grammar == 2
        assert len(result.errors) == 0

    def test_processes_mixed_grammar_files(self, tmp_path: Path, in_memory_db: Database) -> None:
        """Indexer should process files with grammars alongside skipped files."""
        # Create Python file (has grammar) and F# file (no grammar)
        (tmp_path / "main.py").write_text("def greet(): pass\n")
        (tmp_path / "lib.fs").write_text("let add x y = x + y\n")

        # Create context so FK constraints are satisfied
        context_id = self._create_context(in_memory_db, tmp_path)

        indexer = StructuralIndexer(in_memory_db, tmp_path)
        result = indexer.index_files(
            ["main.py", "lib.fs"],
            context_id=context_id,
        )

        assert result.files_processed == 2
        assert result.files_skipped_no_grammar == 1
        assert result.defs_extracted > 0  # Python defs extracted
        assert len(result.errors) == 0


# =============================================================================
# Integration Tests
# =============================================================================


class TestNoGrammarIntegration:
    """Integration tests for no-grammar handling across the pipeline."""

    @pytest.fixture
    def in_memory_db(self, tmp_path: Path) -> Database:
        """Create a test database for testing."""
        from codeplane.index._internal.db import Database, create_additional_indexes

        db_path = tmp_path / "test_no_grammar.db"
        db = Database(db_path)
        db.create_all()
        create_additional_indexes(db.engine)
        return db

    def _create_context(self, db: Database, root_path: Path) -> int:
        """Create a Context record and return its id."""
        from codeplane.index.models import Context

        with db.session() as session:
            ctx = Context(
                name="test",
                language_family="python",
                root_path=str(root_path),
            )
            session.add(ctx)
            session.commit()
            return ctx.id or 1

    def test_mixed_python_and_fsharp_indexing(self, tmp_path: Path, in_memory_db: Database) -> None:
        """Test indexing a directory with both Python and F# files."""
        # Create Python file with meaningful content
        py_content = '''\
def calculate(x: int, y: int) -> int:
    """Calculate sum of two numbers."""
    return x + y


class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b
'''
        (tmp_path / "calculator.py").write_text(py_content)

        # Create F# file with meaningful content
        fs_content = """\
module Calculator

let add x y = x + y

type Calc() =
    member this.Add(a, b) = a + b
"""
        (tmp_path / "Calculator.fs").write_text(fs_content)

        # Create context so FK constraints are satisfied
        context_id = self._create_context(in_memory_db, tmp_path)

        indexer = StructuralIndexer(in_memory_db, tmp_path)
        result = indexer.index_files(
            ["calculator.py", "Calculator.fs"],
            context_id=context_id,
        )

        # Verify overall results
        assert result.files_processed == 2
        assert result.files_skipped_no_grammar == 1  # F# file
        assert len(result.errors) == 0

        # Verify Python file extracted structural facts
        assert result.defs_extracted >= 2  # At least 'calculate' and 'Calculator'
        assert result.refs_extracted > 0

    def test_all_no_grammar_files(self, tmp_path: Path, in_memory_db: Database) -> None:
        """Test batch with only no-grammar files."""
        (tmp_path / "prog.fs").write_text("let x = 1\n")
        (tmp_path / "mod.vb").write_text("Module M\nEnd Module\n")
        (tmp_path / "script.ps1").write_text("$x = 1\n")

        indexer = StructuralIndexer(in_memory_db, tmp_path)
        result = indexer.index_files(
            ["prog.fs", "mod.vb", "script.ps1"],
            context_id=1,
        )

        assert result.files_processed == 3
        assert result.files_skipped_no_grammar == 3
        assert result.defs_extracted == 0
        assert result.refs_extracted == 0
        assert len(result.errors) == 0

    def test_language_detection_consistency(self) -> None:
        """Verify language detection is consistent with grammar availability."""
        # Languages WITH grammars
        for ext, expected_family in [
            (".py", "python"),
            (".js", "javascript"),
            (".go", "go"),
            (".rs", "rust"),
            (".java", "java"),
            (".rb", "ruby"),
        ]:
            family = detect_language_family(f"test{ext}")
            assert family == expected_family, f"Expected {expected_family} for {ext}"
            assert has_grammar(family), f"{family} should have grammar"

        # Languages WITHOUT grammars
        for ext, expected_family in [
            (".fs", "fsharp"),
            (".vb", "vbnet"),
            (".erl", "erlang"),
            (".ps1", "powershell"),
            (".clj", "clojure"),
            (".dart", "dart"),
        ]:
            family = detect_language_family(f"test{ext}")
            assert family == expected_family, f"Expected {expected_family} for {ext}"
            assert not has_grammar(family), f"{family} should NOT have grammar"

    def test_extract_single_no_grammar(self, tmp_path: Path, in_memory_db: Database) -> None:
        """Test extract_single method with no-grammar file."""
        (tmp_path / "lib.fs").write_text("let double x = x * 2\n")

        indexer = StructuralIndexer(in_memory_db, tmp_path)
        result = indexer.extract_single("lib.fs", unit_id=1)

        assert result.skipped_no_grammar is True
        assert result.error is None
        assert result.content_hash is not None
        assert result.line_count == 1
        assert result.defs == []
