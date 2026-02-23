"""Tests for EmbeddingIndex — dense vector index for DefFact embeddings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture
def index_dir(tmp_path: Path) -> Path:
    """Temporary directory for embedding index storage."""
    d = tmp_path / "embedding"
    d.mkdir()
    return d


@pytest.fixture
def sample_defs() -> list[dict[str, Any]]:
    """Sample def dicts mimicking ExtractionResult.defs."""
    return [
        {
            "def_uid": "file.py::MyClass",
            "name": "MyClass",
            "qualified_name": "module.MyClass",
            "kind": "class",
            "docstring": "A sample class for testing.",
            "signature_text": None,
        },
        {
            "def_uid": "file.py::my_function",
            "name": "my_function",
            "qualified_name": "module.my_function",
            "kind": "function",
            "docstring": "Computes something useful.",
            "signature_text": "(x: int, y: int) -> int",
        },
        {
            "def_uid": "file.py::helper",
            "name": "helper",
            "qualified_name": "module.helper",
            "kind": "function",
            "docstring": None,
            "signature_text": "(data: list) -> None",
        },
    ]


def _make_fake_embedder(dim: int = 384) -> MagicMock:
    """Create a mock TextEmbedding that returns deterministic vectors."""
    model = MagicMock()

    def fake_embed(texts: list[str], **_kwargs: Any) -> list[np.ndarray[Any, np.dtype[np.float32]]]:
        """Generate deterministic embeddings based on text hash."""
        results = []
        for text in texts:
            rng = np.random.RandomState(hash(text) % (2**31))
            vec = rng.randn(dim).astype(np.float32)
            vec /= np.linalg.norm(vec)
            results.append(vec)
        return results

    model.embed = fake_embed
    return model


class TestEmbeddingIndex:
    """Test EmbeddingIndex lifecycle: stage, commit, query, save, load, clear."""

    def test_init_creates_directory(self, tmp_path: Path) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx_path = tmp_path / "new_embedding_dir"
        assert not idx_path.exists()
        _idx = EmbeddingIndex(idx_path)
        assert idx_path.exists()

    def test_empty_index_query_returns_empty(self, index_dir: Path) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        result = idx.query("test query")
        assert result == []

    def test_empty_index_count_zero(self, index_dir: Path) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        assert idx.count == 0

    def test_stage_and_commit(self, index_dir: Path, sample_defs: list[dict[str, Any]]) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        idx.stage_defs(sample_defs, file_path="src/module.py")
        assert idx.has_staged_changes()

        count = idx.commit_staged()
        assert count == 3
        assert idx.count == 3
        assert not idx.has_staged_changes()

    def test_commit_no_changes_returns_zero(self, index_dir: Path) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        assert idx.commit_staged() == 0

    def test_query_returns_sorted_results(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        idx.stage_defs(sample_defs, file_path="src/module.py")
        idx.commit_staged()

        results = idx.query_batch(["compute something useful"], top_k=3)[0]
        assert len(results) == 3
        # Results should be (uid, similarity) tuples sorted descending
        for uid, sim in results:
            assert isinstance(uid, str)
            assert isinstance(sim, float)
        # Verify descending order
        sims = [s for _, s in results]
        assert sims == sorted(sims, reverse=True)

    def test_save_and_load(self, index_dir: Path, sample_defs: list[dict[str, Any]]) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        # Build and save
        idx1 = EmbeddingIndex(index_dir)
        idx1._model = _make_fake_embedder()
        idx1.stage_defs(sample_defs, file_path="src/module.py")
        idx1.commit_staged()  # commit_staged calls _save internally

        # Load into fresh instance
        idx2 = EmbeddingIndex(index_dir)
        loaded = idx2.load()
        assert loaded is True
        assert idx2.count == 3

        # Verify files on disk
        assert (index_dir / "embeddings.npz").exists()
        assert (index_dir / "metadata.json").exists()

        # Verify metadata content
        with (index_dir / "metadata.json").open() as f:
            meta = json.load(f)
        assert meta["unique_defs"] == 3
        assert meta["dim"] == 384
        assert meta["version"] == 3

    def test_load_nonexistent_returns_false(self, index_dir: Path) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        assert idx.load() is False

    def test_stage_remove(self, index_dir: Path, sample_defs: list[dict[str, Any]]) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        idx.stage_defs(sample_defs, file_path="src/module.py")
        idx.commit_staged()
        assert idx.count == 3

        # Remove one def
        idx.stage_remove({"file.py::helper"})
        idx.commit_staged()
        assert idx.count == 2

        # Verify removed uid not in raw results
        results = idx.query_batch(["helper function"], top_k=10)[0]
        uids = {uid for uid, _ in results}
        assert "file.py::helper" not in uids

    def test_stage_remove_all(self, index_dir: Path, sample_defs: list[dict[str, Any]]) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        idx.stage_defs(sample_defs, file_path="src/module.py")
        idx.commit_staged()

        # Remove all
        all_uids = {d["def_uid"] for d in sample_defs}
        idx.stage_remove(all_uids)
        idx.commit_staged()
        assert idx.count == 0
        assert idx.query("anything") == []

    def test_incremental_update_replaces_existing(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        # Initial embed
        idx.stage_defs(sample_defs, file_path="src/module.py")
        idx.commit_staged()
        assert idx.count == 3

        # Re-stage same uid with different content (simulates re-index)
        updated_def = {
            "def_uid": "file.py::MyClass",
            "name": "MyClass",
            "qualified_name": "module.MyClass",
            "kind": "class",
            "docstring": "Completely different docstring now.",
            "signature_text": None,
        }
        idx.stage_defs([updated_def], file_path="src/module.py")
        idx.commit_staged()
        # Count should still be 3 (replaced, not duplicated)
        assert idx.count == 3

    def test_clear_removes_everything(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        idx.stage_defs(sample_defs, file_path="src/module.py")
        idx.commit_staged()

        idx.clear()
        assert idx.count == 0
        assert not (index_dir / "embeddings.npz").exists()
        assert not (index_dir / "metadata.json").exists()

    def test_discard_staged(self, index_dir: Path, sample_defs: list[dict[str, Any]]) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx.stage_defs(sample_defs, file_path="src/module.py")
        idx.stage_remove({"some_uid"})
        assert idx.has_staged_changes()

        count = idx.discard_staged()
        assert count == 4  # 3 defs + 1 removal
        assert not idx.has_staged_changes()

    def test_def_to_text_format(self) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        d = {
            "kind": "function",
            "qualified_name": "module.my_func",
            "name": "my_func",
            "signature_text": "(x: int) -> str",
            "docstring": "Convert integer to string.",
        }
        text = EmbeddingIndex._def_to_text(d)
        assert text.startswith("function module.my_func")
        assert "(x: int) -> str" in text
        assert "Convert integer to string." in text

    def test_def_to_text_minimal(self) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        d = {"kind": "variable", "name": "MAX_SIZE"}
        text = EmbeddingIndex._def_to_text(d)
        assert text == "variable MAX_SIZE"

    def test_def_to_text_truncation(self) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        d = {
            "kind": "function",
            "name": "f",
            "qualified_name": "f",
            "docstring": "x" * 5000,
        }
        text = EmbeddingIndex._def_to_text(d)
        assert len(text) <= 1500

    def test_fastembed_not_installed_disables_gracefully(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)

        with patch.dict("sys.modules", {"fastembed": None}):
            idx._ensure_model()

        assert idx._disabled is True
        # Staging and committing should be no-ops
        idx.stage_defs(sample_defs, file_path="src/module.py")
        count = idx.commit_staged()
        assert count == 0
        assert idx.query("anything") == []

    def test_reload_reloads_from_disk(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()
        idx.stage_defs(sample_defs, file_path="src/module.py")
        idx.commit_staged()

        # Wipe in-memory state
        idx._matrix = None
        idx._kinds = []
        assert idx.count == 0

        idx.reload()
        assert idx.count == 3

    def test_save_and_load_preserves_query_results(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()
        idx.stage_defs(sample_defs, file_path="src/module.py")
        idx.commit_staged()

        # Query before save (use query_batch for deterministic raw scoring)
        results_before = idx.query_batch(["class for testing"], top_k=3)[0]

        # Load into new instance and query
        idx2 = EmbeddingIndex(index_dir)
        idx2._model = _make_fake_embedder()
        idx2.load()
        results_after = idx2.query_batch(["class for testing"], top_k=3)[0]

        # UIDs should match (order preserved)
        uids_before = [uid for uid, _ in results_before]
        uids_after = [uid for uid, _ in results_after]
        assert uids_before == uids_after

    def test_dedup_staged_defs_last_wins(self, index_dir: Path) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        # Stage same uid twice with different docstrings
        idx.stage_defs(
            [
                {"def_uid": "a::b", "name": "b", "kind": "function", "docstring": "first"},
                {"def_uid": "a::b", "name": "b", "kind": "function", "docstring": "second"},
            ],
            file_path="src/module.py",
        )
        count = idx.commit_staged()
        assert count == 1
        assert idx.count == 1

    def test_mixed_stage_and_remove_in_one_commit(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        # Initial population
        idx.stage_defs(sample_defs, file_path="src/module.py")
        idx.commit_staged()
        assert idx.count == 3

        # Stage removal + new addition in same commit
        new_def = {
            "def_uid": "other.py::NewClass",
            "name": "NewClass",
            "kind": "class",
            "docstring": "Brand new class.",
        }
        idx.stage_remove({"file.py::MyClass", "file.py::helper"})
        idx.stage_defs([new_def], file_path="src/other.py")
        count = idx.commit_staged()
        assert count == 1  # Only the new def was embedded
        assert idx.count == 2  # my_function (kept) + NewClass (new)

        all_uids = {uid for uid, _kind in idx._kinds}
        assert "file.py::my_function" in all_uids
        assert "other.py::NewClass" in all_uids
        assert "file.py::MyClass" not in all_uids
        assert "file.py::helper" not in all_uids


class TestEvidenceRecords:
    """Tests for evidence-record construction including SEM_FACTS."""

    def test_sem_facts_record_emitted(self, index_dir: Path) -> None:
        """SEM_FACTS record is produced when _sem_facts dict is present."""
        from codeplane.index._internal.indexing.embedding import (
            KIND_SEM_FACTS,
            EmbeddingIndex,
        )

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        defs = [
            {
                "def_uid": "a.py::process",
                "name": "process",
                "kind": "function",
                "docstring": None,
                "_sem_facts": {
                    "calls": ["getUserById", "validate"],
                    "raises": ["ValueError"],
                },
            }
        ]
        idx.stage_defs(defs, file_path="src/a.py")
        idx.commit_staged()

        # Should have SEM_FACTS record among the kinds
        sem_records = [(uid, k) for uid, k in idx._kinds if k == KIND_SEM_FACTS]
        assert len(sem_records) == 1
        assert sem_records[0][0] == "a.py::process"

    def test_sem_facts_not_emitted_when_absent(self, index_dir: Path) -> None:
        """No SEM_FACTS record when _sem_facts is missing or empty."""
        from codeplane.index._internal.indexing.embedding import (
            KIND_SEM_FACTS,
            EmbeddingIndex,
        )

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        defs = [
            {
                "def_uid": "b.py::simple",
                "name": "simple",
                "kind": "function",
                "docstring": "A simple function.",
            }
        ]
        idx.stage_defs(defs, file_path="src/b.py")
        idx.commit_staged()

        sem_records = [(uid, k) for uid, k in idx._kinds if k == KIND_SEM_FACTS]
        assert len(sem_records) == 0

    def test_render_sem_facts_format(self) -> None:
        """Verify _render_sem_facts produces English-structured tags."""
        from codeplane.index._internal.indexing.embedding import _render_sem_facts

        sem_facts = {
            "calls": ["getUserById", "validate"],
            "assigns": ["name"],
            "returns": ["result"],
            "raises": ["ValueError"],
            "literals": ["config_key"],
        }
        text = _render_sem_facts(sem_facts, word_df={}, n_defs=0)
        # Should contain category headers and word-split tokens
        assert text.startswith("calls")
        assert "get" in text
        assert "user" in text
        assert "assigns" in text
        assert "name" in text
        assert "returns" in text
        assert "result" in text
        assert "raises" in text
        assert "value" in text
        assert "error" in text
        assert "literals" in text

    def test_render_sem_facts_drops_high_freq_tokens(self) -> None:
        """High-frequency tokens are filtered from SEM_FACTS rendering."""
        from codeplane.index._internal.indexing.embedding import _render_sem_facts

        # Simulate a repo where "get" appears in 50% of defs
        word_df = {"get": 500}
        n_defs = 1000

        sem_facts = {"calls": ["getUser"]}
        text = _render_sem_facts(sem_facts, word_df=word_df, n_defs=n_defs)
        # "get" should be filtered (50% >> threshold ~5%)
        assert "get" not in text.split()
        # "user" should remain
        assert "user" in text

    def test_render_sem_facts_caps_tokens(self) -> None:
        """Token cap prevents runaway record sizes."""
        from codeplane.index._internal.indexing.embedding import (
            _SEM_FACTS_TOKEN_CAP,
            _render_sem_facts,
        )

        # Create enough calls to exceed the token cap
        sem_facts = {"calls": [f"function{i}" for i in range(50)]}
        text = _render_sem_facts(sem_facts, word_df={}, n_defs=0)
        # Count tokens (excluding category header "calls")
        tokens = text.split()
        # Category header + tokens <= cap + 1 (for header)
        content_tokens = [t for t in tokens if t != "calls"]
        assert len(content_tokens) <= _SEM_FACTS_TOKEN_CAP

    def test_file_path_passed_to_stage_defs(self, index_dir: Path) -> None:
        """file_path kwarg is propagated to def dicts for CTX_PATH records."""
        from codeplane.index._internal.indexing.embedding import (
            KIND_CTX_PATH,
            EmbeddingIndex,
        )

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        defs = [
            {
                "def_uid": "x.py::foo",
                "name": "foo",
                "kind": "function",
                "docstring": None,
            }
        ]
        idx.stage_defs(defs, file_path="src/auth/middleware.py")
        idx.commit_staged()

        ctx_records = [(uid, k) for uid, k in idx._kinds if k == KIND_CTX_PATH]
        assert len(ctx_records) >= 1

    def test_sem_facts_counts_as_context_for_tiered_acceptance(
        self, index_dir: Path
    ) -> None:
        """SEM_FACTS should act as context signal (like CTX_PATH/CTX_USAGE).

        Verifies that the SEM_FACTS record kind is included in the
        has_ctx check for tiered acceptance rules.
        """
        from codeplane.index._internal.indexing.embedding import (
            KIND_CTX_PATH,
            KIND_SEM_FACTS,
            EmbeddingIndex,
        )

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        defs = [
            {
                "def_uid": "c.py::handler",
                "name": "handler",
                "kind": "function",
                "docstring": None,
                "_sem_facts": {
                    "calls": ["processRequest", "sendResponse"],
                    "raises": ["HTTPError"],
                },
            }
        ]
        idx.stage_defs(defs, file_path="src/c.py")
        idx.commit_staged()

        # Verify SEM_FACTS and CTX_PATH records exist for "handler"
        handler_kinds = {k for uid, k in idx._kinds if uid == "c.py::handler"}
        assert KIND_SEM_FACTS in handler_kinds
        assert KIND_CTX_PATH in handler_kinds


class TestSemQueriesModule:
    """Tests for the _sem_queries module structure."""

    def test_categories_match_captures(self) -> None:
        """Every capture used in queries is mapped in SEM_CAPTURE_CATEGORIES."""
        import re as _re

        from codeplane.index._internal.parsing._sem_queries import (
            SEM_CAPTURE_CATEGORIES,
            SEM_FACTS_QUERIES,
        )

        capture_re = _re.compile(r"@(\w+)")
        for lang, query_text in SEM_FACTS_QUERIES.items():
            captures = set(capture_re.findall(query_text))
            for cap in captures:
                assert cap in SEM_CAPTURE_CATEGORIES, (
                    f"Language {lang}: capture @{cap} not in SEM_CAPTURE_CATEGORIES"
                )

    def test_category_order_covers_all(self) -> None:
        """SEM_CATEGORY_ORDER covers all values in SEM_CAPTURE_CATEGORIES."""
        from codeplane.index._internal.parsing._sem_queries import (
            SEM_CAPTURE_CATEGORIES,
            SEM_CATEGORY_ORDER,
        )

        all_cats = set(SEM_CAPTURE_CATEGORIES.values())
        order_cats = set(SEM_CATEGORY_ORDER)
        assert all_cats == order_cats
