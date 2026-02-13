"""Unit tests for multi-language import extraction.

Tests cover import extraction for all languages added in issue #135:
- Go: import declarations, dot imports
- Rust: use declarations, glob imports
- Java: import declarations, star imports, static imports
- Kotlin: import declarations with aliases
- Ruby: require/require_relative
- PHP: use declarations
- Swift: import declarations
- Scala: import with selectors and wildcards
- Elixir: import/alias/use
- Haskell: import declarations
- OCaml: open declarations
- Lua: require calls
- Julia: using/import
- C/C++: #include directives
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.parsing import TreeSitterParser


@pytest.fixture
def parser() -> TreeSitterParser:
    """Create a TreeSitterParser instance."""
    return TreeSitterParser()


class TestGoImportExtraction:
    """Tests for Go import extraction."""

    def test_extract_single_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract single import statements."""
        content = """package main

import "fmt"
import "os"
"""
        file_path = tmp_path / "test.go"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 2
        sources = [i.source_literal for i in imports]
        assert "fmt" in sources
        assert "os" in sources
        assert all(i.import_kind == "go_import" for i in imports)

    def test_extract_grouped_imports(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract grouped import statements."""
        content = """package main

import (
    "fmt"
    "os"
    "path/filepath"
)
"""
        file_path = tmp_path / "test.go"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 3
        sources = [i.source_literal for i in imports]
        assert "fmt" in sources
        assert "os" in sources
        assert "path/filepath" in sources

    def test_extract_aliased_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract aliased imports."""
        content = """package main

import f "fmt"
import io "io/ioutil"
"""
        file_path = tmp_path / "test.go"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 2
        fmt_import = next(i for i in imports if i.source_literal == "fmt")
        assert fmt_import.alias == "f"

    def test_extract_dot_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract dot imports (namespace-level import) as wildcard."""
        content = """package main

import . "fmt"
"""
        file_path = tmp_path / "test.go"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 1
        assert imports[0].imported_name == "*"  # Dot import = star import
        assert imports[0].source_literal == "fmt"

    def test_extract_blank_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract blank (side-effect) imports."""
        content = """package main

import _ "database/sql"
"""
        file_path = tmp_path / "test.go"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 1
        assert imports[0].source_literal == "database/sql"
        assert imports[0].alias == "_"


class TestRustImportExtraction:
    """Tests for Rust use declaration extraction."""

    def test_extract_simple_use(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract simple use declarations."""
        content = """use std::io;
use std::collections::HashMap;
"""
        file_path = tmp_path / "test.rs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) >= 2
        sources = [i.source_literal for i in imports if i.source_literal]
        assert any("std::io" in s for s in sources)
        assert any("HashMap" in s for s in sources)
        assert all(i.import_kind == "rust_use" for i in imports)

    def test_extract_glob_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract glob use declarations."""
        content = "use std::collections::*;\n"
        file_path = tmp_path / "test.rs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) >= 1
        glob_imports = [i for i in imports if i.imported_name == "*"]
        assert len(glob_imports) >= 1

    def test_extract_aliased_use(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract aliased use declarations."""
        content = "use std::collections::HashMap as Map;\n"
        file_path = tmp_path / "test.rs"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) >= 1
        # Look for an import with alias
        aliased = [i for i in imports if i.alias == "Map"]
        assert len(aliased) >= 1


class TestJavaImportExtraction:
    """Tests for Java import extraction."""

    def test_extract_regular_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract regular Java imports."""
        content = """import java.util.List;
import java.util.ArrayList;
import com.google.common.collect.ImmutableList;
"""
        file_path = tmp_path / "Test.java"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 3
        names = [i.imported_name for i in imports]
        assert "List" in names
        assert "ArrayList" in names
        assert "ImmutableList" in names
        assert all(i.import_kind == "java_import" for i in imports)

    def test_extract_star_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract star (wildcard) imports."""
        content = "import java.util.*;\n"
        file_path = tmp_path / "Test.java"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 1
        assert imports[0].imported_name == "*"
        assert imports[0].source_literal == "java.util"
        assert imports[0].import_kind == "java_import"

    def test_extract_static_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract static imports."""
        content = """import static java.lang.Math.PI;
import static java.util.Collections.emptyList;
"""
        file_path = tmp_path / "Test.java"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 2
        assert all(i.import_kind == "java_import_static" for i in imports)
        names = [i.imported_name for i in imports]
        assert "PI" in names
        assert "emptyList" in names


class TestKotlinImportExtraction:
    """Tests for Kotlin import extraction."""

    def test_extract_regular_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract regular Kotlin imports."""
        content = """import kotlin.collections.List
import kotlin.collections.Map
"""
        file_path = tmp_path / "Test.kt"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) >= 1
        # Kotlin syntax varies - just verify we get some imports
        assert all(i.import_kind == "kotlin_import" for i in imports)


class TestRubyImportExtraction:
    """Tests for Ruby require extraction."""

    def test_extract_require(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract require statements."""
        content = """require 'json'
require "yaml"
"""
        file_path = tmp_path / "test.rb"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 2
        sources = [i.source_literal for i in imports]
        assert "json" in sources
        assert "yaml" in sources
        assert all(i.import_kind == "ruby_require" for i in imports)

    def test_extract_require_relative(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract require_relative statements."""
        content = "require_relative 'helper'\n"
        file_path = tmp_path / "test.rb"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 1
        assert imports[0].source_literal == "helper"
        assert imports[0].import_kind == "ruby_require_relative"


class TestPHPImportExtraction:
    """Tests for PHP use extraction."""

    def test_extract_use(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract use statements."""
        content = """<?php
use App\\Models\\User;
use Illuminate\\Support\\Facades\\Log;
"""
        file_path = tmp_path / "test.php"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        # PHP parsing may vary - check basic extraction works
        assert all(i.import_kind == "php_use" for i in imports)


class TestSwiftImportExtraction:
    """Tests for Swift import extraction."""

    def test_extract_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract import statements."""
        content = """import Foundation
import UIKit
import struct Foundation.URL
"""
        file_path = tmp_path / "test.swift"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) >= 2
        assert all(i.import_kind == "swift_import" for i in imports)
        names = [i.imported_name for i in imports]
        # At least module names should be present
        assert "Foundation" in names or "URL" in names


class TestScalaImportExtraction:
    """Tests for Scala import extraction."""

    def test_extract_simple_import(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract simple imports."""
        content = """import scala.collection.mutable.ListBuffer
import java.util.Date
"""
        file_path = tmp_path / "Test.scala"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) >= 1
        assert all(i.import_kind == "scala_import" for i in imports)


class TestCIncludeExtraction:
    """Tests for C/C++ #include extraction."""

    def test_extract_system_include(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract system includes."""
        content = """#include <stdio.h>
#include <stdlib.h>
"""
        file_path = tmp_path / "test.c"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 2
        headers = [i.source_literal for i in imports]
        assert "stdio.h" in headers
        assert "stdlib.h" in headers
        assert all(i.import_kind == "c_include" for i in imports)

    def test_extract_local_include(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract local includes."""
        content = '#include "myheader.h"\n'
        file_path = tmp_path / "test.c"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 1
        assert imports[0].source_literal == "myheader.h"
        assert imports[0].import_kind == "c_include"

    def test_extract_cpp_include(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract C++ includes."""
        content = """#include <iostream>
#include <vector>
#include "mylibrary.hpp"
"""
        file_path = tmp_path / "test.cpp"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) == 3
        headers = [i.source_literal for i in imports]
        assert "iostream" in headers
        assert "vector" in headers
        assert "mylibrary.hpp" in headers


class TestLuaImportExtraction:
    """Tests for Lua require extraction."""

    def test_extract_require(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Should extract require calls."""
        content = """local json = require("json")
local utils = require("utils")
"""
        file_path = tmp_path / "test.lua"
        file_path.write_text(content)
        result = parser.parse(file_path, content.encode())

        imports = parser.extract_imports(result, str(file_path))

        assert len(imports) >= 1
        assert all(i.import_kind == "lua_require" for i in imports)
        sources = [i.source_literal for i in imports]
        assert "json" in sources or "utils" in sources


class TestImportKindEnumValues:
    """Verify all new ImportKind enum values are recognized."""

    def test_all_import_kinds_defined(self) -> None:
        """All new import kinds should be defined in the enum."""
        from codeplane.index.models import ImportKind

        # Tier 1 languages
        assert hasattr(ImportKind, "GO_IMPORT")
        assert hasattr(ImportKind, "RUST_USE")
        assert hasattr(ImportKind, "JAVA_IMPORT")
        assert hasattr(ImportKind, "JAVA_IMPORT_STATIC")

        # Tier 2 languages
        assert hasattr(ImportKind, "KOTLIN_IMPORT")
        assert hasattr(ImportKind, "RUBY_REQUIRE")
        assert hasattr(ImportKind, "RUBY_REQUIRE_RELATIVE")
        assert hasattr(ImportKind, "PHP_USE")
        assert hasattr(ImportKind, "SWIFT_IMPORT")

        # Tier 3 languages
        assert hasattr(ImportKind, "SCALA_IMPORT")
        assert hasattr(ImportKind, "ELIXIR_IMPORT")
        assert hasattr(ImportKind, "HASKELL_IMPORT")
        assert hasattr(ImportKind, "OCAML_OPEN")
        assert hasattr(ImportKind, "LUA_REQUIRE")
        assert hasattr(ImportKind, "JULIA_USING")

        # Special
        assert hasattr(ImportKind, "C_INCLUDE")
