"""Tests for the recon MCP tool.

Tests:
- parse_task: task description parsing (keywords, paths, symbols)
- register_tools: tool wiring
- ArtifactKind: artifact classification
- TaskIntent: intent extraction
- EvidenceRecord: structured evidence
- Negative mentions, stacktrace detection, test-driven detection
- Failure-mode next actions
- File-centric pipeline: OutputTier, FileCandidate, two-elbow scoring, tier assignment
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from codeplane.mcp.tools.recon import (
    ArtifactKind,
    EvidenceRecord,
    FileCandidate,
    HarvestCandidate,
    OutputTier,
    ParsedTask,
    TaskIntent,
    _build_failure_actions,
    _classify_artifact,
    _def_signature_text,
    _detect_stacktrace_driven,
    _detect_test_driven,
    _enrich_file_candidates,
    _extract_intent,
    _extract_negative_mentions,
    _read_lines,
    assign_tiers,
    compute_anchor_floor,
    compute_noise_metric,
    compute_two_elbows,
    find_elbow,
    parse_task,
)
from codeplane.mcp.tools.recon.pipeline import _find_unindexed_files

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
        terms = parse_task("IndexCoordinator").keywords
        assert "indexcoordinator" in terms
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
        parsed = parse_task("IndexCoordinator search lint")
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
            ("debug the crash in IndexCoordinator", TaskIntent.debug),
            ("add caching to the search tool", TaskIntent.implement),
            ("implement a new endpoint for users", TaskIntent.implement),
            ("refactor the recon pipeline", TaskIntent.refactor),
            ("rename IndexCoordinator to Coordinator", TaskIntent.refactor),
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
        parsed = parse_task("IndexCoordinator")
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
        c = HarvestCandidate(def_uid="test::func", from_term_match=True, from_lexical=True)
        assert c.evidence_axes == 2


class TestFindElbow:
    """Tests for find_elbow."""

    def test_small_list(self) -> None:
        assert find_elbow([10.0, 5.0, 1.0]) == 3

    def test_clear_elbow(self) -> None:
        scores = [100.0, 95.0, 90.0, 85.0, 80.0, 10.0, 5.0, 3.0, 2.0, 1.0]
        k = find_elbow(scores)
        assert 3 <= k <= 6

    def test_flat_distribution(self) -> None:
        scores = [10.0, 10.0, 10.0, 10.0, 10.0]
        k = find_elbow(scores)
        assert k == len(scores)

    def test_empty(self) -> None:
        assert find_elbow([]) == 0

    def test_single(self) -> None:
        assert find_elbow([5.0]) == 1

    def test_respects_min_seeds(self) -> None:
        scores = [100.0, 1.0, 1.0, 1.0, 1.0]
        k = find_elbow(scores, min_seeds=3)
        assert k >= 3

    def test_respects_max_seeds(self) -> None:
        scores = [float(x) for x in range(100, 0, -1)]
        k = find_elbow(scores, max_seeds=10)
        assert k <= 10


class TestComputeAnchorFloor:
    """Tests for compute_anchor_floor — anchor-only MAD band."""

    def test_empty(self) -> None:
        assert compute_anchor_floor([], []) == 0.0

    def test_no_anchors(self) -> None:
        assert compute_anchor_floor([10.0, 5.0, 3.0], []) == 0.0

    def test_single_anchor(self) -> None:
        # Single anchor at rank 1 (score 5.0)
        # Anchor scores: [5.0], median=5.0, MAD=0.0
        # floor = 5.0 - 0.0 = 5.0
        floor = compute_anchor_floor([10.0, 5.0, 3.0], [1])
        assert floor == 5.0

    def test_anchor_band_includes_nearby(self) -> None:
        """Simulates #108-like distribution: anchor at rank 4 (0-indexed)."""
        scores = [1.33, 1.02, 0.84, 0.83, 0.81, 0.74, 0.67, 0.59]
        floor = compute_anchor_floor(scores, [4])
        # Single anchor → MAD=0, floor=0.81
        assert floor == scores[4]

    def test_multiple_anchors(self) -> None:
        scores = [10.0, 8.0, 6.0, 4.0, 2.0]
        floor = compute_anchor_floor(scores, [1, 3])
        # Anchor scores: [4.0, 8.0], sorted → [4, 8], median=8
        # Abs devs: [4, 0] → sorted [0, 4] → MAD=4
        # floor = 4.0 - 4.0 = 0.0
        assert floor == 0.0

    def test_anchor_at_top(self) -> None:
        """Anchor at rank 0 — floor should still be sensible."""
        scores = [10.0, 9.0, 8.0, 1.0]
        floor = compute_anchor_floor(scores, [0])
        # Single anchor → MAD=0, floor=10.0
        assert floor == 10.0

    def test_consistent_scores_tight_band(self) -> None:
        """When anchor scores are very similar, MAD is small, band is tight."""
        scores = [5.0, 4.9, 4.8, 4.7, 4.6]
        floor = compute_anchor_floor(scores, [1, 2, 3])
        # Anchor scores: [4.7, 4.8, 4.9], median=4.8
        # Abs devs: [0.1, 0.0, 0.1] → sorted [0, 0.1, 0.1] → MAD=0.1
        # floor = 4.7 - 0.1 = 4.6
        assert floor == pytest.approx(4.6)

    def test_three_anchors_realistic(self) -> None:
        """Three anchors spread across ranking — typical benchmark case."""
        scores = [1.33, 1.02, 0.84, 0.83, 0.81, 0.74, 0.67, 0.59]
        # Anchors at ranks 1, 4, 5 (scores 1.02, 0.81, 0.74)
        floor = compute_anchor_floor(scores, [1, 4, 5])
        # Anchor scores: [0.74, 0.81, 1.02], median=0.81
        # Abs devs: [0.07, 0.0, 0.21] → sorted [0.0, 0.07, 0.21] → MAD=0.07
        # floor = 0.74 - 0.07 = 0.67
        assert floor == pytest.approx(0.67)


