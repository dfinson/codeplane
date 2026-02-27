"""Tests for read_scaffold MCP tool helpers.

Covers _build_symbol_tree, _build_unindexed_fallback, _build_lite_scaffold,
and structural indexer persistence of new DefFact/ImportFact fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# =============================================================================
# Fake DefFact for tree builder tests
# =============================================================================


class FakeDef:
    """Minimal stand-in for DefFact rows used by _build_symbol_tree."""

    def __init__(
        self,
        name: str,
        kind: str,
        start_line: int,
        end_line: int,
        start_col: int = 0,
        signature_text: str | None = None,
        return_type: str | None = None,
        decorators_json: str | None = None,
        docstring: str | None = None,
        display_name: str | None = None,
    ) -> None:
        self.name = name
        self.kind = kind
        self.start_line = start_line
        self.start_col = start_col
        self.end_line = end_line
        self.signature_text = signature_text
        self.return_type = return_type
        self.decorators_json = decorators_json
        self.docstring = docstring
        self.display_name = display_name


# =============================================================================
# _build_symbol_tree tests
# =============================================================================


class TestBuildSymbolTree:
    """Test the _build_symbol_tree helper from files.py."""

    def _import_tree_builder(self) -> Any:
        from codeplane.mcp.tools.files import _build_symbol_tree

        return _build_symbol_tree

    def test_flat_functions(self) -> None:
        build = self._import_tree_builder()
        defs = [
            FakeDef("func_a", "function", 1, 5, signature_text="(x: int)"),
            FakeDef("func_b", "function", 7, 10, signature_text="(y: str)"),
        ]
        tree = build(defs)
        assert len(tree) == 2
        assert tree[0] == "function func_a(x: int)  [1-5]"
        assert tree[1] == "function func_b(y: str)  [7-10]"

    def test_nested_class_with_methods(self) -> None:
        build = self._import_tree_builder()
        defs = [
            FakeDef("MyClass", "class", 1, 20),
            FakeDef("__init__", "method", 3, 8, signature_text="(self, x: int)"),
            FakeDef("do_work", "method", 10, 18, signature_text="(self)"),
            FakeDef("standalone", "function", 22, 25, signature_text="()"),
        ]
        tree = build(defs)

        # 4 lines total: class, 2 indented methods, standalone
        assert len(tree) == 4
        assert tree[0] == "class MyClass  [1-20]"
        assert tree[1] == "  method __init__(self, x: int)  [3-8]"
        assert tree[2] == "  method do_work(self)  [10-18]"
        assert tree[3] == "function standalone()  [22-25]"

    def test_deeply_nested_containers(self) -> None:
        """class > inner class > method."""
        build = self._import_tree_builder()
        defs = [
            FakeDef("Outer", "class", 1, 30),
            FakeDef("Inner", "class", 5, 25),
            FakeDef("deep_method", "method", 10, 20, signature_text="(self)"),
        ]
        tree = build(defs)

        assert len(tree) == 3
        assert tree[0] == "class Outer  [1-30]"
        assert tree[1] == "  class Inner  [5-25]"
        assert tree[2] == "    method deep_method(self)  [10-20]"

    def test_docstrings_excluded_by_default(self) -> None:
        build = self._import_tree_builder()
        defs = [
            FakeDef("func", "function", 1, 5, docstring="Important docs."),
        ]
        tree = build(defs, include_docstrings=False)
        assert len(tree) == 1
        assert '"Important docs."' not in tree[0]

    def test_docstrings_included_when_requested(self) -> None:
        build = self._import_tree_builder()
        defs = [
            FakeDef("func", "function", 1, 5, docstring="Important docs."),
        ]
        tree = build(defs, include_docstrings=True)
        assert len(tree) == 2
        assert tree[1] == '  "Important docs."'

    def test_return_type_present(self) -> None:
        build = self._import_tree_builder()
        defs = [
            FakeDef("compute", "function", 1, 5, return_type="int"),
        ]
        tree = build(defs)
        assert "-> int" in tree[0]

    def test_return_type_omitted_when_none(self) -> None:
        build = self._import_tree_builder()
        defs = [
            FakeDef("compute", "function", 1, 5),
        ]
        tree = build(defs)
        assert "->" not in tree[0]

    def test_decorators_json_parsed(self) -> None:
        build = self._import_tree_builder()
        decos = ["@app.route('/api')", "@login_required"]
        defs = [
            FakeDef(
                "handler",
                "function",
                1,
                5,
                decorators_json=json.dumps(decos),
            ),
        ]
        tree = build(defs)
        assert "@" in tree[0]
        assert "@app.route('/api'), @login_required" in tree[0]

    def test_invalid_decorators_json_handled(self) -> None:
        build = self._import_tree_builder()
        defs = [
            FakeDef(
                "handler",
                "function",
                1,
                5,
                decorators_json="not-valid-json{{",
            ),
        ]
        # Should not raise
        tree = build(defs)
        # No decorator segment should appear
        assert isinstance(tree, list)
        assert "@" not in tree[0]

    def test_empty_defs_list(self) -> None:
        build = self._import_tree_builder()
        tree = build([])
        assert tree == []

    def test_single_method_outside_class(self) -> None:
        """Methods not inside a class container should be top-level."""
        build = self._import_tree_builder()
        defs = [
            FakeDef("orphan_method", "method", 1, 5, signature_text="(self)"),
        ]
        tree = build(defs)
        assert len(tree) == 1
        assert tree[0] == "method orphan_method(self)  [1-5]"

    def test_line_range_includes_span_info(self) -> None:
        build = self._import_tree_builder()
        defs = [
            FakeDef("func", "function", 10, 25),
        ]
        tree = build(defs)
        assert "[10-25]" in tree[0]

    def test_display_name_used_when_present(self) -> None:
        """display_name is not used in compact format; name is used."""
        build = self._import_tree_builder()
        defs = [
            FakeDef(
                "impl_trait_method",
                "method",
                1,
                5,
                display_name="Trait::method",
            ),
        ]
        tree = build(defs)
        assert "impl_trait_method" in tree[0]

    def test_constants_included_in_tree_when_passed(self) -> None:
        """_build_symbol_tree renders all defs it receives, including constants."""
        build = self._import_tree_builder()
        defs = [
            FakeDef("MY_CONST", "variable", 1, 1),
            FakeDef("func", "function", 3, 10),
        ]
        tree = build(defs)
        assert len(tree) == 2
        assert "variable MY_CONST" in tree[0]
        assert "function func" in tree[1]

    def test_constant_filtering_logic(self) -> None:
        """Validates the constant-kind filtering used by _build_scaffold."""
        constant_kinds = frozenset({"variable", "constant", "val", "var", "property", "field"})
        defs = [
            FakeDef("MY_VAR", "variable", 1, 1),
            FakeDef("SETTING", "constant", 2, 2),
            FakeDef("func", "function", 5, 10),
            FakeDef("MyClass", "class", 12, 30),
        ]
        # exclude constants (default behavior)
        filtered = [d for d in defs if d.kind not in constant_kinds]
        assert len(filtered) == 2
        assert filtered[0].name == "func"
        assert filtered[1].name == "MyClass"

        # include constants (include_constants=True means keep all)
        include_constants = True
        filtered_all = [d for d in defs if include_constants or d.kind not in constant_kinds]
        assert len(filtered_all) == 4


# =============================================================================
# _build_unindexed_fallback tests
# =============================================================================


class TestBuildUnindexedFallback:
    """Test the _build_unindexed_fallback helper."""

    def _import_fallback(self) -> Any:
        from codeplane.mcp.tools.files import _build_unindexed_fallback

        return _build_unindexed_fallback

    def test_returns_indexed_false(self, tmp_path: Path) -> None:
        fallback = self._import_fallback()
        fp = tmp_path / "test.txt"
        fp.write_text("line 1\nline 2\nline 3\n")

        result = fallback(fp, "test.txt")
        assert result["indexed"] is False
        assert result["total_lines"] == 3
        assert "agentic_hint" in result
        assert result["symbols"] == []
        assert result["imports"] == []

    def test_empty_file(self, tmp_path: Path) -> None:
        fallback = self._import_fallback()
        fp = tmp_path / "empty.txt"
        fp.write_text("")

        result = fallback(fp, "empty.txt")
        assert result["indexed"] is False
        assert result["total_lines"] == 0

    def test_path_in_response(self, tmp_path: Path) -> None:
        fallback = self._import_fallback()
        fp = tmp_path / "data.csv"
        fp.write_text("a,b,c\n1,2,3\n")

        result = fallback(fp, "data.csv")
        assert result["path"] == "data.csv"

    def test_hint_suggests_read_source(self, tmp_path: Path) -> None:
        fallback = self._import_fallback()
        fp = tmp_path / "config.yaml"
        fp.write_text("key: value\n")

        result = fallback(fp, "config.yaml")
        hint = result.get("agentic_hint", "")
        assert "recon_resolve" in hint


# =============================================================================
# Fake ImportFact for lite scaffold tests
# =============================================================================


class FakeImport:
    """Minimal stand-in for ImportFact rows used by _build_lite_scaffold."""

    def __init__(
        self,
        imported_name: str,
        source_literal: str | None = None,
        alias: str | None = None,
    ) -> None:
        self.imported_name = imported_name
        self.source_literal = source_literal
        self.alias = alias


# =============================================================================
# _build_lite_scaffold tests
# =============================================================================


class TestBuildLiteScaffold:
    """Test the _build_lite_scaffold helper from files.py."""

    @pytest.fixture
    def _mock_app_ctx(self) -> Any:
        """Build a mock app_ctx wired to return controlled defs/imports.

        The fixture stores ``_defs`` and ``_imports`` on itself; callers
        assign lists before invoking the function under test.
        """
        from unittest.mock import MagicMock

        file_rec = MagicMock()
        file_rec.id = 42
        file_rec.path = "src/mod.py"

        ctx = MagicMock()

        # First session → select(File) → returns file_rec
        # Second session → FactQueries → returns stored defs/imports
        # We use side_effect to distinguish the two context-manager calls.
        class _FakeSession:
            """Context manager that routes calls to the right fakes."""

            def __init__(self, call_index: list[int], owner: Any) -> None:
                self._call_index = call_index
                self._owner = owner

            def __enter__(self) -> Any:
                idx = self._call_index[0]
                self._call_index[0] = idx + 1
                if idx == 0:
                    # File lookup session
                    sess = MagicMock()
                    exec_result = MagicMock()
                    exec_result.first.return_value = file_rec
                    sess.exec.return_value = exec_result
                    self._sess = sess
                    return sess
                # FactQueries session
                sess = MagicMock()
                self._sess = sess
                return sess

            def __exit__(self, *_: Any) -> None:
                pass

        call_idx = [0]
        ctx.coordinator.db.session.side_effect = lambda: _FakeSession(call_idx, ctx)

        # Patch FactQueries — the function imports it inside the body
        ctx._defs = []
        ctx._imports = []
        ctx._call_idx = call_idx  # so tests can reset
        ctx._file_rec = file_rec
        return ctx

    @pytest.fixture
    def _reset_call_idx(self, _mock_app_ctx: Any) -> Any:
        """Reset call counter between tests."""
        _mock_app_ctx._call_idx[0] = 0
        return _mock_app_ctx

    async def _run(
        self,
        app_ctx: Any,
        tmp_path: Path,
        *,
        defs: list[Any] | None = None,
        imports: list[Any] | None = None,
        content: str = "line1\nline2\nline3\n",
    ) -> dict[str, Any]:
        """Helper to execute _build_lite_scaffold with patched deps."""
        from unittest.mock import patch

        from codeplane.mcp.tools.files import _build_lite_scaffold

        fp = tmp_path / "mod.py"
        fp.write_text(content)

        fq_instance = MagicMock()
        fq_instance.list_defs_in_file.return_value = defs or []
        fq_instance.list_imports.return_value = imports or []

        with patch(
            "codeplane.index._internal.indexing.graph.FactQueries",
            return_value=fq_instance,
        ):
            return await _build_lite_scaffold(app_ctx, "src/mod.py", fp)

    @pytest.mark.asyncio
    async def test_basic_structure(self, _reset_call_idx: Any, tmp_path: Path) -> None:
        result = await self._run(_reset_call_idx, tmp_path)
        assert "total_lines" in result
        assert "imports" in result
        assert "symbols" in result
        assert result["total_lines"] == 3

    @pytest.mark.asyncio
    async def test_symbols_include_functions_and_classes(
        self, _reset_call_idx: Any, tmp_path: Path
    ) -> None:
        defs = [
            FakeDef("MyClass", "class", 1, 10),
            FakeDef("helper", "function", 12, 20),
            FakeDef("do_work", "method", 3, 8),
        ]
        result = await self._run(_reset_call_idx, tmp_path, defs=defs)
        assert "class MyClass" in result["symbols"]
        assert "function helper" in result["symbols"]
        assert "method do_work" in result["symbols"]

    @pytest.mark.asyncio
    async def test_constants_excluded(self, _reset_call_idx: Any, tmp_path: Path) -> None:
        defs = [
            FakeDef("MY_CONST", "variable", 1, 1),
            FakeDef("SETTING", "constant", 2, 2),
            FakeDef("prop", "property", 3, 3),
            FakeDef("x", "field", 4, 4),
            FakeDef("real_func", "function", 5, 10),
        ]
        result = await self._run(_reset_call_idx, tmp_path, defs=defs)
        assert len(result["symbols"]) == 1
        assert result["symbols"][0] == "function real_func"

    @pytest.mark.asyncio
    async def test_import_sources_deduplicated(self, _reset_call_idx: Any, tmp_path: Path) -> None:
        imports = [
            FakeImport("Path", source_literal="pathlib"),
            FakeImport("PurePath", source_literal="pathlib"),
            FakeImport("json", source_literal=None),
        ]
        result = await self._run(_reset_call_idx, tmp_path, imports=imports)
        assert "pathlib" in result["imports"]
        assert "json" in result["imports"]
        # Deduplicated — pathlib only once
        assert result["imports"].count("pathlib") == 1

    @pytest.mark.asyncio
    async def test_import_sources_sorted(self, _reset_call_idx: Any, tmp_path: Path) -> None:
        imports = [
            FakeImport("z", source_literal="zlib"),
            FakeImport("a", source_literal="abc"),
            FakeImport("m", source_literal="math"),
        ]
        result = await self._run(_reset_call_idx, tmp_path, imports=imports)
        assert result["imports"] == ["abc", "math", "zlib"]

    @pytest.mark.asyncio
    async def test_unindexed_file_returns_empty(self, tmp_path: Path) -> None:
        """When file is not in the index, returns empty symbols/imports."""
        from unittest.mock import MagicMock

        from codeplane.mcp.tools.files import _build_lite_scaffold

        app_ctx = MagicMock()
        sess = MagicMock()
        exec_result = MagicMock()
        exec_result.first.return_value = None  # file not found
        sess.exec.return_value = exec_result
        app_ctx.coordinator.db.session.return_value.__enter__ = MagicMock(return_value=sess)
        app_ctx.coordinator.db.session.return_value.__exit__ = MagicMock(return_value=False)

        fp = tmp_path / "unknown.py"
        fp.write_text("print('hello')\n")

        result = await _build_lite_scaffold(app_ctx, "unknown.py", fp)
        assert result["total_lines"] == 1
        assert result["imports"] == []
        assert result["symbols"] == []

    @pytest.mark.asyncio
    async def test_line_count_no_trailing_newline(
        self, _reset_call_idx: Any, tmp_path: Path
    ) -> None:
        result = await self._run(_reset_call_idx, tmp_path, content="line1\nline2")
        assert result["total_lines"] == 2

    @pytest.mark.asyncio
    async def test_empty_file(self, _reset_call_idx: Any, tmp_path: Path) -> None:
        result = await self._run(_reset_call_idx, tmp_path, content="")
        assert result["total_lines"] == 0
        assert result["symbols"] == []
        assert result["imports"] == []


# =============================================================================
# Structural indexer: new DefFact fields persisted
# =============================================================================


class TestDefFactNewFields:
    """Test that new DefFact fields are populated by the structural indexer."""

    def test_def_dict_has_scaffold_fields(self, tmp_path: Path) -> None:
        from codeplane.index._internal.indexing.structural import _extract_file

        code = '''@staticmethod
def helper(x: int) -> str:
    """Convert int to string."""
    return str(x)
'''
        (tmp_path / "test.py").write_text(code)

        result = _extract_file("test.py", str(tmp_path), unit_id=1)
        assert not result.error

        assert len(result.defs) > 0
        helper_def = next(d for d in result.defs if d["name"] == "helper")

        # signature_text
        assert helper_def.get("signature_text") is not None
        assert "x: int" in helper_def["signature_text"]

        # decorators_json
        assert helper_def.get("decorators_json") is not None
        decos = json.loads(helper_def["decorators_json"])
        assert any("staticmethod" in d for d in decos)

        # docstring
        assert helper_def.get("docstring") is not None
        assert "Convert" in helper_def["docstring"]

    def test_no_scaffold_fields_when_absent(self, tmp_path: Path) -> None:
        from codeplane.index._internal.indexing.structural import _extract_file

        code = """def bare():
    return 1
