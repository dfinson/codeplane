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
    ReconBucket,
    TaskIntent,
    _aggregate_to_files,
    _aggregate_to_files_dual,
    _assign_buckets,
    _build_evidence_string,
    _build_failure_actions,
    _classify_artifact,
    _compute_context_value,
    _compute_edit_likelihood,
    _compute_embedding_floor,
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
    compute_anchor_floor,
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


class TestComputeEmbeddingFloor:
    """Tests for _compute_embedding_floor — adaptive elbow detection."""

    def test_too_few_candidates(self) -> None:
        """With < 4 embedding candidates, returns 0.0 (no filtering)."""
        cands = {
            f"c{i}": HarvestCandidate(
                def_uid=f"c{i}",
                from_embedding=True,
                embedding_similarity=0.5 + i * 0.1,
            )
            for i in range(3)
        }
        assert _compute_embedding_floor(cands) == 0.0

    def test_clear_elbow(self) -> None:
        """Distribution with a clear drop → floor at the elbow."""
        sims = [0.85, 0.80, 0.75, 0.70, 0.65, 0.30, 0.25, 0.20, 0.15, 0.10]
        cands = {
            f"c{i}": HarvestCandidate(
                def_uid=f"c{i}",
                from_embedding=True,
                embedding_similarity=s,
            )
            for i, s in enumerate(sims)
        }
        floor = _compute_embedding_floor(cands)
        # Elbow should be around where the sharp drop happens
        assert 0.25 <= floor <= 0.70

    def test_flat_distribution_no_floor(self) -> None:
        """All similarities similar → returns 0.0 (keep everything)."""
        cands = {
            f"c{i}": HarvestCandidate(
                def_uid=f"c{i}",
                from_embedding=True,
                embedding_similarity=0.50 + i * 0.005,
            )
            for i in range(10)
        }
        floor = _compute_embedding_floor(cands)
        assert floor == 0.0  # < 10% relative spread

    def test_non_embedding_candidates_ignored(self) -> None:
        """Candidates without from_embedding=True are not in the distribution."""
        cands = {
            "e1": HarvestCandidate(def_uid="e1", from_embedding=True, embedding_similarity=0.8),
            "e2": HarvestCandidate(def_uid="e2", from_embedding=True, embedding_similarity=0.7),
            "e3": HarvestCandidate(def_uid="e3", from_embedding=True, embedding_similarity=0.6),
            "e4": HarvestCandidate(def_uid="e4", from_embedding=True, embedding_similarity=0.5),
            "t1": HarvestCandidate(def_uid="t1", from_term_match=True, embedding_similarity=0.0),
            "t2": HarvestCandidate(def_uid="t2", from_graph=True, embedding_similarity=0.0),
        }
        # Only 4 embedding candidates considered — exactly the minimum
        floor = _compute_embedding_floor(cands)
        assert isinstance(floor, float)


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
        c.evidence.append(EvidenceRecord(category="graph", detail="callee of evaluate", score=0.4))
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


# ---------------------------------------------------------------------------
# Dual scoring tests
# ---------------------------------------------------------------------------