class TestElbowBasedFileInclusion:
    """Verify no-anchor file inclusion uses elbow detection.

    When no anchors exist, the pipeline uses ``find_elbow`` on file
    scores to determine the natural cutoff — no arbitrary score-floor
    fractions or patience windows.  This adapts to the score distribution's
    shape rather than using fixed constants.
    """

    def test_clear_elbow_cuts_noise(self) -> None:
        """A sharp drop in scores → elbow catches it."""
        scores = [10.0, 9.0, 8.5, 8.0, 7.5, 2.0, 1.5, 1.0, 0.5, 0.2]
        k = find_elbow(scores, min_seeds=3, max_seeds=10)
        assert 3 <= k <= 6

    def test_flat_distribution_keeps_all(self) -> None:
        """All scores similar → no natural break → keep all."""
        scores = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        k = find_elbow(scores, min_seeds=3, max_seeds=6)
        assert k == 6

    def test_steep_drop_includes_few(self) -> None:
        """One dominant file, rest noise → few files included."""
        scores = [20.0, 1.0, 0.5, 0.3, 0.2, 0.1]
        k = find_elbow(scores, min_seeds=3, max_seeds=6)
        assert k >= 3  # min_seeds enforced

    def test_gradual_decay_includes_more(self) -> None:
        """Gradual score decay without sharp break → more included."""
        scores = [float(x) for x in range(20, 0, -1)]
        k = find_elbow(scores, min_seeds=3, max_seeds=15)
        assert k >= 3

    def test_min_seeds_respected(self) -> None:
        """Even with a steep drop, min_seeds is honoured."""
        scores = [100.0, 1.0, 0.5, 0.3, 0.2]
        k = find_elbow(scores, min_seeds=3, max_seeds=5)
        assert k >= 3

    def test_max_seeds_caps(self) -> None:
        """Elbow can't exceed max_seeds."""
        scores = [float(x) for x in range(100, 0, -1)]
        k = find_elbow(scores, min_seeds=3, max_seeds=10)
        assert k <= 10


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


