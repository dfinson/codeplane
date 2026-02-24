"""Tests for file-level embedding scaffold builder.

Tests the anglicified scaffold generation from tree-sitter
extracted defs and imports — the bridge between English-language
queries and code structure.
"""

from __future__ import annotations

import pytest

from codeplane.index._internal.indexing.file_embedding import (
    _build_embed_text,
    _compact_sig,
    _path_to_phrase,
    _word_split,
    build_file_scaffold,
)

# ---------------------------------------------------------------------------
# _word_split tests
# ---------------------------------------------------------------------------


class TestWordSplit:
    """Tests for identifier → word splitting."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("getUserById", ["get", "user", "by", "id"]),
            ("XMLParser", ["xml", "parser"]),
            ("snake_case_name", ["snake", "case", "name"]),
            ("PascalCase", ["pascal", "case"]),
            ("simpleword", ["simpleword"]),
            ("__init__", ["init"]),
            ("HTTP2Client", ["http", "2", "client"]),
            ("", []),
        ],
    )
    def test_splits(self, name: str, expected: list[str]) -> None:
        assert _word_split(name) == expected


# ---------------------------------------------------------------------------
# _path_to_phrase tests
# ---------------------------------------------------------------------------


class TestPathToPhrase:
    """Tests for file path → natural phrase conversion."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("src/auth/middleware/rate_limiter.py", "auth middleware rate limiter"),
            ("lib/utils/string_helper.js", "utils string helper"),
            ("app/models/UserProfile.ts", "models user profile"),
            ("core/base.py", "core base"),
            ("README.md", "readme"),
        ],
    )
    def test_paths(self, path: str, expected: str) -> None:
        assert _path_to_phrase(path) == expected


# ---------------------------------------------------------------------------
# _compact_sig tests
# ---------------------------------------------------------------------------


class TestCompactSig:
    """Tests for signature compaction."""

    def test_with_signature(self) -> None:
        result = _compact_sig("check_rate", "(self, request, limit)")
        assert result == "check rate(request, limit)"

    def test_no_signature(self) -> None:
        result = _compact_sig("get_value", "")
        assert result == "get value"

    def test_self_only(self) -> None:
        result = _compact_sig("reset", "(self)")
        assert result == "reset"


# ---------------------------------------------------------------------------
# build_file_scaffold tests
# ---------------------------------------------------------------------------


class TestBuildFileScaffold:
    """Tests for anglicified scaffold generation from tree-sitter data."""

    def test_empty_defs_and_imports(self) -> None:
        """Scaffold with no defs produces just module line."""
        result = build_file_scaffold("src/core/base.py", [], [])
        assert "module" in result
        assert "core base" in result

    def test_with_classes(self) -> None:
        defs = [
            {"kind": "class", "name": "RateLimiter", "signature_text": ""},
            {"kind": "method", "name": "check_rate", "signature_text": "(self, request)"},
        ]
        result = build_file_scaffold("src/rate_limiter.py", defs, [])
        assert "class rate limiter" in result.lower()
        assert "defines" in result

    def test_with_imports(self) -> None:
        imports = [
            {"imported_name": "os", "source_literal": "os"},
            {"imported_name": "Path", "source_literal": "pathlib"},
        ]
        result = build_file_scaffold("src/utils.py", [], imports)
        assert "imports" in result.lower()

    def test_with_functions(self) -> None:
        defs = [
            {"kind": "function", "name": "compute_hash", "signature_text": "(data: bytes)"},
            {"kind": "function", "name": "validate_input", "signature_text": "(value: str)"},
        ]
        result = build_file_scaffold("src/helpers.py", defs, [])
        assert "defines" in result
        assert "compute hash" in result

    def test_scaffold_includes_all_extraction_data(self) -> None:
        """Scaffold includes all defs and imports — no arbitrary truncation."""
        defs = [
            {"kind": "function", "name": f"very_long_function_name_{i}", "signature_text": "(a, b, c)"}
            for i in range(50)
        ]
        imports = [
            {"imported_name": f"module_{i}", "source_literal": f"package.module_{i}"}
            for i in range(30)
        ]
        result = build_file_scaffold("src/big_module.py", defs, imports)
        # All 50 functions should appear (no arbitrary cap)
        for i in range(50):
            assert f"very long function name {i}" in result
        # All 30 unique import sources should appear
        for i in range(30):
            assert f"module {i}" in result

    def test_with_docstring(self) -> None:
        defs = [
            {
                "kind": "class",
                "name": "Config",
                "signature_text": "",
                "docstring": "Configuration manager for application settings.",
            },
        ]
        result = build_file_scaffold("src/config.py", defs, [])
        assert "describes" in result.lower()

    def test_dedup_imports(self) -> None:
        """Duplicate import sources should be deduplicated."""
        imports = [
            {"imported_name": "A", "source_literal": "os"},
            {"imported_name": "B", "source_literal": "os"},
        ]
        result = build_file_scaffold("src/x.py", [], imports)
        # "os" should appear only once in imports line
        import_line = [line for line in result.split("\n") if line.startswith("imports")][0]
        assert import_line.count("os") == 1

    def test_no_defs_no_imports_from_path(self) -> None:
        """With only a path, scaffold should still produce module line."""
        result = build_file_scaffold("src/auth/middleware.py", [], [])
        assert result.startswith("module")

    def test_mixed_kinds_sorted(self) -> None:
        """Classes should appear before functions in defines."""
        defs = [
            {"kind": "function", "name": "helper_func", "signature_text": "()"},
            {"kind": "class", "name": "MainClass", "signature_text": ""},
            {"kind": "method", "name": "do_work", "signature_text": "(self)"},
        ]
        result = build_file_scaffold("src/main.py", defs, [])
        assert "class main class" in result.lower()
        # Class should come before function in the defines line
        defines_line = [line for line in result.split("\n") if line.startswith("defines")][0]
        class_pos = defines_line.lower().find("class")
        func_pos = defines_line.lower().find("helper")
        assert class_pos < func_pos


# ---------------------------------------------------------------------------
# _build_embed_text tests
# ---------------------------------------------------------------------------


class TestBuildEmbedText:
    """Tests for composed embed text (scaffold + content)."""

    def test_with_scaffold(self) -> None:
        scaffold = "module auth rate limiter\ndefines class RateLimiter"
        content = "class RateLimiter:\n    pass"
        result = _build_embed_text(scaffold, content)
        assert "FILE_SCAFFOLD" in result
        assert "FILE_CHUNK" in result
        assert "module auth" in result
        assert "class RateLimiter" in result

    def test_without_scaffold(self) -> None:
        result = _build_embed_text("", "print('hello')")
        assert "FILE_SCAFFOLD" not in result
        assert "FILE_CHUNK" in result
        assert "print('hello')" in result

    def test_scaffold_preserved_on_truncation(self) -> None:
        """Scaffold should be preserved even when content is truncated."""
        scaffold = "module test"
        # Content longer than budget
        content = "x" * 30_000
        result = _build_embed_text(scaffold, content)
        assert "module test" in result
        assert len(result) < len(content) + len(scaffold) + 50
