"""Tests for the recon MCP tool.

Tests:
- parse_task: task description parsing (keywords, paths, symbols)
- register_tools: tool wiring
- ArtifactKind: artifact classification
- TaskIntent: intent extraction
- EvidenceRecord: structured evidence
- Negative mentions, stacktrace detection, test-driven detection
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from codeplane.mcp.tools.recon.expansion import _def_signature_text, _read_lines
from codeplane.mcp.tools.recon.models import (
    ArtifactKind,
    EvidenceRecord,
    HarvestCandidate,
    TaskIntent,
    _classify_artifact,
    _extract_intent,
)
from codeplane.mcp.tools.recon.parsing import (
    _detect_stacktrace_driven,
    _detect_test_driven,
    _extract_negative_mentions,
    parse_task,
)

# ---------------------------------------------------------------------------
# Tokenization tests
# ---------------------------------------------------------------------------


class TestTokenizeTask:
    """Tests for parse_task keyword extraction."""

    def test_single_word(self) -> None:
        terms = parse_task("FactQueries").keywords
        assert "factqueries" in terms

    def test_multi_word(self) -> None:
        terms = parse_task("add validation to the search tool").keywords
        assert "validation" in terms
        assert "search" in terms
        # "add", "to", "the", "tool" are stop words → excluded
        assert "add" not in terms
        assert "to" not in terms
        assert "the" not in terms
        assert "tool" not in terms

    def test_camelcase_split(self) -> None:
        terms = parse_task("IndexCoordinatorEngine").keywords
        assert "indexcoordinatorengine" in terms
        # camelCase parts also extracted
        assert "index" in terms
        assert "coordinator" in terms

    def test_snake_case_split(self) -> None:
        terms = parse_task("get_callees").keywords
        assert "get_callees" in terms
        assert "callees" in terms

    def test_quoted_terms_preserved(self) -> None:
        terms = parse_task('fix "read_source" tool').keywords
        assert "read_source" in terms

    def test_stop_words_filtered(self) -> None:
        terms = parse_task("how does the checkpoint tool run tests").keywords
        assert "checkpoint" in terms
        assert "how" not in terms
        assert "does" not in terms
        assert "the" not in terms

    def test_short_terms_filtered(self) -> None:
        terms = parse_task("a b cd ef").keywords
        assert "a" not in terms
        assert "b" not in terms
        assert "cd" in terms
        assert "ef" in terms

    def test_empty_task(self) -> None:
        assert parse_task("").keywords == []

    def test_all_stop_words(self) -> None:
        assert parse_task("the is and or").keywords == []

    def test_dedup(self) -> None:
        terms = parse_task("search search search").keywords
        assert terms.count("search") == 1

    def test_sorted_by_length_descending(self) -> None:
        parsed = parse_task("IndexCoordinatorEngine search lint")
        # primary_terms sorted longest first; secondary may follow
        lengths = [len(t) for t in parsed.primary_terms]
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
        terms = parse_task(task).keywords
        assert expected_term in terms


# ---------------------------------------------------------------------------
# Path extraction tests
# ---------------------------------------------------------------------------


class TestExtractPaths:
    """Tests for parse_task path extraction."""

    def test_backtick_path(self) -> None:
        paths = parse_task(
            "Fix the model in `src/evee/core/base_model.py` to add caching"
        ).explicit_paths
        assert "src/evee/core/base_model.py" in paths

    def test_quoted_path(self) -> None:
        paths = parse_task('Look at "config/models.py" for settings').explicit_paths
        assert "config/models.py" in paths

    def test_bare_path(self) -> None:
        paths = parse_task("The evaluator is in evaluation/model_evaluator.py").explicit_paths
        assert "evaluation/model_evaluator.py" in paths

    def test_multiple_paths(self) -> None:
        task = "Modify `src/core/base_model.py` and `src/config/models.py` to support caching"
        paths = parse_task(task).explicit_paths
        assert "src/core/base_model.py" in paths
        assert "src/config/models.py" in paths

    def test_no_paths(self) -> None:
        paths = parse_task("add caching to the model abstraction").explicit_paths
        assert paths == []

    def test_dotted_but_not_path(self) -> None:
        # Version numbers, URLs etc should not match as paths
        paths = parse_task("upgrade to version 3.12").explicit_paths
        assert paths == []

    def test_strip_leading_dot_slash(self) -> None:
        paths = parse_task("Fix `./src/main.py` please").explicit_paths
        assert "src/main.py" in paths

    def test_dedup(self) -> None:
        paths = parse_task("`config/models.py` and also config/models.py again").explicit_paths
        assert paths.count("config/models.py") == 1

    def test_various_extensions(self) -> None:
        paths = parse_task("Check `src/app.ts` and `lib/utils.js` and `main.go`").explicit_paths
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


# ---------------------------------------------------------------------------
# ArtifactKind classification tests
# ---------------------------------------------------------------------------


class TestArtifactKind:
    """Tests for _classify_artifact."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("src/core/handler.py", ArtifactKind.code),
            ("src/utils.js", ArtifactKind.code),
            ("tests/test_handler.py", ArtifactKind.test),
            ("test/unit/test_core.py", ArtifactKind.test),
            ("src/core/handler_test.py", ArtifactKind.test),
            ("config/settings.yaml", ArtifactKind.config),
            ("app.json", ArtifactKind.config),
            ("pyproject.toml", ArtifactKind.build),
            ("Makefile", ArtifactKind.build),
            ("Dockerfile", ArtifactKind.build),
            ("docs/README.md", ArtifactKind.doc),
            ("CHANGELOG.rst", ArtifactKind.doc),
        ],
    )
    def test_classification(self, path: str, expected: ArtifactKind) -> None:
        assert _classify_artifact(path) == expected