class TestFailureActions:
    """Tests for _build_failure_actions."""

    def test_with_terms(self) -> None:
        actions = _build_failure_actions(["search", "handler"], [])
        assert any(a["action"] == "search" for a in actions)

    def test_with_paths(self) -> None:
        actions = _build_failure_actions([], ["src/handler.py"])
        assert any(a["action"] == "read_source" for a in actions)

    def test_always_has_recon_retry(self) -> None:
        actions = _build_failure_actions([], [])
        assert any(a["action"] == "recon" for a in actions)

    def test_always_has_map_repo(self) -> None:
        actions = _build_failure_actions([], [])
        assert any(a["action"] == "map_repo" for a in actions)


# ---------------------------------------------------------------------------
# Unindexed file discovery tests
# ---------------------------------------------------------------------------


class TestFindUnindexedFiles:
    """Tests for _find_unindexed_files — path-based discovery of non-indexed files."""

    @staticmethod
    def _make_app_ctx(tracked: list[str]) -> MagicMock:
        ctx = MagicMock()
        ctx.git_ops.tracked_files.return_value = tracked
        return ctx

    def test_matches_yaml_by_term(self) -> None:
        """YAML file with matching path component is found."""
        parsed = ParsedTask(raw="", primary_terms=["config", "mlflow"], secondary_terms=[])
        ctx = self._make_app_ctx(
            [
                "src/app.py",
                "config/mlflow.yaml",
                "README.md",
            ]
        )
        indexed = {"src/app.py"}
        result = _find_unindexed_files(ctx, parsed, indexed)
        paths = [p for p, _ in result]
        assert "config/mlflow.yaml" in paths

    def test_excludes_indexed_files(self) -> None:
        """Files already in the structural index are excluded."""
        parsed = ParsedTask(raw="", primary_terms=["config"], secondary_terms=[])
        ctx = self._make_app_ctx(["src/config.py", "config.yaml"])
        indexed = {"src/config.py"}
        result = _find_unindexed_files(ctx, parsed, indexed)
        paths = [p for p, _ in result]
        assert "src/config.py" not in paths
        assert "config.yaml" in paths

    def test_no_terms_returns_empty(self) -> None:
        """No terms to match → empty result."""
        parsed = ParsedTask(raw="", primary_terms=[], secondary_terms=[])
        ctx = self._make_app_ctx(["config.yaml"])
        result = _find_unindexed_files(ctx, parsed, set())
        assert result == []

    def test_sorted_by_score_desc(self) -> None:
        """Results sorted by score descending."""
        parsed = ParsedTask(
            raw="", primary_terms=["config", "mlflow", "tracking"], secondary_terms=[]
        )
        ctx = self._make_app_ctx(
            [
                "config.yaml",  # matches "config"
                "config/mlflow/tracking.yaml",  # matches all 3
                "README.md",
            ]
        )
        result = _find_unindexed_files(ctx, parsed, set())
        if len(result) >= 2:
            assert result[0][1] >= result[1][1]

    def test_caps_at_limit(self) -> None:
        """Results capped at _UNINDEXED_MAX_FILES."""
        parsed = ParsedTask(raw="", primary_terms=["test"], secondary_terms=[])
        files = [f"test/file{i}.yaml" for i in range(30)]
        ctx = self._make_app_ctx(files)
        result = _find_unindexed_files(ctx, parsed, set())
        assert len(result) <= 15

    def test_substring_match(self) -> None:
        """Terms match as substrings in path."""
        parsed = ParsedTask(raw="", primary_terms=["integration"], secondary_terms=[])
        ctx = self._make_app_ctx(
            [
                ".github/workflows/integration-tests.yml",
                "README.md",
            ]
        )
        result = _find_unindexed_files(ctx, parsed, set())
        paths = [p for p, _ in result]
        assert ".github/workflows/integration-tests.yml" in paths


