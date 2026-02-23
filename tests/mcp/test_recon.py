"""Tests for the recon MCP tool.

Tests:
- _tokenize_task: task description tokenization
- _select_seeds: term-intersection + hub-score reranking
- _expand_seed: graph expansion
- _trim_to_budget: budget assembly
- _summarize_recon: summary generation
- register_tools: tool wiring
- ArtifactKind: artifact classification
- TaskIntent: intent extraction
- EvidenceRecord: structured evidence
- _apply_filters: intent-aware filter pipeline
- _score_candidates: bounded scoring model
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codeplane.mcp.tools.recon import (
    ArtifactKind,
    EvidenceRecord,
    HarvestCandidate,
    TaskIntent,
    _classify_artifact,
    _def_signature_text,
    _estimate_bytes,
    _extract_intent,
    _extract_paths,
    _read_lines,
    _score_candidates,
    _summarize_recon,
    _tokenize_task,
    _trim_to_budget,
    find_elbow,
    parse_task,
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
            emb_sim=1.0, hub=100, terms=10,
            from_embedding=True, from_explicit=True,
        )
        parsed = parse_task("test task")
        scored = _score_candidates({"test::func": c}, parsed)
        assert len(scored) == 1
        _, score = scored[0]
        # Bounded scoring means score shouldn't explode
        assert 0 <= score <= 2.0

    def test_explicit_beats_embedding_only(self) -> None:
        """Explicit mentions should score higher than embedding-only."""
        c_explicit = self._make_candidate(
            uid="a", from_explicit=True, emb_sim=0.5,
        )
        c_embed = self._make_candidate(
            uid="b", from_embedding=True, emb_sim=0.5,
        )
        parsed = parse_task("test task")
        scored = _score_candidates(
            {"a": c_explicit, "b": c_embed}, parsed
        )
        scores = dict(scored)
        assert scores["a"] > scores["b"]

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
            uid="a", from_embedding=True, emb_sim=0.6,
            file_path="src/handler.py",
        )
        c_test = self._make_candidate(
            uid="b", from_embedding=True, emb_sim=0.6,
            file_path="tests/test_handler.py", is_test=True,
        )
        parsed = parse_task("implement caching in handler")
        scored = _score_candidates({"a": c_code, "b": c_test}, parsed)
        scores = dict(scored)
        assert scores["a"] > scores["b"]

    def test_separated_relevance_and_seed_scores(self) -> None:
        """relevance_score and seed_score should be set on candidates."""
        c = self._make_candidate(
            from_embedding=True, emb_sim=0.7, hub=5,
        )
        parsed = parse_task("test task")
        _score_candidates({"test::func": c}, parsed)
        assert c.relevance_score > 0
        assert c.seed_score > 0
        # seed_score incorporates hub-based multiplier
        assert c.seed_score != c.relevance_score


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
        result = _apply_filters({"test::func": c}, TaskIntent.unknown)
        assert "test::func" in result


# ---------------------------------------------------------------------------
# Elbow detection tests (existing + new)
# ---------------------------------------------------------------------------


class TestFindElbow:
    """Tests for find_elbow."""

    def test_small_list(self) -> None:
        assert find_elbow([10, 5, 1]) == 3

    def test_clear_elbow(self) -> None:
        scores = [100, 95, 90, 85, 80, 10, 5, 3, 2, 1]
        k = find_elbow(scores)
        assert 3 <= k <= 6

    def test_flat_distribution(self) -> None:
        scores = [10, 10, 10, 10, 10]
        k = find_elbow(scores)
        assert k == len(scores)

    def test_empty(self) -> None:
        assert find_elbow([]) == 0

    def test_single(self) -> None:
        assert find_elbow([5]) == 1

    def test_respects_min_seeds(self) -> None:
        scores = [100, 1, 1, 1, 1]
        k = find_elbow(scores, min_seeds=3)
        assert k >= 3

    def test_respects_max_seeds(self) -> None:
        scores = list(range(100, 0, -1))
        k = find_elbow(scores, max_seeds=10)
        assert k <= 10