class TestEditLikelihood:
    """Tests for _compute_edit_likelihood — def-level edit-likelihood scoring."""

    def _make_candidate(
        self,
        uid: str = "test::func",
        *,
        emb_sim: float = 0.0,
        hub: int = 0,
        terms: int = 0,
        from_embedding: bool = False,
        from_graph: bool = False,
        is_test: bool = False,
        is_callee_of_top: bool = False,
        is_imported_by_top: bool = False,
        shares_file: bool = False,
        name: str = "func",
        file_path: str = "src/core.py",
        kind: str = "function",
    ) -> HarvestCandidate:
        d = MagicMock()
        d.name = name
        d.kind = kind
        d.file_id = 1
        return HarvestCandidate(
            def_uid=uid,
            def_fact=d,
            embedding_similarity=emb_sim,
            hub_score=hub,
            matched_terms={f"t{i}" for i in range(terms)},
            from_embedding=from_embedding,
            from_graph=from_graph,
            is_test=is_test,
            is_callee_of_top=is_callee_of_top,
            is_imported_by_top=is_imported_by_top,
            shares_file_with_seed=shares_file,
            file_path=file_path,
            artifact_kind=_classify_artifact(file_path),
        )

    def test_high_embedding_code_gets_high_edit_score(self) -> None:
        """Code with high embedding similarity should have high edit-likelihood."""
        c = self._make_candidate(
            emb_sim=0.9,
            from_embedding=True,
            name="handle_request",
        )
        parsed = parse_task("fix handle_request error")
        _compute_edit_likelihood({"test::func": c}, parsed)
        assert c.edit_score > 0.3

    def test_name_match_boosts_edit_score(self) -> None:
        """Name matching task terms should boost edit-likelihood."""
        c_match = self._make_candidate(uid="a", name="handle_request", emb_sim=0.5)
        c_no_match = self._make_candidate(uid="b", name="other_func", emb_sim=0.5)
        parsed = parse_task("fix handle_request error")
        _compute_edit_likelihood({"a": c_match, "b": c_no_match}, parsed)
        assert c_match.edit_score > c_no_match.edit_score

    def test_graph_centrality_boosts_edit_score(self) -> None:
        """Graph-connected defs should have higher edit-likelihood."""
        c_graph = self._make_candidate(
            uid="a", emb_sim=0.5, from_graph=True, is_callee_of_top=True
        )
        c_isolated = self._make_candidate(uid="b", emb_sim=0.5)
        parsed = parse_task("fix something")
        _compute_edit_likelihood({"a": c_graph, "b": c_isolated}, parsed)
        assert c_graph.edit_score > c_isolated.edit_score

    def test_test_files_downranked_for_edit(self) -> None:
        """Test files should have low edit-likelihood (unless test-driven)."""
        c_code = self._make_candidate(
            uid="a", emb_sim=0.6, file_path="src/handler.py"
        )
        c_test = self._make_candidate(
            uid="b", emb_sim=0.6, file_path="tests/test_handler.py", is_test=True
        )
        parsed = parse_task("implement caching")
        _compute_edit_likelihood({"a": c_code, "b": c_test}, parsed)
        assert c_code.edit_score > c_test.edit_score

    def test_test_files_not_downranked_for_test_task(self) -> None:
        """Test files should NOT be downranked when task is test-driven."""
        c_test = self._make_candidate(
            uid="a", emb_sim=0.6, file_path="tests/test_handler.py",
            is_test=True, name="test_handler",
        )
        parsed = ParsedTask(
            raw="write tests for handler",
            intent=TaskIntent.test,
            primary_terms=["handler"],
            keywords=["handler"],
            is_test_driven=True,
        )
        _compute_edit_likelihood({"a": c_test}, parsed)
        # Should not be heavily penalized
        assert c_test.edit_score > 0.1

    def test_variable_kind_gets_lower_score(self) -> None:
        """Variable defs should have lower edit-likelihood than functions."""
        c_func = self._make_candidate(uid="a", emb_sim=0.5, kind="function")
        c_var = self._make_candidate(uid="b", emb_sim=0.5, kind="variable")
        parsed = parse_task("fix something")
        _compute_edit_likelihood({"a": c_func, "b": c_var}, parsed)
        assert c_func.edit_score > c_var.edit_score

    def test_doc_files_heavily_downranked(self) -> None:
        """Doc files should have very low edit-likelihood."""
        c_code = self._make_candidate(uid="a", emb_sim=0.5, file_path="src/core.py")
        c_doc = self._make_candidate(uid="b", emb_sim=0.5, file_path="README.md")
        parsed = parse_task("fix the API")
        _compute_edit_likelihood({"a": c_code, "b": c_doc}, parsed)
        assert c_code.edit_score > c_doc.edit_score * 3