# ---------------------------------------------------------------------------
# OutputTier and FileCandidate tests
# ---------------------------------------------------------------------------


class TestOutputTier:
    """Test OutputTier enum values."""

    def test_tier_values(self) -> None:
        assert OutputTier.FULL_FILE.value == "full_file"
        assert OutputTier.MIN_SCAFFOLD.value == "min_scaffold"
        assert OutputTier.SUMMARY_ONLY.value == "summary_only"

    def test_tier_is_str(self) -> None:
        assert isinstance(OutputTier.FULL_FILE, str)


class TestFileCandidate:
    """Test FileCandidate dataclass."""

    def test_default_tier(self) -> None:
        fc = FileCandidate(path="foo.py", similarity=0.8)
        assert fc.tier == OutputTier.SUMMARY_ONLY
        assert fc.combined_score == 0.0

    def test_evidence_summary(self) -> None:
        fc = FileCandidate(
            path="foo.py",
            similarity=0.85,
            term_match_count=3,
            lexical_hit_count=2,
            has_explicit_mention=True,
            graph_connected=True,
        )
        ev = fc.evidence_summary
        assert "sim(0.85)" in ev
        assert "terms(3)" in ev
        assert "lex(2)" in ev
        assert "explicit" in ev
        assert "graph" in ev

    def test_evidence_summary_empty(self) -> None:
        fc = FileCandidate(path="foo.py")
        assert fc.evidence_summary == ""


# ---------------------------------------------------------------------------
# RRF enrichment tests
# ---------------------------------------------------------------------------


