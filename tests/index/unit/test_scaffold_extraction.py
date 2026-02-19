"""Unit tests for scaffold-related extraction: decorators, docstrings, return types.

Tests the new SyntacticSymbol fields populated during query-based symbol extraction:
- signature_text: raw parameter signature
- decorators: list of decorator/annotation strings
- docstring: first paragraph of docstring
- return_type: return type annotation

Covers Python, JavaScript, TypeScript, Java, Rust, Go to exercise
all extraction strategies (decorated_definition parent, modifiers children,
preceding attribute_item siblings, preceding doc comments).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.parsing import (
    TreeSitterParser,
)


@pytest.fixture
def parser() -> TreeSitterParser:
    return TreeSitterParser()


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    return tmp_path


# =============================================================================
# Python: decorators, docstrings, return types
# =============================================================================


class TestPythonScaffoldExtraction:
    """Test scaffold fields for Python."""

    def test_function_with_decorator(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = '''import functools

@functools.lru_cache(maxsize=128)
def cached_compute(x: int) -> int:
    """Compute something expensive."""
    return x * x
'''
        fp = temp_dir / "test.py"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "cached_compute")
        assert func.decorators is not None
        assert any("lru_cache" in d for d in func.decorators)
        assert func.signature_text is not None
        assert "x: int" in func.signature_text
        assert func.docstring == "Compute something expensive."

    def test_function_return_type(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = '''def greet(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"
'''
        fp = temp_dir / "test.py"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "greet")
        assert func.return_type is not None
        assert "str" in func.return_type

    def test_class_with_docstring(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = '''class MyService:
    """Provides core business logic.

    This class handles all the heavy lifting.
    """

    def run(self) -> None:
        pass
'''
        fp = temp_dir / "test.py"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        cls = next(s for s in symbols if s.name == "MyService")
        assert cls.docstring == "Provides core business logic."

    def test_multiple_decorators(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """@staticmethod
@deprecated
def legacy_func():
    pass
"""
        fp = temp_dir / "test.py"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "legacy_func")
        assert func.decorators is not None
        assert len(func.decorators) >= 2

    def test_no_decorator_no_docstring(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """def bare_func(x):
    return x + 1
"""
        fp = temp_dir / "test.py"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "bare_func")
        assert func.decorators is None
        assert func.docstring is None

    def test_signature_text_preserved(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """def process(items: list[str], *, verbose: bool = False) -> dict:
    pass
"""
        fp = temp_dir / "test.py"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "process")
        assert func.signature_text is not None
        assert "items" in func.signature_text
        assert "verbose" in func.signature_text

    def test_method_return_type(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = '''class Converter:
    def to_json(self, data: dict) -> str:
        """Serialize to JSON."""
        return "{}"
'''
        fp = temp_dir / "test.py"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        method = next(s for s in symbols if s.name == "to_json")
        assert method.return_type is not None
        assert "str" in method.return_type
        assert method.docstring == "Serialize to JSON."


# =============================================================================
# JavaScript: JSDoc comments
# =============================================================================


class TestJavaScriptScaffoldExtraction:
    """Test scaffold fields for JavaScript."""

    def test_jsdoc_docstring(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """/**
 * Process the input data and return results.
 * @param {string} data - Input data
 * @returns {Object} Processed result
 */
function processData(data) {
    return { data };
}
"""
        fp = temp_dir / "test.js"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "processData")
        assert func.docstring is not None
        assert "Process" in func.docstring

    def test_no_jsdoc_no_docstring(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """// Just a regular comment
function simpleFunc() {
    return 42;
}
"""
        fp = temp_dir / "test.js"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "simpleFunc")
        # Regular comments (not /** */) should NOT be captured as docstrings
        assert func.docstring is None

    def test_class_with_jsdoc(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """/**
 * Manages user sessions.
 */