class TestContextValue:
    """Tests for _compute_context_value — context-value scoring."""

    def _make_candidate(
        self,
        uid: str = "test::func",
        *,
        emb_sim: float = 0.0,
        hub: int = 0,
        terms: int = 0,
        from_graph: bool = False,
        is_test: bool = False,
        is_callee_of_top: bool = False,
        is_imported_by_top: bool = False,
        shares_file: bool = False,
        name: str = "func",
        file_path: str = "src/core.py",
    ) -> HarvestCandidate:
        d = MagicMock()
        d.name = name
        d.kind = "function"
        d.file_id = 1
        return HarvestCandidate(
            def_uid=uid,
            def_fact=d,
            embedding_similarity=emb_sim,
            hub_score=hub,
            matched_terms={f"t{i}" for i in range(terms)},
            from_graph=from_graph,
            is_test=is_test,
            is_callee_of_top=is_callee_of_top,
            is_imported_by_top=is_imported_by_top,
            shares_file_with_seed=shares_file,
            file_path=file_path,
            artifact_kind=_classify_artifact(file_path),
        )

    def test_graph_connected_test_has_high_context(self) -> None:
        """Tests that are graph-connected should have high context-value."""
        c_test = self._make_candidate(
            uid="a", emb_sim=0.5, is_test=True,
            from_graph=True, file_path="tests/test_core.py",
        )
        parsed = parse_task("fix core module")
        _compute_context_value({"a": c_test}, parsed)
        assert c_test.context_score > 0.3

    def test_disconnected_test_has_lower_context(self) -> None:
        """Tests not graph-connected should have lower context-value."""
        c_connected = self._make_candidate(
            uid="a", emb_sim=0.5, is_test=True, from_graph=True,
            file_path="tests/test_core.py",
        )
        c_disconnected = self._make_candidate(
            uid="b", emb_sim=0.5, is_test=True,
            file_path="tests/test_other.py",
        )
        parsed = parse_task("fix core module")
        _compute_context_value({"a": c_connected, "b": c_disconnected}, parsed)
        assert c_connected.context_score > c_disconnected.context_score

    def test_doc_with_term_overlap_has_context(self) -> None:
        """Docs matching task terms should have decent context-value."""
        c_doc = self._make_candidate(
            uid="a", emb_sim=0.6, terms=3, file_path="docs/api.md",
        )
        parsed = parse_task("fix the API")
        _compute_context_value({"a": c_doc}, parsed)
        assert c_doc.context_score > 0.15

    def test_structurally_coupled_code_has_context(self) -> None:
        """Code that is graph-connected should have context-value."""
        c_coupled = self._make_candidate(
            uid="a", emb_sim=0.4, from_graph=True,
            is_imported_by_top=True,
        )
        c_isolated = self._make_candidate(uid="b", emb_sim=0.4)
        parsed = parse_task("fix something")
        _compute_context_value({"a": c_coupled, "b": c_isolated}, parsed)
        assert c_coupled.context_score > c_isolated.context_score

    def test_edit_and_context_scores_are_bounded(self) -> None:
        """Both scores should be in [0, 1]."""
        c = self._make_candidate(emb_sim=1.0, terms=10, from_graph=True)
        parsed = parse_task("test task")
        _compute_edit_likelihood({"test::func": c}, parsed)
        _compute_context_value({"test::func": c}, parsed)
        assert 0.0 <= c.edit_score <= 1.0
        assert 0.0 <= c.context_score <= 1.0


# ---------------------------------------------------------------------------
# Dual file aggregation tests
# ---------------------------------------------------------------------------