class TestEnrichFileCandidatesRRF:
    """Test _enrich_file_candidates with Reciprocal Rank Fusion scoring."""

    _RRF_K = 60  # must match the constant in harvesters.py
    _EMB_W = 2.0  # embedding weight multiplier

    @staticmethod
    def _parsed(explicit_paths: list[str] | None = None) -> ParsedTask:
        return ParsedTask(raw="test task", explicit_paths=explicit_paths or [])

    def test_embedding_only(self) -> None:
        """Files with only embedding signal get RRF from embedding source only."""
        fcs = [
            FileCandidate(path="a.py", similarity=0.9),
            FileCandidate(path="b.py", similarity=0.7),
        ]
        result = _enrich_file_candidates(fcs, {}, self._parsed())
        # a.py is rank 1, b.py is rank 2 — embedding has 2× weight
        assert result[0].combined_score == pytest.approx(self._EMB_W / (self._RRF_K + 1))
        assert result[1].combined_score == pytest.approx(self._EMB_W / (self._RRF_K + 2))

    def test_multi_source_boosts_score(self) -> None:
        """A file with both embedding and term_match signals gets higher RRF."""
        fcs = [
            FileCandidate(path="a.py", similarity=0.9),
            FileCandidate(path="b.py", similarity=0.7),
        ]
        def_cands = {
            "b.py::func": HarvestCandidate(
                def_uid="b.py::func",
                file_path="b.py",
                matched_terms={"foo", "bar"},
                from_term_match=True,
                term_idf_score=1.5,
            ),
        }
        result = _enrich_file_candidates(fcs, def_cands, self._parsed())
        score_a = result[0].combined_score
        score_b = result[1].combined_score
        # b.py has two sources (embedding rank 2 + term rank 1) vs a.py (embedding rank 1 only)
        assert score_b > score_a

    def test_explicit_mention_gives_rrf_contribution(self) -> None:
        """Explicit mention adds an RRF source contribution at rank 1."""
        fcs = [
            FileCandidate(path="a.py", similarity=0.9),
            FileCandidate(path="b.py", similarity=0.7),
        ]
        result = _enrich_file_candidates(fcs, {}, self._parsed(explicit_paths=["b.py"]))
        score_b = next(fc for fc in result if fc.path == "b.py")
        # b.py: embedding rank 2 (2× weight) + explicit rank 1
        expected = self._EMB_W / (self._RRF_K + 2) + 1.0 / (self._RRF_K + 1)
        assert score_b.combined_score == pytest.approx(expected)
        assert score_b.has_explicit_mention is True

    def test_graph_connected_gives_graded_rrf(self) -> None:
        """Graph connection adds a graded RRF contribution based on edge quality."""
        fcs = [
            FileCandidate(path="a.py", similarity=0.9),
        ]
        def_cands = {
            "a.py::cls": HarvestCandidate(
                def_uid="a.py::cls",
                file_path="a.py",
                from_graph=True,
                graph_quality=0.8,
            ),
        }
        result = _enrich_file_candidates(fcs, def_cands, self._parsed())
        score = result[0].combined_score
        # embedding rank 1 (2× weight) + graph rank 1
        expected = self._EMB_W / (self._RRF_K + 1) + 1.0 / (self._RRF_K + 1)
        assert score == pytest.approx(expected)
        assert result[0].graph_connected is True

    def test_graph_quality_affects_rank(self) -> None:
        """Files with higher graph_quality rank higher in graph source."""
        fcs = [
            FileCandidate(path="a.py", similarity=0.5),
            FileCandidate(path="b.py", similarity=0.5),
        ]
        def_cands = {
            "a.py::f": HarvestCandidate(
                def_uid="a.py::f",
                file_path="a.py",
                from_graph=True,
                graph_quality=1.0,
            ),
            "b.py::f": HarvestCandidate(
                def_uid="b.py::f",
                file_path="b.py",
                from_graph=True,
                graph_quality=0.3,
            ),
        }
        result = _enrich_file_candidates(fcs, def_cands, self._parsed())
        scores = {fc.path: fc.combined_score for fc in result}
        # a.py has graph rank 1 (quality 1.0), b.py has graph rank 2 (quality 0.3)
        assert scores["a.py"] > scores["b.py"]

    def test_secondary_only_file_added(self) -> None:
        """A file found only by secondary harvesters gets added with RRF score."""
        fcs = [
            FileCandidate(path="a.py", similarity=0.9),
        ]
        def_cands = {
            "new.py::func": HarvestCandidate(
                def_uid="new.py::func",
                file_path="new.py",
                matched_terms={"baz"},
                from_term_match=True,
                term_idf_score=1.0,
            ),
        }
        result = _enrich_file_candidates(fcs, def_cands, self._parsed())
        paths = {fc.path for fc in result}
        assert "new.py" in paths
        new_fc = next(fc for fc in result if fc.path == "new.py")
        assert new_fc.similarity == 0.0
        # Only has term_match source at rank 1
        assert new_fc.combined_score == pytest.approx(1.0 / (self._RRF_K + 1))

    def test_all_five_sources(self) -> None:
        """A file with all 5 RRF sources gets the maximum contribution."""
        fcs = [
            FileCandidate(path="a.py", similarity=0.9),
        ]
        def_cands = {
            "a.py::f1": HarvestCandidate(
                def_uid="a.py::f1",
                file_path="a.py",
                matched_terms={"x"},
                lexical_hit_count=1,
                from_explicit=True,
                from_graph=True,
                from_term_match=True,
                from_lexical=True,
                term_idf_score=1.0,
                graph_quality=0.9,
            ),
        }
        result = _enrich_file_candidates(fcs, def_cands, self._parsed())
        score = result[0].combined_score
        # embedding(2×) + term + lex + graph + explicit, all at rank 1
        expected = (self._EMB_W + 1.0 + 1.0 + 1.0 + 1.0) / (self._RRF_K + 1)
        assert score == pytest.approx(expected)

    def test_embedding_weight_dominates(self) -> None:
        """Embedding 2× weight means embedding rank 1 outscores other rank 1s."""
        fcs = [
            FileCandidate(path="a.py", similarity=0.9),
        ]
        result = _enrich_file_candidates(fcs, {}, self._parsed())
        # Embedding-only rank 1 = 2/(60+1) ≈ 0.0328
        # Compare with what a single non-embedding source would give: 1/(60+1) ≈ 0.0164
        assert result[0].combined_score > 1.0 / (self._RRF_K + 1)

    def test_signal_fields_populated(self) -> None:
        """Signal display fields (term_match_count etc.) are still populated."""
        fcs = [
            FileCandidate(path="a.py", similarity=0.8),
        ]
        def_cands = {
            "a.py::f": HarvestCandidate(
                def_uid="a.py::f",
                file_path="a.py",
                matched_terms={"foo", "bar"},
                lexical_hit_count=3,
                from_explicit=True,
                from_graph=True,
                from_term_match=True,
                from_lexical=True,
                graph_quality=0.5,
            ),
        }
        result = _enrich_file_candidates(fcs, def_cands, self._parsed())
        fc = result[0]
        assert fc.term_match_count == 2
        assert fc.lexical_hit_count == 3
        assert fc.has_explicit_mention is True
        assert fc.graph_connected is True

    def test_ranking_preserved_for_tied_embedding(self) -> None:
        """Files with equal embedding similarity still get distinct embedding ranks."""
        fcs = [
            FileCandidate(path="a.py", similarity=0.5),
            FileCandidate(path="b.py", similarity=0.5),
        ]
        result = _enrich_file_candidates(fcs, {}, self._parsed())
        # Both have embedding signal — one is rank 1, other rank 2
        scores = {fc.path: fc.combined_score for fc in result}
        assert len(set(scores.values())) == 2  # distinct scores

    def test_empty_candidates(self) -> None:
        """Empty candidates list returns empty."""
        result = _enrich_file_candidates([], {}, self._parsed())
        assert result == []