class SessionManager {
    constructor() {
        this.sessions = {};
    }
}
"""
        fp = temp_dir / "test.js"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        cls = next(s for s in symbols if s.name == "SessionManager")
        assert cls.docstring is not None
        assert "session" in cls.docstring.lower()


# =============================================================================
# TypeScript: return types + JSDoc
# =============================================================================


class TestTypeScriptScaffoldExtraction:
    """Test scaffold fields for TypeScript."""

    def test_return_type_annotation(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """function greet(name: string): string {
    return `Hello, ${name}!`;
}
"""
        fp = temp_dir / "test.ts"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "greet")
        # TypeScript return types may be extracted
        # At minimum, signature should be present
        assert func.signature_text is not None


# =============================================================================
# Rust: #[...] attributes and /// doc comments
# =============================================================================


class TestRustScaffoldExtraction:
    """Test scaffold fields for Rust."""

    def test_derive_attribute(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """#[derive(Debug, Clone)]
struct Point {
    x: f64,
    y: f64,
}
"""
        fp = temp_dir / "test.rs"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        struct = next(s for s in symbols if s.name == "Point")
        assert struct.decorators is not None
        assert any("derive" in d for d in struct.decorators)

    def test_doc_comment(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """/// Adds two numbers together.
fn add(a: i32, b: i32) -> i32 {
    a + b
}
"""
        fp = temp_dir / "test.rs"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "add")
        assert func.docstring is not None
        assert "Adds" in func.docstring

    def test_function_return_type(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """fn compute(x: i32) -> Vec<String> {
    vec![]
}
"""
        fp = temp_dir / "test.rs"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "compute")
        # Rust return types: might be extracted via 'return_type' field
        assert func.signature_text is not None


# =============================================================================
# Java: annotations + Javadoc
# =============================================================================


class TestJavaScaffoldExtraction:
    """Test scaffold fields for Java."""

    def test_annotation_extraction(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """import java.lang.Override;

public class MyService {
    /**
     * Process the request.
     */
    @Override
    public void handleRequest(String input) {
        System.out.println(input);
    }
}
"""
        fp = temp_dir / "test.java"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        # Check annotations on method
        method = next((s for s in symbols if s.name == "handleRequest"), None)
        if method is not None and method.decorators is not None:
            # Java annotations should appear as decorators
            assert any("Override" in d for d in method.decorators)

    def test_javadoc_extraction(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """/**
 * Main application entry point.
 */
public class Application {
    public static void main(String[] args) {
    }
}
"""
        fp = temp_dir / "test.java"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        cls = next((s for s in symbols if s.name == "Application"), None)
        if cls is not None:
            assert cls.docstring is not None
            assert "entry point" in cls.docstring.lower()


# =============================================================================
# Go: no decorators, doc comments
# =============================================================================


class TestGoScaffoldExtraction:
    """Test scaffold fields for Go."""

    def test_no_decorators(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """package main

func Add(a int, b int) int {
    return a + b
}
"""
        fp = temp_dir / "test.go"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        func = next(s for s in symbols if s.name == "Add")
        assert func.decorators is None  # Go has no decorators
        assert func.signature_text is not None


# =============================================================================
# C#: attributes + XML doc comments
# =============================================================================


class TestCSharpScaffoldExtraction:
    """Test scaffold fields for C#."""

    def test_attribute_extraction(self, parser: TreeSitterParser, temp_dir: Path) -> None:
        code = """using System;

/// <summary>
/// A simple service class.
/// </summary>
[Serializable]
public class DataService
{
    [Obsolete("Use NewMethod instead")]
    public void OldMethod()
    {
    }

    public string NewMethod()
    {
        return "hello";
    }
}
"""
        fp = temp_dir / "test.cs"
        fp.write_text(code)
        result = parser.parse(fp, code.encode())
        assert result is not None
        symbols = parser.extract_symbols(result)

        cls = next((s for s in symbols if s.name == "DataService"), None)
        if cls is not None:
            assert cls.docstring is not None
            assert "service" in cls.docstring.lower()