class TestAggregateToFilesDual:
    """Tests for _aggregate_to_files_dual — dual-score file aggregation."""

    def _make_candidate(
        self,
        uid: str,
        file_id: int,
        *,
        name: str = "func",
        edit_score: float = 0.0,
        context_score: float = 0.0,
    ) -> HarvestCandidate:
        d = MagicMock()
        d.name = name
        d.file_id = file_id
        d.kind = "function"
        return HarvestCandidate(
            def_uid=uid,
            def_fact=d,
            edit_score=edit_score,
            context_score=context_score,
        )

    def test_empty_input(self) -> None:
        assert _aggregate_to_files_dual([], {}) == []

    def test_single_file_returns_dual_scores(self) -> None:
        c = self._make_candidate("a::f1", file_id=1, edit_score=0.8, context_score=0.3)
        scored = [("a::f1", 0.5)]
        result = _aggregate_to_files_dual(scored, {"a::f1": c})
        assert len(result) == 1
        fid, fscore, fedit, fctx, defs = result[0]
        assert fid == 1
        assert fscore == 0.5
        assert fedit == 0.8
        assert fctx == 0.3

    def test_multi_def_file_averages_top2(self) -> None:
        """File with multiple defs should average top-2 edit/context scores."""
        c1 = self._make_candidate("a::f1", file_id=1, edit_score=0.9, context_score=0.2)
        c2 = self._make_candidate("a::f2", file_id=1, edit_score=0.6, context_score=0.8)
        c3 = self._make_candidate("a::f3", file_id=1, edit_score=0.3, context_score=0.1)
        cands = {"a::f1": c1, "a::f2": c2, "a::f3": c3}
        scored = [("a::f1", 0.5), ("a::f2", 0.4), ("a::f3", 0.3)]
        result = _aggregate_to_files_dual(scored, cands)
        assert len(result) == 1
        _fid, _fs, fedit, fctx, _defs = result[0]
        # Top-2 edit: (0.9 + 0.6) / 2 = 0.75
        assert abs(fedit - 0.75) < 0.01
        # Top-2 context: (0.8 + 0.2) / 2 = 0.50
        assert abs(fctx - 0.50) < 0.01


# ---------------------------------------------------------------------------
# Bucketing tests
# ---------------------------------------------------------------------------


class TestBucketing:
    """Tests for _assign_buckets — score-based bucket assignment."""

    def _make_file_entry(
        self,
        fid: int,
        *,
        file_score: float = 0.5,
        edit_score: float = 0.0,
        context_score: float = 0.0,
    ) -> tuple[int, float, float, float, list[tuple[str, float]]]:
        return (fid, file_score, edit_score, context_score, [(f"def::{fid}", file_score)])

    def test_empty_input(self) -> None:
        assert _assign_buckets([], {}) == {}

    def test_top_edit_files_become_edit_targets(self) -> None:
        """Files with edit_score >= 0.10 and edit > ctx should be edit_target."""
        files = [
            self._make_file_entry(1, edit_score=0.9),
            self._make_file_entry(2, edit_score=0.7),
            self._make_file_entry(3, edit_score=0.15, context_score=0.04),
        ]
        buckets = _assign_buckets(files, {})
        assert buckets[1] == ReconBucket.edit_target
        assert buckets[2] == ReconBucket.edit_target
        assert buckets[3] == ReconBucket.edit_target  # edit > ctx and >= 0.10

    def test_high_context_files_become_context(self) -> None:
        """Files with context_score >= 0.05 and ctx >= edit should be context."""
        files = [
            self._make_file_entry(1, edit_score=0.9, context_score=0.3),
            self._make_file_entry(2, edit_score=0.04, context_score=0.8),
            self._make_file_entry(3, edit_score=0.03, context_score=0.7),
        ]
        buckets = _assign_buckets(files, {})
        assert buckets[1] == ReconBucket.edit_target
        assert buckets[2] == ReconBucket.context
        assert buckets[3] == ReconBucket.context

    def test_remainder_is_supplementary(self) -> None:
        """Files weak on both axes should be supplementary."""
        files = [
            self._make_file_entry(1, edit_score=0.9),
            self._make_file_entry(2, edit_score=0.03, context_score=0.04),
            self._make_file_entry(3, edit_score=0.01, context_score=0.01),
        ]
        buckets = _assign_buckets(files, {})
        assert buckets[1] == ReconBucket.edit_target
        assert buckets[2] == ReconBucket.supplementary
        assert buckets[3] == ReconBucket.supplementary

    def test_no_hard_cap_on_edit_targets(self) -> None:
        """All qualifying files should become edit_target — no artificial limit."""
        files = [
            self._make_file_entry(i, edit_score=0.9 - i * 0.01)
            for i in range(10)
        ]
        buckets = _assign_buckets(files, {})
        edit_count = sum(1 for b in buckets.values() if b == ReconBucket.edit_target)
        # All 10 have edit_score >= 0.10 and ctx=0, so all should be edit_target
        assert edit_count == 10

    def test_no_hard_cap_on_context(self) -> None:
        """All qualifying context files should remain context — no artificial limit."""
        files = [
            self._make_file_entry(i, edit_score=0.01, context_score=0.9 - i * 0.05)
            for i in range(10)
        ]
        buckets = _assign_buckets(files, {})
        ctx_count = sum(1 for b in buckets.values() if b == ReconBucket.context)
        # All 10 have ctx_score >= 0.05 and ctx > edit, so all qualify for context.
        # But safety net promotes the top by edit_score to edit_target → 9 ctx.
        assert ctx_count == 9
        edit_count = sum(1 for b in buckets.values() if b == ReconBucket.edit_target)
        assert edit_count == 1  # safety net

    def test_safety_net_promotes_top_edit(self) -> None:
        """If no file qualifies for edit_target, the top by edit_score is promoted."""
        files = [
            self._make_file_entry(1, edit_score=0.08, context_score=0.2),
            self._make_file_entry(2, edit_score=0.05, context_score=0.3),
        ]
        buckets = _assign_buckets(files, {})
        # Neither qualifies (edit < 0.10), but fid=1 should be promoted
        assert buckets[1] == ReconBucket.edit_target
        assert buckets[2] == ReconBucket.context

    def test_propagates_to_candidates(self) -> None:
        """Bucket assignment should propagate to candidate objects."""
        d = MagicMock()
        d.name = "func"
        d.kind = "function"
        d.file_id = 1
        cand = HarvestCandidate(def_uid="a", def_fact=d)
        files = [self._make_file_entry(1, edit_score=0.9)]
        _assign_buckets(files, {"a": cand})
        assert cand.bucket == ReconBucket.edit_target