# ---------------------------------------------------------------------------
# Two-elbow detection tests
# ---------------------------------------------------------------------------


class TestTwoElbows:
    """Test compute_two_elbows function."""

    def test_empty_scores(self) -> None:
        n_full, n_scaffold = compute_two_elbows([])
        assert n_full == 0
        assert n_scaffold == 0

    def test_small_list(self) -> None:
        """Lists smaller than min_full are returned entirely."""
        n_full, n_scaffold = compute_two_elbows([0.9], min_full=2)
        assert n_full == 1
        assert n_scaffold == 1

    def test_clear_two_elbows(self) -> None:
        """Distribution with two clear drops should produce two elbows."""
        # 3 high scores, 3 medium, 4 low
        scores = [0.9, 0.88, 0.85, 0.5, 0.48, 0.45, 0.1, 0.08, 0.05, 0.03]
        n_full, n_scaffold = compute_two_elbows(scores, min_full=1, min_total=2)
        assert n_full >= 1
        assert n_scaffold >= n_full
        assert n_scaffold <= len(scores)

    def test_flat_distribution(self) -> None:
        """Flat distribution → everything in full tier."""
        scores = [0.5, 0.49, 0.48, 0.47, 0.46]
        n_full, n_scaffold = compute_two_elbows(scores, min_full=2)
        # Flat → no clear elbow, should include most
        assert n_full >= 2

    def test_n_scaffold_gte_n_full(self) -> None:
        """n_scaffold is always >= n_full."""
        import random

        random.seed(42)
        for _ in range(20):
            scores = sorted([random.random() for _ in range(15)], reverse=True)
            n_full, n_scaffold = compute_two_elbows(scores, min_full=1, min_total=2)
            assert n_scaffold >= n_full

    def test_min_full_respected(self) -> None:
        """min_full is always respected."""
        scores = [0.9, 0.1, 0.05, 0.01, 0.005]
        n_full, _ = compute_two_elbows(scores, min_full=3)
        assert n_full >= 3


