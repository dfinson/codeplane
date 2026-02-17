"""Unit tests for multi-language symbol extraction.

Tests cover symbol extraction for all 13 languages with specialized extractors:
- Java, C#, Kotlin, Scala, PHP, Ruby, C/C++, Swift, Elixir, Haskell,
  OCaml, Julia, Lua
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.parsing import TreeSitterParser


@pytest.fixture
def parser() -> TreeSitterParser:
    """Create a TreeSitterParser instance."""
    return TreeSitterParser()


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------


class TestJavaSymbolExtraction:
    """Tests for Java symbol extraction."""

    def test_class_and_method(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"class Foo {\n    void bar(int x) {}\n    int baz() { return 1; }\n}"
        result = parser.parse(tmp_path / "Foo.java", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "class") in nk
        assert ("bar", "method") in nk
        assert ("baz", "method") in nk

    def test_interface_and_enum(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"interface Greeter { void greet(); }\nenum Color { RED, GREEN, BLUE }"
        result = parser.parse(tmp_path / "Greeter.java", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Greeter", "interface") in nk
        assert ("Color", "enum") in nk
        assert ("RED", "enum_constant") in nk
        assert ("GREEN", "enum_constant") in nk

    def test_record(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"record Point(int x, int y) {}"
        result = parser.parse(tmp_path / "Point.java", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Point", "record") in nk

    def test_parent_name_tracking(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"class Foo {\n    void bar() {}\n}"
        result = parser.parse(tmp_path / "Foo.java", content)
        symbols = parser.extract_symbols(result)
        method = next(s for s in symbols if s.name == "bar")
        assert method.parent_name == "Foo"


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------


class TestCSharpSymbolExtraction:
    """Tests for C# symbol extraction."""

    def test_class_and_method(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"class Foo {\n    void Bar(int x) {}\n}"
        result = parser.parse(tmp_path / "Foo.cs", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "class") in nk
        assert ("Bar", "method") in nk

    def test_struct_and_interface(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"struct Point { int X; }\ninterface IFoo { void DoThing(); }"
        result = parser.parse(tmp_path / "Types.cs", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Point", "struct") in nk
        assert ("IFoo", "interface") in nk

    def test_namespace_and_enum(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"namespace MyApp {\n    enum Color { Red, Green }\n}"
        result = parser.parse(tmp_path / "App.cs", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("MyApp", "namespace") in nk
        assert ("Color", "enum") in nk

    def test_property_and_delegate(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"class Foo {\n    string Name { get; set; }\n}\ndelegate void Handler(int x);"
        result = parser.parse(tmp_path / "Foo.cs", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Name", "property") in nk
        assert ("Handler", "delegate") in nk


# ---------------------------------------------------------------------------
# Kotlin
# ---------------------------------------------------------------------------


class TestKotlinSymbolExtraction:
    """Tests for Kotlin symbol extraction."""

    def test_class_and_function(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"class Foo {\n    fun bar(x: Int): Int = x + 1\n}"
        result = parser.parse(tmp_path / "Foo.kt", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "class") in nk
        assert ("bar", "method") in nk

    def test_object_and_enum(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"object Singleton {}\nenum class Color { RED, GREEN }"
        result = parser.parse(tmp_path / "Obj.kt", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Singleton", "object") in nk
        assert ("RED", "enum_constant") in nk


# ---------------------------------------------------------------------------
# Scala
# ---------------------------------------------------------------------------


class TestScalaSymbolExtraction:
    """Tests for Scala symbol extraction."""

    def test_class_and_method(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b'class Foo {\n  def bar(x: Int): Int = x + 1\n  val name: String = ""\n}'
        result = parser.parse(tmp_path / "Foo.scala", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "class") in nk
        assert ("bar", "method") in nk
        assert ("name", "val") in nk

    def test_trait_and_object(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"trait Qux { def doThing(): Unit }\nobject Singleton {}"
        result = parser.parse(tmp_path / "Qux.scala", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Qux", "trait") in nk
        assert ("Singleton", "object") in nk


# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------


class TestPHPSymbolExtraction:
    """Tests for PHP symbol extraction."""

    def test_class_and_method(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"<?php\nclass Foo {\n    function bar(int $x): int { return $x; }\n}"
        result = parser.parse(tmp_path / "Foo.php", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "class") in nk
        assert ("bar", "method") in nk

    def test_interface_trait_enum(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"<?php\ninterface Qux {}\ntrait MyTrait {}\nenum Color { case Red; }"
        result = parser.parse(tmp_path / "Types.php", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Qux", "interface") in nk
        assert ("MyTrait", "trait") in nk
        assert ("Color", "enum") in nk
        assert ("Red", "enum_case") in nk


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------


class TestRubySymbolExtraction:
    """Tests for Ruby symbol extraction."""

    def test_class_and_methods(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"class Foo\n  def bar(x)\n    x + 1\n  end\n  def self.class_method\n    42\n  end\nend"
        result = parser.parse(tmp_path / "foo.rb", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "class") in nk
        assert ("bar", "method") in nk
        assert ("class_method", "method") in nk

    def test_module(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"module MyMod\nend"
        result = parser.parse(tmp_path / "my_mod.rb", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("MyMod", "module") in nk


# ---------------------------------------------------------------------------
# C/C++
# ---------------------------------------------------------------------------


class TestCppSymbolExtraction:
    """Tests for C/C++ symbol extraction."""

    def test_class_and_methods(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"class Foo {\npublic:\n    void bar(int x);\n    int baz() { return 1; }\n};"
        result = parser.parse(tmp_path / "foo.cpp", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "class") in nk
        assert ("bar", "method") in nk
        assert ("baz", "method") in nk

    def test_struct_namespace_enum(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"namespace ns { struct Point { int x; }; }\nenum Color { Red, Green };"
        result = parser.parse(tmp_path / "types.cpp", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("ns", "namespace") in nk
        assert ("Point", "struct") in nk
        assert ("Color", "enum") in nk

    def test_template_class(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"template<typename T>\nclass Container { T value; };"
        result = parser.parse(tmp_path / "tmpl.cpp", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Container", "class") in nk

    def test_free_function(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"int add(int a, int b) { return a + b; }"
        result = parser.parse(tmp_path / "util.cpp", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("add", "function") in nk


# ---------------------------------------------------------------------------
# Swift
# ---------------------------------------------------------------------------


class TestSwiftSymbolExtraction:
    """Tests for Swift symbol extraction."""

    def test_class_and_method(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"class Foo {\n    func bar(x: Int) -> Int { return x + 1 }\n}"
        result = parser.parse(tmp_path / "Foo.swift", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "class") in nk
        assert ("bar", "method") in nk

    def test_struct_and_protocol(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"struct Point { var x: Int }\nprotocol Drawable { func draw() }"
        result = parser.parse(tmp_path / "Types.swift", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Point", "struct") in nk
        assert ("Drawable", "protocol") in nk

    def test_enum_with_cases(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"enum Color {\n    case red\n    case green\n}"
        result = parser.parse(tmp_path / "Color.swift", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Color", "enum") in nk
        assert ("red", "enum_case") in nk
        assert ("green", "enum_case") in nk


# ---------------------------------------------------------------------------
# Elixir
# ---------------------------------------------------------------------------


class TestElixirSymbolExtraction:
    """Tests for Elixir symbol extraction."""

    def test_module_and_functions(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = (
            b"defmodule Foo do\n  def bar(x) do\n    x + 1\n  end\n  defp baz(y), do: y * 2\nend"
        )
        result = parser.parse(tmp_path / "foo.ex", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "module") in nk
        assert ("bar", "function") in nk
        assert ("baz", "private_function") in nk

    def test_macro(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"defmodule MyMacros do\n  defmacro my_macro(expr) do\n    quote do: unquote(expr)\n  end\nend"
        result = parser.parse(tmp_path / "macros.ex", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("my_macro", "macro") in nk


# ---------------------------------------------------------------------------
# Haskell
# ---------------------------------------------------------------------------


class TestHaskellSymbolExtraction:
    """Tests for Haskell symbol extraction."""

    def test_data_type_and_constructors(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"module Foo where\ndata Color = Red | Green | Blue"
        result = parser.parse(tmp_path / "Foo.hs", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Color", "data") in nk
        assert ("Red", "constructor") in nk
        assert ("Green", "constructor") in nk
        assert ("Blue", "constructor") in nk

    def test_newtype_and_function(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"newtype Name = Name String\nfoo :: Int -> Int\nfoo x = x + 1"
        result = parser.parse(tmp_path / "Bar.hs", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Name", "newtype") in nk
        assert ("foo", "function") in nk
        assert ("foo", "signature") in nk


# ---------------------------------------------------------------------------
# OCaml
# ---------------------------------------------------------------------------


class TestOCamlSymbolExtraction:
    """Tests for OCaml symbol extraction."""

    def test_module_and_functions(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"module Foo = struct\n  let bar x = x + 1\nend\nlet top_fn x = x * 2"
        result = parser.parse(tmp_path / "foo.ml", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "module") in nk
        assert ("bar", "function") in nk
        assert ("top_fn", "function") in nk

    def test_type_definition(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"type color = Red | Green | Blue"
        result = parser.parse(tmp_path / "types.ml", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("color", "type") in nk


# ---------------------------------------------------------------------------
# Julia
# ---------------------------------------------------------------------------


class TestJuliaSymbolExtraction:
    """Tests for Julia symbol extraction."""

    def test_module_and_struct(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"module Foo\nstruct Bar\n  x::Int\nend\nend"
        result = parser.parse(tmp_path / "foo.jl", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Foo", "module") in nk
        assert ("Bar", "struct") in nk

    def test_function_with_return_type(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Functions with return type annotation should be extracted."""
        content = b"function baz(x::Int)::Int\n  x + 1\nend"
        result = parser.parse(tmp_path / "func.jl", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("baz", "function") in nk

    def test_function_simple(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"function simple(x)\n  x\nend"
        result = parser.parse(tmp_path / "simple.jl", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("simple", "function") in nk

    def test_short_form_function(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        """Short-form f(x) = expr should be extracted."""
        content = b"f(x) = x + 1"
        result = parser.parse(tmp_path / "short.jl", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("f", "function") in nk

    def test_abstract_type(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = b"abstract type Shape end"
        result = parser.parse(tmp_path / "abs.jl", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("Shape", "abstract_type") in nk


# ---------------------------------------------------------------------------
# Lua
# ---------------------------------------------------------------------------


class TestLuaSymbolExtraction:
    """Tests for Lua symbol extraction."""

    def test_named_functions(self, parser: TreeSitterParser, tmp_path: Path) -> None:
        content = (
            b"local function foo(x)\n    return x + 1\nend\nfunction bar(y)\n    return y * 2\nend"
        )
        result = parser.parse(tmp_path / "foo.lua", content)
        symbols = parser.extract_symbols(result)
        nk = {(s.name, s.kind) for s in symbols}
        assert ("foo", "function") in nk
        assert ("bar", "function") in nk


# ---------------------------------------------------------------------------
# Cross-language: verify location info is present
# ---------------------------------------------------------------------------


class TestSymbolLocationInfo:
    """All extracted symbols must have valid location info."""

    @pytest.mark.parametrize(
        "filename,code",
        [
            ("Foo.java", b"class Foo { void bar() {} }"),
            ("Foo.cs", b"class Foo { void Bar() {} }"),
            ("Foo.kt", b"class Foo { fun bar() {} }"),
            ("Foo.scala", b"class Foo { def bar(): Unit = {} }"),
            ("Foo.php", b"<?php\nclass Foo { function bar() {} }"),
            ("foo.rb", b"class Foo\n  def bar; end\nend"),
            ("foo.cpp", b"class Foo { int bar() { return 1; } };"),
            ("Foo.swift", b"class Foo { func bar() {} }"),
            ("foo.ex", b"defmodule Foo do\n  def bar(x), do: x\nend"),
            ("Foo.hs", b"foo x = x + 1"),
            ("foo.ml", b"let foo x = x + 1"),
            ("foo.jl", b"function foo(x)\n  x\nend"),
            ("foo.lua", b"function foo(x) return x end"),
        ],
        ids=[
            "java",
            "csharp",
            "kotlin",
            "scala",
            "php",
            "ruby",
            "cpp",
            "swift",
            "elixir",
            "haskell",
            "ocaml",
            "julia",
            "lua",
        ],
    )
    def test_symbols_have_location(
        self,
        parser: TreeSitterParser,
        tmp_path: Path,
        filename: str,
        code: bytes,
    ) -> None:
        result = parser.parse(tmp_path / filename, code)
        symbols = parser.extract_symbols(result)
        assert len(symbols) > 0, f"No symbols extracted for {filename}"
        for symbol in symbols:
            assert symbol.line >= 1
            assert symbol.column >= 0
            assert symbol.end_line >= symbol.line