# ---------------------------------------------------------------------------
# ReconBucket enum tests
# ---------------------------------------------------------------------------


class TestReconBucket:
    """Tests for ReconBucket enum."""

    def test_values(self) -> None:
        assert ReconBucket.edit_target.value == "edit_target"
        assert ReconBucket.context.value == "context"
        assert ReconBucket.supplementary.value == "supplementary"

    def test_default_bucket_is_supplementary(self) -> None:
        """HarvestCandidate default bucket should be supplementary."""
        c = HarvestCandidate(def_uid="test")
        assert c.bucket == ReconBucket.supplementary


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
        parsed = ParsedTask(
            raw="",
            primary_terms=["config", "mlflow"],
            secondary_terms=[],
        )
        ctx = self._make_app_ctx([
            "src/app.py",
            "config/mlflow.yaml",
            "README.md",
        ])
        indexed = {"src/app.py"}
        result = _find_unindexed_files(ctx, parsed, indexed)
        paths = [p for p, _ in result]
        assert "config/mlflow.yaml" in paths

    def test_excludes_indexed_files(self) -> None:
        """Files already in the structural index are excluded."""
        parsed = ParsedTask(
            raw="",
            primary_terms=["config"],
            secondary_terms=[],
        )
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
            raw="",
            primary_terms=["config", "mlflow", "tracking"],
            secondary_terms=[],
        )
        ctx = self._make_app_ctx([
            "config.yaml",                     # matches "config"
            "config/mlflow/tracking.yaml",     # matches all 3
            "README.md",
        ])
        result = _find_unindexed_files(ctx, parsed, set())
        if len(result) >= 2:
            assert result[0][1] >= result[1][1]

    def test_caps_at_limit(self) -> None:
        """Results capped at _UNINDEXED_MAX_FILES."""
        parsed = ParsedTask(
            raw="",
            primary_terms=["test"],
            secondary_terms=[],
        )
        files = [f"test/file{i}.yaml" for i in range(30)]
        ctx = self._make_app_ctx(files)
        result = _find_unindexed_files(ctx, parsed, set())
        assert len(result) <= 15

    def test_substring_match(self) -> None:
        """Terms match as substrings in path."""
        parsed = ParsedTask(
            raw="",
            primary_terms=["integration"],
            secondary_terms=[],
        )
        ctx = self._make_app_ctx([
            ".github/workflows/integration-tests.yml",
            "README.md",
        ])
        result = _find_unindexed_files(ctx, parsed, set())
        paths = [p for p, _ in result]
        assert ".github/workflows/integration-tests.yml" in paths