class TestAssignTiers:
    """Test assign_tiers function."""

    def test_empty_candidates(self) -> None:
        result = assign_tiers([])
        assert result == []

    def test_tiers_are_assigned(self) -> None:
        candidates = [
            FileCandidate(path=f"f{i}.py", combined_score=1.0 - i * 0.1) for i in range(10)
        ]
        result = assign_tiers(candidates)
        tiers = [c.tier for c in result]
        assert OutputTier.FULL_FILE in tiers
        # Should be sorted by score descending
        scores = [c.combined_score for c in result]
        assert scores == sorted(scores, reverse=True)

    def test_explicit_promotion(self) -> None:
        """Explicit mentions should be promoted to at least MIN_SCAFFOLD."""
        candidates = [
            FileCandidate(path="high.py", combined_score=0.9),
            FileCandidate(path="low.py", combined_score=0.01, has_explicit_mention=True),
        ]
        result = assign_tiers(candidates)
        low = next(c for c in result if c.path == "low.py")
        assert low.tier != OutputTier.SUMMARY_ONLY  # promoted


class TestNoiseMetric:
    """Test compute_noise_metric function."""

    def test_empty_scores(self) -> None:
        assert compute_noise_metric([]) == 1.0
        assert compute_noise_metric([0.5]) == 1.0

    def test_clear_signal(self) -> None:
        """Strong top-heavy distribution → low noise."""
        scores = [0.95, 0.90, 0.85, 0.1, 0.05, 0.02, 0.01]
        noise = compute_noise_metric(scores)
        assert noise < 0.5

    def test_noisy_signal(self) -> None:
        """Flat distribution → high noise."""
        scores = [0.5, 0.49, 0.48, 0.47, 0.46, 0.45, 0.44]
        noise = compute_noise_metric(scores)
        assert noise > 0.3


# ---------------------------------------------------------------------------
# Consecutive recon call gating tests
# ---------------------------------------------------------------------------


