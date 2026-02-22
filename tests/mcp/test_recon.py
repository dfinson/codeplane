"""Tests for the recon MCP tool.

Tests:
- _tokenize_task: task description tokenization
- _select_seeds: term-intersection + hub-score reranking
- _expand_seed: graph expansion
- _trim_to_budget: budget assembly
- _summarize_recon: summary generation
- register_tools: tool wiring
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codeplane.mcp.tools.recon import (
    _def_signature_text,
    _estimate_bytes,
    _extract_paths,
    _read_lines,
    _summarize_recon,
    _tokenize_task,
    _trim_to_budget,
)

# ---------------------------------------------------------------------------
# Tokenization tests
# ---------------------------------------------------------------------------


class TestTokenizeTask:
    """Tests for _tokenize_task."""

    def test_single_word(self) -> None:
        terms = _tokenize_task("FactQueries")
        assert "factqueries" in terms

    def test_multi_word(self) -> None:
        terms = _tokenize_task("add validation to the search tool")
        assert "validation" in terms
        assert "search" in terms
        # "add", "to", "the", "tool" are stop words → excluded
        assert "add" not in terms
        assert "to" not in terms
        assert "the" not in terms
        assert "tool" not in terms

    def test_camelcase_split(self) -> None:
        terms = _tokenize_task("IndexCoordinator")
        assert "indexcoordinator" in terms
        # camelCase parts also extracted
        assert "index" in terms
        assert "coordinator" in terms

    def test_snake_case_split(self) -> None:
        terms = _tokenize_task("get_callees")
        assert "get_callees" in terms
        assert "callees" in terms

    def test_quoted_terms_preserved(self) -> None:
        terms = _tokenize_task('fix "read_source" tool')
        assert "read_source" in terms

    def test_stop_words_filtered(self) -> None:
        terms = _tokenize_task("how does the checkpoint tool run tests")
        assert "checkpoint" in terms
        assert "how" not in terms
        assert "does" not in terms
        assert "the" not in terms

    def test_short_terms_filtered(self) -> None:
        terms = _tokenize_task("a b cd ef")
        assert "a" not in terms
        assert "b" not in terms
        assert "cd" in terms
        assert "ef" in terms

    def test_empty_task(self) -> None:
        assert _tokenize_task("") == []

    def test_all_stop_words(self) -> None:
        assert _tokenize_task("the is and or") == []

    def test_dedup(self) -> None:
        terms = _tokenize_task("search search search")
        assert terms.count("search") == 1

    def test_sorted_by_length_descending(self) -> None:
        terms = _tokenize_task("IndexCoordinator search lint")
        # Longer terms should come first
        lengths = [len(t) for t in terms]
        assert lengths == sorted(lengths, reverse=True)

    @pytest.mark.parametrize(
        ("task", "expected_term"),
        [
            ("FactQueries", "factqueries"),
            ("checkpoint", "checkpoint"),
            ("semantic_diff", "semantic_diff"),
            ("recon tool", "recon"),
            ("MCP server", "mcp"),
            ("graph.py", "graph"),
        ],
    )
    def test_common_tasks(self, task: str, expected_term: str) -> None:
        terms = _tokenize_task(task)
        assert expected_term in terms


# ---------------------------------------------------------------------------
# Path extraction tests
# ---------------------------------------------------------------------------


class TestExtractPaths:
    """Tests for _extract_paths."""

    def test_backtick_path(self) -> None:
        paths = _extract_paths("Fix the model in `src/evee/core/base_model.py` to add caching")
        assert "src/evee/core/base_model.py" in paths

    def test_quoted_path(self) -> None:
        paths = _extract_paths('Look at "config/models.py" for settings')
        assert "config/models.py" in paths

    def test_bare_path(self) -> None:
        paths = _extract_paths("The evaluator is in evaluation/model_evaluator.py")
        assert "evaluation/model_evaluator.py" in paths

    def test_multiple_paths(self) -> None:
        task = "Modify `src/core/base_model.py` and `src/config/models.py` to support caching"
        paths = _extract_paths(task)
        assert "src/core/base_model.py" in paths
        assert "src/config/models.py" in paths

    def test_no_paths(self) -> None:
        paths = _extract_paths("add caching to the model abstraction")
        assert paths == []

    def test_dotted_but_not_path(self) -> None:
        # Version numbers, URLs etc should not match as paths
        paths = _extract_paths("upgrade to version 3.12")
        assert paths == []

    def test_strip_leading_dot_slash(self) -> None:
        paths = _extract_paths("Fix `./src/main.py` please")
        assert "src/main.py" in paths

    def test_dedup(self) -> None:
        paths = _extract_paths("`config/models.py` and also config/models.py again")
        assert paths.count("config/models.py") == 1

    def test_various_extensions(self) -> None:
        paths = _extract_paths("Check `src/app.ts` and `lib/utils.js` and `main.go`")
        assert "src/app.ts" in paths
        assert "lib/utils.js" in paths
        assert "main.go" in paths


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestDefSignatureText:
    """Tests for _def_signature_text."""

    def test_simple_function(self) -> None:
        d = MagicMock()
        d.kind = "function"
        d.name = "foo"
        d.signature_text = "(x: int, y: int)"
        d.return_type = "str"
        assert _def_signature_text(d) == "function foo(x: int, y: int) -> str"

    def test_no_signature_no_return(self) -> None:
        d = MagicMock()
        d.kind = "class"
        d.name = "MyClass"
        d.signature_text = None
        d.return_type = None
        assert _def_signature_text(d) == "class MyClass"

    def test_signature_without_parens(self) -> None:
        d = MagicMock()
        d.kind = "method"
        d.name = "run"
        d.signature_text = "self, timeout: float"
        d.return_type = None
        assert _def_signature_text(d) == "method run(self, timeout: float)"


class TestReadLines:
    """Tests for _read_lines."""

    def test_reads_range(self, tmp_path: Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")
        result = _read_lines(f, 2, 4)
        assert result == "line2\nline3\nline4\n"

    def test_clamps_to_bounds(self, tmp_path: Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("line1\nline2\n")
        result = _read_lines(f, 1, 100)
        assert result == "line1\nline2\n"

    def test_missing_file(self, tmp_path: Path) -> None:
        result = _read_lines(tmp_path / "nope.py", 1, 5)
        assert result == ""


class TestSummarizeRecon:
    """Tests for _summarize_recon."""

    def test_full_summary(self) -> None:
        s = _summarize_recon(3, 10, 5, 4, 2, "add caching to search")
        assert "3 seeds" in s
        assert "10 callees" in s
        assert "4 import defs" in s
        assert "5 callers" in s
        assert "2 scaffolds" in s
        assert "add caching to search" in s

    def test_minimal_summary(self) -> None:
        s = _summarize_recon(1, 0, 0, 0, 0, "fix bug")
        assert "1 seeds" in s
        assert "callees" not in s
        assert "callers" not in s
        assert "import defs" not in s


class TestEstimateBytes:
    """Tests for _estimate_bytes."""

    def test_simple_dict(self) -> None:
        obj = {"key": "value"}
        result = _estimate_bytes(obj)
        assert result > 0
        assert isinstance(result, int)


class TestTrimToBudget:
    """Tests for _trim_to_budget."""

    def test_within_budget_unchanged(self) -> None:
        result = {"seeds": [{"source": "x = 1"}], "summary": "1 seed"}
        original = dict(result)
        trimmed = _trim_to_budget(result, 100_000)
        assert trimmed["seeds"] == original["seeds"]

    def test_scaffolds_trimmed_first(self) -> None:
        result = {
            "seeds": [{"source": "x" * 100}],
            "import_scaffolds": [
                {"path": "a.py", "symbols": ["a" * 500]},
                {"path": "b.py", "symbols": ["b" * 500]},
            ],
            "summary": "test",
        }
        trimmed = _trim_to_budget(result, 200)
        # Scaffolds should be trimmed or removed before seeds
        scaffold_count = len(trimmed.get("import_scaffolds", []))
        assert scaffold_count < 2 or "import_scaffolds" not in trimmed

    def test_callers_trimmed_before_callees(self) -> None:
        result = {
            "seeds": [
                {
                    "source": "x" * 50,
                    "callees": [{"symbol": "a"}, {"symbol": "b"}],
                    "callers": [{"context": "c" * 200}, {"context": "d" * 200}],
                }
            ],
            "summary": "test",
        }
        trimmed = _trim_to_budget(result, 200)
        seed = trimmed["seeds"][0]
        # Callers trimmed before callees
        caller_count = len(seed.get("callers", []))
        callee_count = len(seed.get("callees", []))
        assert caller_count <= callee_count or callee_count == 0


# ---------------------------------------------------------------------------
# Tool registration test
# ---------------------------------------------------------------------------


class TestReconRegistration:
    """Tests for recon tool registration."""

    def test_register_creates_tool(self) -> None:
        """recon tool registers with FastMCP."""
        from codeplane.mcp.tools.recon import register_tools

        mcp_mock = MagicMock()
        app_ctx = MagicMock()

        # FastMCP.tool returns a decorator
        mcp_mock.tool = MagicMock(return_value=lambda fn: fn)

        register_tools(mcp_mock, app_ctx)

        # Verify mcp.tool was called (to register the recon function)
        assert mcp_mock.tool.called


class TestReconInGate:
    """Tests for recon in TOOL_CATEGORIES."""

    def test_recon_category(self) -> None:
        from codeplane.mcp.gate import TOOL_CATEGORIES

        assert "recon" in TOOL_CATEGORIES
        assert TOOL_CATEGORIES["recon"] == "search"


class TestReconInToolsInit:
    """Tests for recon in tools __init__."""

    def test_recon_importable(self) -> None:
        from codeplane.mcp.tools import recon

        assert hasattr(recon, "register_tools")