# ---------------------------------------------------------------------------
# TaskIntent tests
# ---------------------------------------------------------------------------


class TestTaskIntent:
    """Tests for _extract_intent."""

    @pytest.mark.parametrize(
        ("task", "expected"),
        [
            ("fix the bug in search handler", TaskIntent.debug),
            ("debug the crash in IndexCoordinatorEngine", TaskIntent.debug),
            ("add caching to the search tool", TaskIntent.implement),
            ("implement a new endpoint for users", TaskIntent.implement),
            ("refactor the recon pipeline", TaskIntent.refactor),
            ("rename IndexCoordinatorEngine to Coordinator", TaskIntent.refactor),
            ("how does the checkpoint tool work", TaskIntent.understand),
            ("explain the search pipeline", TaskIntent.understand),
            ("add tests for the recon tool", TaskIntent.implement),  # "add" -> implement
            ("write unit tests with pytest for search", TaskIntent.test),
            ("increase test coverage for search", TaskIntent.test),
            ("FactQueries", TaskIntent.unknown),
        ],
    )
    def test_intent_extraction(self, task: str, expected: TaskIntent) -> None:
        assert _extract_intent(task) == expected

    def test_parse_task_includes_intent(self) -> None:
        parsed = parse_task("fix the bug in search handler")
        assert parsed.intent == TaskIntent.debug

    def test_parse_task_unknown_intent(self) -> None:
        parsed = parse_task("IndexCoordinatorEngine")
        assert parsed.intent == TaskIntent.unknown


# ---------------------------------------------------------------------------
# EvidenceRecord tests
# ---------------------------------------------------------------------------


class TestEvidenceRecord:
    """Tests for EvidenceRecord dataclass."""

    def test_creation(self) -> None:
        e = EvidenceRecord(category="embedding", detail="semantic similarity 0.850", score=0.85)
        assert e.category == "embedding"
        assert e.score == 0.85

    def test_default_score(self) -> None:
        e = EvidenceRecord(category="explicit", detail="agent seed")
        assert e.score == 0.0


# ---------------------------------------------------------------------------
# HarvestCandidate with new fields tests
# ---------------------------------------------------------------------------


class TestHarvestCandidateNew:
    """Tests for new HarvestCandidate fields."""

    def test_artifact_kind_default(self) -> None:
        c = HarvestCandidate(def_uid="test::func")
        assert c.artifact_kind == ArtifactKind.code

    def test_relevance_score_default(self) -> None:
        c = HarvestCandidate(def_uid="test::func")
        assert c.relevance_score == 0.0
        assert c.seed_score == 0.0

    def test_evidence_accumulation(self) -> None:
        c = HarvestCandidate(
            def_uid="test::func",
            evidence=[
                EvidenceRecord(category="embedding", detail="sim 0.9", score=0.9),
                EvidenceRecord(category="term_match", detail="name match", score=0.5),
            ],
        )
        assert len(c.evidence) == 2
        assert c.evidence[0].category == "embedding"

    def test_evidence_axes_unchanged(self) -> None:
        c = HarvestCandidate(def_uid="test::func", from_term_match=True, from_explicit=True)
        assert c.evidence_axes == 2


# ---------------------------------------------------------------------------
# Negative mentions tests
# ---------------------------------------------------------------------------


class TestNegativeMentions:
    """Tests for _extract_negative_mentions."""

    def test_not_pattern(self) -> None:
        mentions = _extract_negative_mentions("fix the bug not tests")
        assert "tests" in mentions

    def test_exclude_pattern(self) -> None:
        mentions = _extract_negative_mentions("refactor handler exclude logging")
        assert "logging" in mentions

    def test_without_pattern(self) -> None:
        mentions = _extract_negative_mentions("implement feature without caching")
        assert "caching" in mentions

    def test_no_negatives(self) -> None:
        mentions = _extract_negative_mentions("add caching to search")
        assert mentions == []

    def test_multiple_negatives(self) -> None:
        mentions = _extract_negative_mentions("fix handler not tests exclude config")
        assert "tests" in mentions
        assert "config" in mentions

    def test_parse_task_populates_negatives(self) -> None:
        parsed = parse_task("refactor handler not tests")
        assert "tests" in parsed.negative_mentions


# ---------------------------------------------------------------------------
# Stacktrace detection tests
# ---------------------------------------------------------------------------


class TestStacktraceDetection:
    """Tests for _detect_stacktrace_driven."""

    def test_traceback_error(self) -> None:
        assert _detect_stacktrace_driven("fix the traceback error in handler")

    def test_exception_raise(self) -> None:
        assert _detect_stacktrace_driven("ValueError raised in parse_task")

    def test_no_stacktrace(self) -> None:
        assert not _detect_stacktrace_driven("add caching to search")

    def test_single_error_word_insufficient(self) -> None:
        # Single indicator not enough (need 2+)
        assert not _detect_stacktrace_driven("fix the error")

    def test_parse_task_populates_stacktrace(self) -> None:
        parsed = parse_task("fix the traceback error in handler")
        assert parsed.is_stacktrace_driven


# ---------------------------------------------------------------------------
# Test-driven detection tests
# ---------------------------------------------------------------------------


class TestTestDrivenDetection:
    """Tests for _detect_test_driven."""

    def test_write_tests(self) -> None:
        assert _detect_test_driven("write tests for handler", TaskIntent.implement)

    def test_test_intent(self) -> None:
        assert _detect_test_driven("anything", TaskIntent.test)

    def test_not_test_driven(self) -> None:
        assert not _detect_test_driven("add caching", TaskIntent.implement)

    def test_parse_task_populates_test_driven(self) -> None:
        parsed = parse_task("write unit tests for the search tool")
        assert parsed.is_test_driven