class TestReconCallGating:
    """Tests for consecutive recon call gating logic."""

    def _make_app_ctx(self, consecutive: int = 0) -> Any:
        """Build a mock app_ctx with a session containing the given recon counter."""
        from codeplane.mcp.session import SessionState

        session = SessionState(
            session_id="test-session",
            created_at=0.0,
            last_active=0.0,
        )
        session.counters["recon_consecutive"] = consecutive

        session_manager = MagicMock()
        session_manager.get_or_create.return_value = session

        app_ctx = MagicMock()
        app_ctx.session_manager = session_manager
        return app_ctx

    def _make_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.session_id = "test-session"
        return ctx

    def test_first_call_no_gate(self) -> None:
        """First recon call (counter=0) should not be gated."""
        from codeplane.mcp.tools.recon.pipeline import _check_recon_gate

        result = _check_recon_gate(
            self._make_app_ctx(0),
            self._make_ctx(),
            expand_reason=None,
            pinned_paths=None,
            gate_token=None,
            gate_reason=None,
        )
        assert result is None

    def test_second_call_without_reason_blocked(self) -> None:
        """2nd call without expand_reason should be blocked."""
        from codeplane.mcp.tools.recon.pipeline import _check_recon_gate

        result = _check_recon_gate(
            self._make_app_ctx(1),
            self._make_ctx(),
            expand_reason=None,
            pinned_paths=None,
            gate_token=None,
            gate_reason=None,
        )
        assert result is not None
        assert result["status"] == "blocked"
        assert "RECON_FOLLOW_UP" in result["error"]["code"]

    def test_second_call_short_reason_blocked(self) -> None:
        """2nd call with too-short expand_reason should be blocked."""
        from codeplane.mcp.tools.recon.pipeline import _check_recon_gate

        result = _check_recon_gate(
            self._make_app_ctx(1),
            self._make_ctx(),
            expand_reason="too short",
            pinned_paths=["src/foo.py"],
            gate_token=None,
            gate_reason=None,
        )
        assert result is not None
        assert result["status"] == "blocked"

    def test_second_call_no_pinned_paths_blocked(self) -> None:
        """2nd call without pinned_paths should be blocked."""
        from codeplane.mcp.tools.recon.pipeline import _check_recon_gate

        reason = "x" * 300  # meets min length
        result = _check_recon_gate(
            self._make_app_ctx(1),
            self._make_ctx(),
            expand_reason=reason,
            pinned_paths=None,
            gate_token=None,
            gate_reason=None,
        )
        assert result is not None
        assert result["status"] == "blocked"

    def test_second_call_with_valid_reason_passes(self) -> None:
        """2nd call with valid expand_reason + pinned_paths passes."""
        from codeplane.mcp.tools.recon.pipeline import _check_recon_gate

        reason = "x" * 300
        result = _check_recon_gate(
            self._make_app_ctx(1),
            self._make_ctx(),
            expand_reason=reason,
            pinned_paths=["src/core/base.py"],
            gate_token=None,
            gate_reason=None,
        )
        assert result is None

    def test_third_call_no_gate_token_blocked(self) -> None:
        """3rd call without gate_token should issue a gate."""
        from codeplane.mcp.tools.recon.pipeline import _check_recon_gate

        result = _check_recon_gate(
            self._make_app_ctx(2),
            self._make_ctx(),
            expand_reason="x" * 300,
            pinned_paths=["src/core/base.py"],
            gate_token=None,
            gate_reason=None,
        )
        assert result is not None
        assert result["status"] == "blocked"
        assert "gate" in result

    def test_third_call_no_pinned_paths_blocked(self) -> None:
        """3rd call without pinned_paths should be blocked."""
        from codeplane.mcp.tools.recon.pipeline import _check_recon_gate

        result = _check_recon_gate(
            self._make_app_ctx(2),
            self._make_ctx(),
            expand_reason="x" * 300,
            pinned_paths=None,
            gate_token=None,
            gate_reason=None,
        )
        assert result is not None
        assert "RECON_EXCESSIVE" in result["error"]["code"]

    def test_third_call_with_valid_gate_passes(self) -> None:
        """3rd call with valid gate token + reason passes."""
        from codeplane.mcp.gate import GateSpec
        from codeplane.mcp.session import SessionState
        from codeplane.mcp.tools.recon.pipeline import _check_recon_gate

        session = SessionState(
            session_id="test-session",
            created_at=0.0,
            last_active=0.0,
        )
        session.counters["recon_consecutive"] = 2

        # Issue a gate to get a valid token
        gate_spec = GateSpec(
            kind="recon_repeat",
            reason_min_chars=500,
            reason_prompt="test",
        )
        gate_block = session.gate_manager.issue(gate_spec)
        token = gate_block["id"]

        session_manager = MagicMock()
        session_manager.get_or_create.return_value = session

        app_ctx = MagicMock()
        app_ctx.session_manager = session_manager

        result = _check_recon_gate(
            app_ctx,
            self._make_ctx(),
            expand_reason="x" * 600,
            pinned_paths=["src/core/base.py"],
            gate_token=token,
            gate_reason="x" * 600,
        )
        assert result is None

    def test_env_var_bypass(self) -> None:
        """CODEPLANE_RECON_GATE_BYPASS=1 should skip gating logic."""
        from codeplane.mcp.tools.recon.pipeline import _check_recon_gate

        # Counter=5 would normally block hard
        # But _check_recon_gate is not called when bypass is active —
        # the bypass is in the recon() function itself. Verify the
        # gating function properly blocks when bypass is NOT active.
        result = _check_recon_gate(
            self._make_app_ctx(5),
            self._make_ctx(),
            expand_reason=None,
            pinned_paths=None,
            gate_token=None,
            gate_reason=None,
        )
        assert result is not None  # Should block without bypass