"""
        (tmp_path / "test.py").write_text(code)

        result = _extract_file("test.py", str(tmp_path), unit_id=1)
        assert not result.error

        bare_def = next(d for d in result.defs if d["name"] == "bare")
        assert bare_def.get("decorators_json") is None
        assert bare_def.get("docstring") is None


# =============================================================================
# Structural indexer: ImportFact line numbers persisted
# =============================================================================


class TestImportFactLineNumbers:
    """Test that import line numbers are persisted in the indexer."""

    def test_import_dict_has_line_numbers(self, tmp_path: Path) -> None:
        from codeplane.index._internal.indexing.structural import _extract_file

        code = """import os
from pathlib import Path

def main():
    pass
"""
        (tmp_path / "test.py").write_text(code)

        result = _extract_file("test.py", str(tmp_path), unit_id=1)
        assert not result.error

        assert len(result.imports) > 0
        for imp_dict in result.imports:
            assert "start_line" in imp_dict
            assert "end_line" in imp_dict
            assert imp_dict["start_line"] is not None
            assert imp_dict["start_line"] > 0

    def test_import_line_numbers_are_correct(self, tmp_path: Path) -> None:
        from codeplane.index._internal.indexing.structural import _extract_file

        code = """import os
from sys import argv
"""
        (tmp_path / "test.py").write_text(code)

        result = _extract_file("test.py", str(tmp_path), unit_id=1)
        assert not result.error

        # First import should be on line 1, second on line 2
        lines = sorted(imp_dict["start_line"] for imp_dict in result.imports)
        assert lines[0] == 1
        assert lines[1] == 2
