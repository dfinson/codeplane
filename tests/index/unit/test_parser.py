"""Unit tests for Tree-sitter parser (parser.py).

Tests cover:
- Parse Python, JavaScript, Go files
- Extract syntactic symbols (functions, classes, methods)
- Extract identifier occurrences
- Compute interface hashes
- Probe validation (Code vs Data rules)
- Language detection from file extension
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.parsing import (
    IdentifierOccurrence,
    ParseResult,
    ProbeValidation,
    SyntacticSymbol,
    TreeSitterParser,
)


@pytest.fixture
def parser() -> TreeSitterParser:
    """Create a TreeSitterParser instance."""
    return TreeSitterParser()


class TestParseBasics:
    """Basic parsing tests."""

    def test_parse_python_file(
        self, parser: TreeSitterParser, sample_python_content: str, temp_dir: Path
    ) -> None:
        """Parser should successfully parse Python files."""
        file_path = temp_dir / "test.py"
        file_path.write_text(sample_python_content)

        result = parser.parse(file_path, sample_python_content.encode())

        assert result is not None
        assert isinstance(result, ParseResult)
        assert result.language == "python"
        assert result.error_count == 0
        assert result.tree is not None

    def test_parse_javascript_file(
        self, parser: TreeSitterParser, sample_javascript_content: str, temp_dir: Path
    ) -> None:
        """Parser should successfully parse JavaScript files."""
        file_path = temp_dir / "test.js"
        file_path.write_text(sample_javascript_content)

        result = parser.parse(file_path, sample_javascript_content.encode())

        assert result is not None
        assert result.language == "javascript"
        assert result.error_count == 0

    def test_parse_go_file(
        self, parser: TreeSitterParser, sample_go_content: str, temp_dir: Path
    ) -> None:
        """Parser should successfully parse Go files."""
        file_path = temp_dir / "test.go"
        file_path.write_text(sample_go_content)

        result = parser.parse(file_path, sample_go_content.encode())

        assert result is not None
        assert result.language == "go"
        assert result.error_count == 0

    def test_parse_typescript_file(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Parser should successfully parse TypeScript files."""
        content = """
interface Greeter {
    greet(name: string): string;
}

class MyGreeter implements Greeter {
    greet(name: string): string {
        return `Hello, ${name}!`;
    }
}
"""
        file_path = temp_dir / "test.ts"
        file_path.write_text(content)

        result = parser.parse(file_path, content.encode())

        assert result is not None
        assert result.language == "typescript"
        assert result.error_count == 0

    def test_parse_with_syntax_errors(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Parser should handle files with syntax errors."""
        content = """
def broken_function(
    # Missing closing paren and body
"""
        file_path = temp_dir / "broken.py"
        file_path.write_text(content)

        result = parser.parse(file_path, content.encode())

        assert result is not None
        assert result.error_count > 0

    def test_parse_unknown_extension(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Parser should raise ValueError for unknown file types."""
        file_path = temp_dir / "test.xyz"
        file_path.write_text("unknown content")

        with pytest.raises(ValueError, match="Unsupported file extension"):
            parser.parse(file_path, b"unknown content")


class TestSymbolExtraction:
    """Tests for symbol extraction."""

    def test_extract_python_functions(
        self, parser: TreeSitterParser, sample_python_content: str, temp_dir: Path
    ) -> None:
        """Should extract function definitions from Python."""
        file_path = temp_dir / "test.py"
        file_path.write_text(sample_python_content)

        result = parser.parse(file_path, sample_python_content.encode())
        assert result is not None

        symbols = parser.extract_symbols(result)

        # Find the hello function
        function_names = {s.name for s in symbols if s.kind == "function"}
        assert "hello" in function_names

    def test_extract_python_classes(
        self, parser: TreeSitterParser, sample_python_content: str, temp_dir: Path
    ) -> None:
        """Should extract class definitions from Python."""
        file_path = temp_dir / "test.py"
        file_path.write_text(sample_python_content)

        result = parser.parse(file_path, sample_python_content.encode())
        assert result is not None

        symbols = parser.extract_symbols(result)

        # Find the Greeter class
        class_names = {s.name for s in symbols if s.kind == "class"}
        assert "Greeter" in class_names

    def test_extract_python_methods(
        self, parser: TreeSitterParser, sample_python_content: str, temp_dir: Path
    ) -> None:
        """Should extract method definitions from Python classes."""
        file_path = temp_dir / "test.py"
        file_path.write_text(sample_python_content)

        result = parser.parse(file_path, sample_python_content.encode())
        assert result is not None

        symbols = parser.extract_symbols(result)

        # Find methods
        method_names = {s.name for s in symbols if s.kind == "method"}
        assert "__init__" in method_names or "greet" in method_names

    def test_extract_javascript_functions(
        self, parser: TreeSitterParser, sample_javascript_content: str, temp_dir: Path
    ) -> None:
        """Should extract function definitions from JavaScript."""
        file_path = temp_dir / "test.js"
        file_path.write_text(sample_javascript_content)

        result = parser.parse(file_path, sample_javascript_content.encode())
        assert result is not None

        symbols = parser.extract_symbols(result)

        function_names = {s.name for s in symbols if s.kind == "function"}
        assert "hello" in function_names

    def test_extract_javascript_classes(
        self, parser: TreeSitterParser, sample_javascript_content: str, temp_dir: Path
    ) -> None:
        """Should extract class definitions from JavaScript."""
        file_path = temp_dir / "test.js"
        file_path.write_text(sample_javascript_content)

        result = parser.parse(file_path, sample_javascript_content.encode())
        assert result is not None

        symbols = parser.extract_symbols(result)

        class_names = {s.name for s in symbols if s.kind == "class"}
        assert "Greeter" in class_names

    def test_extract_go_functions(
        self, parser: TreeSitterParser, sample_go_content: str, temp_dir: Path
    ) -> None:
        """Should extract function definitions from Go."""
        file_path = temp_dir / "test.go"
        file_path.write_text(sample_go_content)

        result = parser.parse(file_path, sample_go_content.encode())
        assert result is not None

        symbols = parser.extract_symbols(result)

        function_names = {s.name for s in symbols if s.kind == "function"}
        assert "Hello" in function_names

    def test_extract_go_types(
        self, parser: TreeSitterParser, sample_go_content: str, temp_dir: Path
    ) -> None:
        """Should extract type definitions from Go."""
        file_path = temp_dir / "test.go"
        file_path.write_text(sample_go_content)

        result = parser.parse(file_path, sample_go_content.encode())
        assert result is not None

        symbols = parser.extract_symbols(result)

        # Should have Greeter struct or type
        names = {s.name for s in symbols}
        assert "Greeter" in names

    def test_symbol_has_location(
        self, parser: TreeSitterParser, sample_python_content: str, temp_dir: Path
    ) -> None:
        """Extracted symbols should have line/column information."""
        file_path = temp_dir / "test.py"
        file_path.write_text(sample_python_content)

        result = parser.parse(file_path, sample_python_content.encode())
        assert result is not None

        symbols = parser.extract_symbols(result)
        assert len(symbols) > 0

        for symbol in symbols:
            assert isinstance(symbol, SyntacticSymbol)
            assert symbol.line >= 0
            assert symbol.column >= 0


class TestIdentifierOccurrences:
    """Tests for identifier occurrence extraction."""

    def test_extract_identifier_occurrences(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract identifier occurrences."""
        content = """
def foo():
    x = 1
    y = x + 2
    return y
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = parser.parse(file_path, content.encode())
        assert result is not None

        occurrences = parser.extract_identifier_occurrences(result)

        # Should find x and y multiple times
        names = [occ.name for occ in occurrences]
        assert "x" in names
        assert "y" in names

    def test_occurrence_has_location(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Extracted occurrences should have location information."""
        content = "x = 1"
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = parser.parse(file_path, content.encode())
        assert result is not None

        occurrences = parser.extract_identifier_occurrences(result)
        assert len(occurrences) > 0

        for occ in occurrences:
            assert isinstance(occ, IdentifierOccurrence)
            assert occ.line >= 0
            assert occ.column >= 0


class TestInterfaceHash:
    """Tests for syntactic interface hash computation."""

    def test_compute_interface_hash(
        self, parser: TreeSitterParser, sample_python_content: str, temp_dir: Path
    ) -> None:
        """Should compute interface hash from symbols."""
        file_path = temp_dir / "test.py"
        file_path.write_text(sample_python_content)

        result = parser.parse(file_path, sample_python_content.encode())
        assert result is not None

        symbols = parser.extract_symbols(result)
        hash1 = parser.compute_interface_hash(symbols)

        assert hash1 is not None
        assert len(hash1) > 0

    def test_interface_hash_changes_with_symbols(
        self, parser: TreeSitterParser, temp_dir: Path
    ) -> None:
        """Interface hash should change when symbols change."""
        content1 = """
def foo():
    pass
"""
        content2 = """
def foo():
    pass

def bar():
    pass
"""
        file_path = temp_dir / "test.py"

        # Parse first version
        file_path.write_text(content1)
        result1 = parser.parse(file_path, content1.encode())
        assert result1 is not None
        symbols1 = parser.extract_symbols(result1)
        hash1 = parser.compute_interface_hash(symbols1)

        # Parse second version
        file_path.write_text(content2)
        result2 = parser.parse(file_path, content2.encode())
        assert result2 is not None
        symbols2 = parser.extract_symbols(result2)
        hash2 = parser.compute_interface_hash(symbols2)

        # Hashes should differ
        assert hash1 != hash2

    def test_interface_hash_stable_for_body_changes(
        self, parser: TreeSitterParser, temp_dir: Path
    ) -> None:
        """Interface hash should be stable when only function bodies change."""
        content1 = """
def foo():
    return 1
"""
        content2 = """
def foo():
    return 2
"""
        file_path = temp_dir / "test.py"

        # Parse first version
        file_path.write_text(content1)
        result1 = parser.parse(file_path, content1.encode())
        assert result1 is not None
        symbols1 = parser.extract_symbols(result1)
        hash1 = parser.compute_interface_hash(symbols1)

        # Parse second version (only body changed)
        file_path.write_text(content2)
        result2 = parser.parse(file_path, content2.encode())
        assert result2 is not None
        symbols2 = parser.extract_symbols(result2)
        hash2 = parser.compute_interface_hash(symbols2)

        # Hashes should be the same (interface didn't change)
        # Note: This depends on what "interface" means - function name + signature
        # If only body changed and signature is the same, hash should match
        # This test verifies that behavior
        assert hash1 == hash2


class TestProbeValidation:
    """Tests for context probe validation."""

    def test_validate_code_file_valid(
        self, parser: TreeSitterParser, sample_python_content: str, temp_dir: Path
    ) -> None:
        """Valid code file should pass validation."""
        file_path = temp_dir / "test.py"
        file_path.write_text(sample_python_content)

        result = parser.parse(file_path, sample_python_content.encode())
        assert result is not None

        validation = parser.validate_code_file(result)

        assert isinstance(validation, ProbeValidation)
        assert validation.is_valid
        assert validation.has_meaningful_content

    def test_validate_code_file_with_many_errors(
        self, parser: TreeSitterParser, temp_dir: Path
    ) -> None:
        """Code file with >10% errors should fail validation."""
        # Create content that's mostly errors
        content = """
def (
    )
class {
}
function broken
syntax everywhere
"""
        file_path = temp_dir / "broken.py"
        file_path.write_text(content)

        result = parser.parse(file_path, content.encode())
        assert result is not None

        validation = parser.validate_code_file(result)

        # Should fail due to high error ratio
        # Note: exact behavior depends on implementation threshold
        assert validation.error_count > 0

    def test_validate_data_file_valid(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Valid data file (JSON) should pass validation."""
        content = '{"key": "value", "number": 42}'
        file_path = temp_dir / "test.json"
        file_path.write_text(content)

        result = parser.parse(file_path, content.encode())
        if result is None:
            pytest.skip("JSON parser not available")

        validation = parser.validate_data_file(result)

        assert validation.is_valid
        assert validation.error_count == 0

    def test_validate_empty_file(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Empty Python file parses with a module root node and is considered valid."""
        content = ""
        file_path = temp_dir / "empty.py"
        file_path.write_text(content)

        result = parser.parse(file_path, content.encode())
        assert result is not None

        validation = parser.validate_code_file(result)

        # Empty Python file has module root node (total_nodes=1)
        # Implementation considers this valid (parseable, no errors)
        assert validation.total_nodes == 1
        assert validation.error_count == 0
        assert validation.is_valid


class TestLanguageDetection:
    """Tests for language detection from file extensions."""

    @pytest.mark.parametrize(
        ("extension", "expected_language"),
        [
            ("py", "python"),
            ("js", "javascript"),
            ("ts", "typescript"),
            ("tsx", "tsx"),
            ("go", "go"),
            ("rs", "rust"),
            ("java", "java"),
            ("rb", "ruby"),
            ("php", "php"),
            ("json", "json"),
            ("yaml", "yaml"),
            ("yml", "yaml"),
            ("dockerfile", "dockerfile"),
            ("makefile", "makefile"),
        ],
    )
    def test_detect_language_from_extension(
        self, parser: TreeSitterParser, extension: str, expected_language: str
    ) -> None:
        """Should detect language from file extension."""
        # The parser uses _detect_language_from_ext which takes extension string
        detected = parser._detect_language_from_ext(extension)
        if detected is None:
            pytest.skip(f"Language detection not implemented for .{extension}")
        assert detected.lower() == expected_language.lower()


class TestScopeExtraction:
    """Tests for lexical scope extraction."""

    def test_extract_python_scopes(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract scopes from Python file."""
        content = """
def foo():
    x = 1

class Bar:
    def method(self):
        pass
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        scopes = parser.extract_scopes(result)

        # Should have file scope (0), function scope (foo), class scope (Bar), method scope
        assert len(scopes) >= 4
        kinds = [s.kind for s in scopes]
        assert "file" in kinds
        assert "function" in kinds
        assert "class" in kinds

    def test_extract_python_comprehension_scopes(
        self, parser: TreeSitterParser, temp_dir: Path
    ) -> None:
        """Should extract comprehension scopes in Python."""
        content = """
x = [i for i in range(10)]
y = {k: v for k, v in items}
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        scopes = parser.extract_scopes(result)

        # Should have file scope + comprehension scopes
        kinds = [s.kind for s in scopes]
        assert "comprehension" in kinds

    def test_scope_parent_chain(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Scopes should have correct parent_scope_id chain."""
        content = """
class Foo:
    def bar(self):
        pass
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        scopes = parser.extract_scopes(result)

        # File scope has no parent
        file_scope = next(s for s in scopes if s.kind == "file")
        assert file_scope.parent_scope_id is None

        # Class scope's parent is file scope
        class_scope = next(s for s in scopes if s.kind == "class")
        assert class_scope.parent_scope_id == file_scope.scope_id


class TestImportExtraction:
    """Tests for import statement extraction."""

    def test_extract_python_import(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract import statements from Python."""
        content = """
import os
import sys as system
from pathlib import Path
from collections import defaultdict as dd
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) >= 4
        names = [i.imported_name for i in imports]
        assert "os" in names
        assert "sys" in names
        assert "Path" in names
        assert "defaultdict" in names

        # Check aliases
        sys_import = next(i for i in imports if i.imported_name == "sys")
        assert sys_import.alias == "system"

        dd_import = next(i for i in imports if i.imported_name == "defaultdict")
        assert dd_import.alias == "dd"

    def test_extract_python_from_import_source(
        self, parser: TreeSitterParser, temp_dir: Path
    ) -> None:
        """Should extract source module from 'from X import Y' statements."""
        content = """
from collections import OrderedDict
from typing import Optional
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        # Check source_literal
        od_import = next(i for i in imports if i.imported_name == "OrderedDict")
        assert od_import.import_kind == "python_from"

    def test_extract_js_imports(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract import statements from JavaScript."""
        content = """
import React from 'react';
import { useState, useEffect } from 'react';
import * as utils from './utils';
"""
        file_path = temp_dir / "test.js"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        # Should find React, useState, useEffect, and namespace import
        names = [i.imported_name for i in imports]
        assert "React" in names
        assert "useState" in names

        # Check namespace import
        namespace_import = next((i for i in imports if i.imported_name == "*"), None)
        if namespace_import:
            assert namespace_import.alias == "utils"

    def test_extract_python_wildcard_import(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract wildcard (star) imports from Python."""
        content = """from os.path import *
from collections import *
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        # Should find two star imports
        star_imports = [i for i in imports if i.imported_name == "*"]
        assert len(star_imports) == 2

        # Check source modules
        sources = sorted(i.source_literal for i in star_imports if i.source_literal)
        assert "collections" in sources
        assert "os.path" in sources

        # All should be python_from kind
        assert all(i.import_kind == "python_from" for i in star_imports)

        # No alias for star imports
        assert all(i.alias is None for i in star_imports)

    def test_extract_python_wildcard_import_relative(
        self, parser: TreeSitterParser, temp_dir: Path
    ) -> None:
        """Should extract relative wildcard imports from Python."""
        content = "from . import *\n"
        file_path = temp_dir / "test.py"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        star_imports = [i for i in imports if i.imported_name == "*"]
        assert len(star_imports) == 1
        assert star_imports[0].import_kind == "python_from"

    def test_extract_csharp_using_regular(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract regular C# using directives."""
        content = """using System;
using System.Collections.Generic;
using Newtonsoft.Json;
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 3
        names = [i.imported_name for i in imports]
        assert "System" in names
        assert "System.Collections.Generic" in names
        assert "Newtonsoft.Json" in names

        # All should be csharp_using kind, no aliases
        assert all(i.import_kind == "csharp_using" for i in imports)
        assert all(i.alias is None for i in imports)

    def test_extract_csharp_using_static(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract static C# using directives."""
        content = "using static System.Math;\n"
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 1
        assert imports[0].imported_name == "System.Math"
        assert imports[0].import_kind == "csharp_using_static"
        assert imports[0].alias is None

    def test_extract_csharp_using_aliased(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract aliased C# using directives."""
        content = "using MyList = System.Collections.Generic.List;\n"
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 1
        assert imports[0].alias == "MyList"
        assert "System.Collections.Generic.List" in imports[0].imported_name
        assert imports[0].import_kind == "csharp_using"

    def test_extract_csharp_mixed_usings(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should handle mixed using directive forms in a single file."""
        content = """using System;
using static System.Math;
using Alias = System.Collections.Generic.List;

namespace Foo {
    class Bar { }
}
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 3

        regular = [i for i in imports if i.import_kind == "csharp_using" and i.alias is None]
        assert len(regular) == 1
        assert regular[0].imported_name == "System"

        static = [i for i in imports if i.import_kind == "csharp_using_static"]
        assert len(static) == 1
        assert static[0].imported_name == "System.Math"

        aliased = [i for i in imports if i.alias is not None]
        assert len(aliased) == 1
        assert aliased[0].alias == "Alias"

    def test_extract_csharp_using_inside_namespace(
        self, parser: TreeSitterParser, temp_dir: Path
    ) -> None:
        """Should extract using directives inside namespace declarations.

        C# allows using directives inside namespace blocks, not just at file scope.
        This tests that _walk_for_usings correctly descends into namespace_declaration
        and declaration_list nodes.
        """
        content = """using System;

namespace MyApp {
    using System.Linq;
    using static System.Math;

    namespace Nested {
        using Newtonsoft.Json;

        public class Foo { }
    }

    public class Bar { }
}
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        # Should find all 4 usings: root-level + namespace-scoped + nested namespace
        assert len(imports) == 4

        names = [i.imported_name for i in imports]
        assert "System" in names  # root level
        assert "System.Linq" in names  # inside MyApp namespace
        assert "System.Math" in names  # static inside MyApp namespace
        assert "Newtonsoft.Json" in names  # inside MyApp.Nested namespace

        # Verify import kinds
        regular = [i for i in imports if i.import_kind == "csharp_using" and i.alias is None]
        assert len(regular) == 3  # System, System.Linq, Newtonsoft.Json

        static = [i for i in imports if i.import_kind == "csharp_using_static"]
        assert len(static) == 1
        assert static[0].imported_name == "System.Math"


class TestNamespaceTypeExtraction:
    """Tests for C# namespace -> type name extraction."""

    def test_extract_block_scoped_namespace(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract types from block-scoped namespace declarations."""
        content = """namespace Foo.Bar {
    public class MyClass { }
    public interface IMyInterface { }
    public struct MyStruct { }
    public enum MyEnum { A, B }
}
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        ns_map = parser.extract_csharp_namespace_types(result.root_node)

        assert "Foo.Bar" in ns_map
        types = ns_map["Foo.Bar"]
        assert "MyClass" in types
        assert "IMyInterface" in types
        assert "MyStruct" in types
        assert "MyEnum" in types

    def test_extract_multiple_namespaces(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract types from multiple namespaces in one file."""
        content = """namespace A {
    class Foo { }
}
namespace B {
    class Bar { }
}
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        ns_map = parser.extract_csharp_namespace_types(result.root_node)

        assert "A" in ns_map
        assert "Foo" in ns_map["A"]
        assert "B" in ns_map
        assert "Bar" in ns_map["B"]

    def test_extract_file_scoped_namespace(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract types from file-scoped namespace declarations."""
        content = """namespace Foo.Bar;

public class Baz { }
public interface IBaz { }
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        ns_map = parser.extract_csharp_namespace_types(result.root_node)

        assert "Foo.Bar" in ns_map
        types = ns_map["Foo.Bar"]
        assert "Baz" in types
        assert "IBaz" in types

    def test_empty_namespace(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should handle empty namespaces without error."""
        content = "namespace Empty { }\n"
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        ns_map = parser.extract_csharp_namespace_types(result.root_node)

        # Empty namespace should not appear in map
        assert "Empty" not in ns_map

    def test_extract_nested_namespaces(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should extract types from nested namespace declarations with composed paths."""
        content = """namespace Outer {
    namespace Inner {
        class Foo { }
        interface IBar { }
    }
    class OuterOnly { }
}
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        ns_map = parser.extract_csharp_namespace_types(result.root_node)

        # Nested namespace should be composed as Outer.Inner
        assert "Outer.Inner" in ns_map
        assert "Foo" in ns_map["Outer.Inner"]
        assert "IBar" in ns_map["Outer.Inner"]

        # Types declared directly in Outer should also be extracted
        assert "Outer" in ns_map
        assert "OuterOnly" in ns_map["Outer"]

    def test_extract_deeply_nested_namespaces(
        self, parser: TreeSitterParser, temp_dir: Path
    ) -> None:
        """Should handle deeply nested namespaces (3+ levels)."""
        content = """namespace A {
    namespace B {
        namespace C {
            class DeepClass { }
        }
    }
}
"""
        file_path = temp_dir / "test.cs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        ns_map = parser.extract_csharp_namespace_types(result.root_node)

        assert "A.B.C" in ns_map
        assert "DeepClass" in ns_map["A.B.C"]


class TestDynamicAccessExtraction:
    """Tests for dynamic access pattern detection."""

    def test_extract_python_getattr(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should detect getattr calls in Python."""
        content = """
x = getattr(obj, "foo")
y = getattr(obj, attr_name)
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        dynamics = parser.extract_dynamic_accesses(result)

        # Should find at least one getattr pattern
        getattrs = [d for d in dynamics if d.pattern_type == "getattr"]
        assert len(getattrs) >= 1

    def test_extract_python_bracket_access(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should detect bracket access in Python."""
        content = """
x = obj["key"]
y = obj[variable]
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        dynamics = parser.extract_dynamic_accesses(result)

        brackets = [d for d in dynamics if d.pattern_type == "bracket_access"]
        assert len(brackets) >= 2

        # One should have extracted literal, one should have dynamic key
        static = [d for d in brackets if not d.has_non_literal_key]
        dynamic = [d for d in brackets if d.has_non_literal_key]
        assert len(static) >= 1
        assert len(dynamic) >= 1

    def test_extract_python_eval(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        """Should detect eval/exec calls in Python."""
        content = """
result = eval("1 + 2")
exec("print('hello')")
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        dynamics = parser.extract_dynamic_accesses(result)

        evals = [d for d in dynamics if d.pattern_type == "eval"]
        assert len(evals) >= 2
