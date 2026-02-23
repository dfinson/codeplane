"""Tests for the recon MCP tool.

Tests:
- parse_task: task description parsing (keywords, paths, symbols)
- _select_seeds: term-intersection + hub-score reranking
- _expand_seed: graph expansion
- _trim_to_budget: budget assembly
- _summarize_recon: summary generation
- register_tools: tool wiring
- ArtifactKind: artifact classification
- TaskIntent: intent extraction
- EvidenceRecord: structured evidence
- _apply_filters: query-conditioned filter pipeline (OR gate + negative gating)
- _score_candidates: bounded scoring model
- Negative mentions, stacktrace detection, test-driven detection
- Failure-mode next actions
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codeplane.mcp.tools.recon import (
    ArtifactKind,
    EvidenceRecord,
    HarvestCandidate,
    ParsedTask,
    TaskIntent,
    _aggregate_to_files,
    _build_evidence_string,
    _build_failure_actions,
    _classify_artifact,
    _def_signature_text,
    _detect_stacktrace_driven,
    _detect_test_driven,
    _estimate_bytes,
    _extract_intent,
    _extract_negative_mentions,
    _read_lines,
    _score_candidates,
    _summarize_recon,
    _trim_to_budget,
    find_elbow,
    find_gap_cutoff,
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
        paths = parse_task("Fix the model in `src/evee/core/base_model.py` to add caching").explicit_paths
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
                    "callees": ["func_a [a.py:1-10]", "func_b [b.py:5-20]"],
                    "callers": ["c.py:" + "c" * 200, "d.py:" + "d" * 200],
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
        e = EvidenceRecord(
            category="embedding",
            detail="semantic similarity 0.850",
            score=0.85,
        )
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
        c = HarvestCandidate(
            def_uid="test::func",
            from_embedding=True,
            from_term_match=True,
        )
        assert c.evidence_axes == 2


# ---------------------------------------------------------------------------
# Scoring model tests
# ---------------------------------------------------------------------------


class TestBoundedScoring:
    """Tests for bounded scoring model."""

    def _make_candidate(
        self,
        uid: str = "test::func",
        *,
        emb_sim: float = 0.0,
        hub: int = 0,
        terms: int = 0,
        from_embedding: bool = False,
        from_explicit: bool = False,
        is_test: bool = False,
        name: str = "func",
        file_path: str = "src/core.py",
    ) -> HarvestCandidate:
        d = MagicMock()
        d.name = name
        d.kind = "function"
        return HarvestCandidate(
            def_uid=uid,
            def_fact=d,
            embedding_similarity=emb_sim,
            hub_score=hub,
            matched_terms={f"t{i}" for i in range(terms)},
            from_embedding=from_embedding,
            from_explicit=from_explicit,
            is_test=is_test,
            file_path=file_path,
            artifact_kind=_classify_artifact(file_path),
        )

    def test_scores_are_bounded(self) -> None:
        """All scores should be in reasonable bounded range."""
        c = self._make_candidate(
            emb_sim=1.0,
            hub=100,
            terms=10,
            from_embedding=True,
            from_explicit=True,
        )
        parsed = parse_task("test task")
        scored = _score_candidates({"test::func": c}, parsed)
        assert len(scored) == 1
        _, score = scored[0]
        # Bounded scoring means score shouldn't explode
        assert 0 <= score <= 2.0

    def test_explicit_does_not_inflate_relevance(self) -> None:
        """Explicit mentions should NOT inflate relevance score.

        Explicit files survive via anchoring, not scoring.  Including
        f_explicit in relevance creates an artificial cluster boundary
        that distorts the gap cutoff.
        """
        c_explicit = self._make_candidate(
            uid="a",
            from_explicit=True,
            emb_sim=0.5,
        )
        c_embed = self._make_candidate(
            uid="b",
            from_embedding=True,
            emb_sim=0.5,
        )
        parsed = parse_task("test task")
        _score_candidates({"a": c_explicit, "b": c_embed}, parsed)
        # Same embedding → same relevance (explicit doesn't inflate)
        assert abs(c_explicit.relevance_score - c_embed.relevance_score) < 0.01

    def test_hub_score_affects_seed_score(self) -> None:
        """Higher hub score should increase seed_score."""
        c_hub = self._make_candidate(uid="a", hub=20, from_embedding=True, emb_sim=0.5)
        c_leaf = self._make_candidate(uid="b", hub=0, from_embedding=True, emb_sim=0.5)
        parsed = parse_task("test task")
        _score_candidates({"a": c_hub, "b": c_leaf}, parsed)
        assert c_hub.seed_score > c_leaf.seed_score

    def test_test_file_downranked_for_implement(self) -> None:
        """Test files should be lower-ranked for implementation tasks."""
        c_code = self._make_candidate(
            uid="a",
            from_embedding=True,
            emb_sim=0.6,
            file_path="src/handler.py",
        )
        c_test = self._make_candidate(
            uid="b",
            from_embedding=True,
            emb_sim=0.6,
            file_path="tests/test_handler.py",
            is_test=True,
        )
        parsed = parse_task("implement caching in handler")
        scored = _score_candidates({"a": c_code, "b": c_test}, parsed)
        scores = dict(scored)
        assert scores["a"] > scores["b"]

    def test_separated_relevance_and_seed_scores(self) -> None:
        """relevance_score and seed_score should be set on candidates."""
        c = self._make_candidate(
            from_embedding=True,
            emb_sim=0.7,
            hub=5,
        )
        parsed = parse_task("test task")
        _score_candidates({"test::func": c}, parsed)
        assert c.relevance_score > 0
        assert c.seed_score > 0
        # seed_score incorporates hub-based multiplier
        assert c.seed_score != c.relevance_score


# ---------------------------------------------------------------------------
# File-level aggregation tests
# ---------------------------------------------------------------------------


class TestAggregateToFiles:
    """Tests for _aggregate_to_files — distribution-relative file scoring."""

    def _make_candidate(
        self,
        uid: str,
        file_id: int,
        name: str = "func",
    ) -> HarvestCandidate:
        d = MagicMock()
        d.name = name
        d.file_id = file_id
        d.kind = "function"
        return HarvestCandidate(def_uid=uid, def_fact=d)

    def test_empty_input(self) -> None:
        assert _aggregate_to_files([], {}) == []

    def test_single_file_single_def(self) -> None:
        c = self._make_candidate("a::f1", file_id=1)
        scored = [("a::f1", 0.5)]
        result = _aggregate_to_files(scored, {"a::f1": c})
        assert len(result) == 1
        fid, fscore, defs = result[0]
        assert fid == 1
        assert fscore == 0.5
        assert len(defs) == 1

    def test_file_with_many_defs_above_median(self) -> None:
        """File with many defs above global median should accumulate more signal."""
        # File A: 1 def with high score
        ca = self._make_candidate("a::f1", file_id=1)
        # File B: 3 defs with moderate scores (all above what will be median)
        cb1 = self._make_candidate("b::f1", file_id=2)
        cb2 = self._make_candidate("b::f2", file_id=2)
        cb3 = self._make_candidate("b::f3", file_id=2)
        # File C: 2 defs with low scores (below median)
        cc1 = self._make_candidate("c::f1", file_id=3)
        cc2 = self._make_candidate("c::f2", file_id=3)

        candidates = {
            "a::f1": ca,
            "b::f1": cb1,
            "b::f2": cb2,
            "b::f3": cb3,
            "c::f1": cc1,
            "c::f2": cc2,
        }
        # Scores: median of [0.30, 0.20, 0.19, 0.18, 0.05, 0.04] = 0.19
        scored = [
            ("a::f1", 0.30),
            ("b::f1", 0.20),
            ("b::f2", 0.19),
            ("b::f3", 0.18),
            ("c::f1", 0.05),
            ("c::f2", 0.04),
        ]
        result = _aggregate_to_files(scored, candidates)

        # File B has 2 defs above median (0.20, 0.19 >= 0.19) → m=2 → score=0.39
        # File A has 1 def above median (0.30 >= 0.19) → m=1 → score=0.30
        # File C has 0 defs above median → m=1 → score=0.05
        assert result[0][0] == 2  # File B first (breadth wins)
        assert result[1][0] == 1  # File A second
        assert result[2][0] == 3  # File C last

    def test_m_capped_at_three(self) -> None:
        """m should not exceed 3 even with many qualifying defs."""
        cands = {}
        scored = []
        # File with 5 defs, all above median
        for i in range(5):
            uid = f"a::f{i}"
            c = self._make_candidate(uid, file_id=1)
            cands[uid] = c
            scored.append((uid, 0.5 - i * 0.01))

        result = _aggregate_to_files(scored, cands)
        assert len(result) == 1
        fid, fscore, defs = result[0]
        # m=3 (capped), so score = 0.50 + 0.49 + 0.48 = 1.47
        assert abs(fscore - (0.50 + 0.49 + 0.48)) < 0.001

    def test_sorting_is_stable_on_file_id(self) -> None:
        """Files with equal scores sort by file_id (deterministic)."""
        c1 = self._make_candidate("a::f1", file_id=10)
        c2 = self._make_candidate("b::f1", file_id=5)
        scored = [("a::f1", 0.3), ("b::f1", 0.3)]
        result = _aggregate_to_files(scored, {"a::f1": c1, "b::f1": c2})
        assert result[0][0] == 5  # Lower file_id first on tie
        assert result[1][0] == 10

    def test_no_additive_constants(self) -> None:
        """File score is purely sum of top-m def scores — no additive bumps."""
        c = self._make_candidate("a::f1", file_id=1)
        scored = [("a::f1", 0.42)]
        result = _aggregate_to_files(scored, {"a::f1": c})
        _, fscore, _ = result[0]
        # Must be exactly the def score — no additive constant
        assert fscore == 0.42

    def test_defs_sorted_descending_in_result(self) -> None:
        """Within each file, defs should be sorted by score descending."""
        c1 = self._make_candidate("a::f1", file_id=1)
        c2 = self._make_candidate("a::f2", file_id=1)
        c3 = self._make_candidate("a::f3", file_id=1)
        cands = {"a::f1": c1, "a::f2": c2, "a::f3": c3}
        scored = [("a::f1", 0.1), ("a::f2", 0.5), ("a::f3", 0.3)]
        result = _aggregate_to_files(scored, cands)
        _, _, defs = result[0]
        scores = [s for _, s in defs]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Filter pipeline tests
# ---------------------------------------------------------------------------


class TestFilterPipeline:
    """Tests for intent-aware filter pipeline."""

    def test_legacy_dual_gate_still_works(self) -> None:
        """The legacy _apply_dual_gate wrapper should still function."""
        from codeplane.mcp.tools.recon import _apply_dual_gate

        c = HarvestCandidate(
            def_uid="test::func",
            from_embedding=True,
            embedding_similarity=0.5,
            hub_score=5,
        )
        result = _apply_dual_gate({"test::func": c})
        # With unknown intent, falls back to dual-gate behavior
        # This candidate has both semantic and structural evidence
        assert "test::func" in result

    def test_explicit_always_passes(self) -> None:
        from codeplane.mcp.tools.recon import _apply_filters

        c = HarvestCandidate(
            def_uid="test::func",
            from_explicit=True,
        )
        parsed = ParsedTask(raw="", intent=TaskIntent.unknown)
        result = _apply_filters({"test::func": c}, parsed)
        assert "test::func" in result


# ---------------------------------------------------------------------------
# Elbow detection tests (existing + new)
# ---------------------------------------------------------------------------


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


class TestFindGapCutoff:
    """Tests for find_gap_cutoff — distribution-relative cutoff."""

    def test_empty(self) -> None:
        assert find_gap_cutoff([]) == 0

    def test_single(self) -> None:
        assert find_gap_cutoff([5.0]) == 1

    def test_small_list(self) -> None:
        assert find_gap_cutoff([10.0, 5.0]) == 2

    def test_clear_gap(self) -> None:
        # Big gap between 80 and 10
        scores = [100.0, 95.0, 90.0, 85.0, 80.0, 10.0, 5.0, 3.0]
        k = find_gap_cutoff(scores, min_keep=2)
        assert k == 5  # Cut at the gap between 80 and 10

    def test_flat_distribution_keeps_all_above_median(self) -> None:
        scores = [10.0, 10.0, 10.0, 10.0, 10.0]
        k = find_gap_cutoff(scores)
        assert k == len(scores)  # No gaps, median fallback keeps all

    def test_no_upper_bound(self) -> None:
        """Gap cutoff has no arbitrary upper limit."""
        # 30 items with consistent scores, then a gap
        scores = [50.0 - i * 0.5 for i in range(30)] + [5.0, 4.0, 3.0]
        k = find_gap_cutoff(scores, min_keep=2)
        assert k >= 20  # Should keep most of the 30 consistent items

    def test_respects_min_keep(self) -> None:
        scores = [100.0, 1.0, 1.0, 1.0]
        k = find_gap_cutoff(scores, min_keep=3)
        assert k >= 3


class TestBuildEvidenceString:
    """Tests for _build_evidence_string — compact evidence format."""

    def test_embedding_only(self) -> None:
        cand = HarvestCandidate(def_uid="a::f1")
        cand.from_embedding = True
        cand.embedding_similarity = 0.82
        result = _build_evidence_string(cand)
        assert result == "emb(0.82)"

    def test_multiple_sources(self) -> None:
        cand = HarvestCandidate(def_uid="a::f1")
        cand.from_embedding = True
        cand.embedding_similarity = 0.75
        cand.from_term_match = True
        cand.matched_terms = {"config", "model"}
        cand.from_lexical = True
        cand.lexical_hit_count = 3
        result = _build_evidence_string(cand)
        assert "emb(0.75)" in result
        assert "term(" in result
        assert "lex(3)" in result

    def test_explicit(self) -> None:
        cand = HarvestCandidate(def_uid="a::f1")
        cand.from_explicit = True
        result = _build_evidence_string(cand)
        assert result == "explicit"

    def test_no_sources(self) -> None:
        cand = HarvestCandidate(def_uid="a::f1")
        result = _build_evidence_string(cand)
        assert result == ""


# ---------------------------------------------------------------------------
# Negative mention extraction tests
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


# ---------------------------------------------------------------------------
# OR gate tests
# ---------------------------------------------------------------------------


class TestORGate:
    """Tests for has_strong_single_axis and OR gate in filter pipeline."""

    def test_high_embedding_passes(self) -> None:
        c = HarvestCandidate(
            def_uid="test::func",
            from_embedding=True,
            embedding_similarity=0.6,
            file_path="src/handler.py",
        )
        assert c.has_strong_single_axis

    def test_explicit_passes(self) -> None:
        c = HarvestCandidate(
            def_uid="test::func",
            from_explicit=True,
        )
        assert c.has_strong_single_axis

    def test_high_hub_passes(self) -> None:
        c = HarvestCandidate(
            def_uid="test::func",
            hub_score=10,
        )
        assert c.has_strong_single_axis

    def test_many_terms_passes(self) -> None:
        c = HarvestCandidate(
            def_uid="test::func",
            matched_terms={"search", "handler", "query"},
        )
        assert c.has_strong_single_axis

    def test_weak_signal_does_not_pass(self) -> None:
        c = HarvestCandidate(
            def_uid="test::func",
            from_embedding=True,
            embedding_similarity=0.3,
            hub_score=2,
        )
        assert not c.has_strong_single_axis

    def test_or_gate_in_filter(self) -> None:
        """Strong single-axis candidates pass filter without structural evidence."""
        from codeplane.mcp.tools.recon import _apply_filters

        c = HarvestCandidate(
            def_uid="test::func",
            from_embedding=True,
            embedding_similarity=0.6,
            hub_score=0,  # No structural evidence
            file_path="src/handler.py",
            artifact_kind=ArtifactKind.code,
        )
        parsed = ParsedTask(raw="fix handler", intent=TaskIntent.debug)
        result = _apply_filters({"test::func": c}, parsed)
        assert "test::func" in result


# ---------------------------------------------------------------------------
# Negative gating tests
# ---------------------------------------------------------------------------


class TestNegativeGating:
    """Tests for matches_negative and negative gating in filter pipeline."""

    def test_name_match(self) -> None:
        d = MagicMock()
        d.name = "test_handler"
        c = HarvestCandidate(
            def_uid="test::func",
            def_fact=d,
            file_path="tests/test_handler.py",
        )
        assert c.matches_negative(["test_handler"])

    def test_path_match(self) -> None:
        d = MagicMock()
        d.name = "func"
        c = HarvestCandidate(
            def_uid="test::func",
            def_fact=d,
            file_path="src/logging/handler.py",
        )
        assert c.matches_negative(["logging"])

    def test_no_match(self) -> None:
        d = MagicMock()
        d.name = "func"
        c = HarvestCandidate(
            def_uid="test::func",
            def_fact=d,
            file_path="src/handler.py",
        )
        assert not c.matches_negative(["logging"])

    def test_negative_gating_in_filter(self) -> None:
        """Candidates matching negative mentions are excluded."""
        from codeplane.mcp.tools.recon import _apply_filters

        d = MagicMock()
        d.name = "logging_handler"
        c = HarvestCandidate(
            def_uid="test::func",
            def_fact=d,
            from_embedding=True,
            embedding_similarity=0.8,
            hub_score=10,
            file_path="src/handler.py",
        )
        parsed = ParsedTask(
            raw="fix handler not logging",
            intent=TaskIntent.debug,
            negative_mentions=["logging"],
        )
        result = _apply_filters({"test::func": c}, parsed)
        assert "test::func" not in result


# ---------------------------------------------------------------------------
# Failure-mode next actions tests
# ---------------------------------------------------------------------------


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
# Graph evidence boosting tests
# ---------------------------------------------------------------------------


class TestGraphEvidenceBoosting:
    """Tests that graph harvester adds evidence to already-merged candidates."""

    def test_callee_already_merged_gets_graph_flag(self) -> None:
        """When a callee is already in merged, from_graph should become True."""
        # Simulate: a candidate found by embedding, then graph harvester
        # discovers it's a callee of a seed
        c = HarvestCandidate(
            def_uid="mod::BaseModel",
            from_embedding=True,
            embedding_similarity=0.7,
            evidence=[
                EvidenceRecord(category="embedding", detail="sim=0.70", score=0.7),
            ],
        )
        assert c.from_graph is False
        assert c.evidence_axes == 1

        # Simulate what graph harvester now does
        c.from_graph = True
        c.evidence.append(
            EvidenceRecord(category="graph", detail="callee of evaluate", score=0.4)
        )
        assert c.from_graph is True
        assert c.evidence_axes == 2

    def test_graph_evidence_increases_score(self) -> None:
        """Adding graph evidence to a merged candidate should increase its score."""
        d = MagicMock()
        d.name = "BaseModel"
        d.kind = "class"

        c_no_graph = HarvestCandidate(
            def_uid="a",
            def_fact=d,
            from_embedding=True,
            embedding_similarity=0.7,
            file_path="src/base_model.py",
            artifact_kind=_classify_artifact("src/base_model.py"),
        )
        c_with_graph = HarvestCandidate(
            def_uid="b",
            def_fact=d,
            from_embedding=True,
            from_graph=True,
            embedding_similarity=0.7,
            file_path="src/base_model.py",
            artifact_kind=_classify_artifact("src/base_model.py"),
            evidence=[
                EvidenceRecord(category="embedding", detail="sim=0.70", score=0.7),
                EvidenceRecord(category="graph", detail="callee of evaluate", score=0.4),
            ],
        )
        parsed = parse_task("test model evaluation")
        _score_candidates({"a": c_no_graph}, parsed)
        _score_candidates({"b": c_with_graph}, parsed)

        assert c_with_graph.relevance_score > c_no_graph.relevance_score



