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


def _make_fake_embedder(dim: int = 768) -> MagicMock:
    """Create a mock TextEmbedding that returns deterministic vectors."""
    model = MagicMock()

    def fake_embed(texts: list[str]) -> list[np.ndarray[Any, np.dtype[np.float32]]]:
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

        idx.stage_defs(sample_defs)
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

        idx.stage_defs(sample_defs)
        idx.commit_staged()

        results = idx.query("compute something useful", top_k=3)
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
        idx1.stage_defs(sample_defs)
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
        assert meta["count"] == 3
        assert meta["dim"] == 768
        assert meta["version"] == 1

    def test_load_nonexistent_returns_false(self, index_dir: Path) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        assert idx.load() is False

    def test_stage_remove(self, index_dir: Path, sample_defs: list[dict[str, Any]]) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        idx.stage_defs(sample_defs)
        idx.commit_staged()
        assert idx.count == 3

        # Remove one def
        idx.stage_remove({"file.py::helper"})
        idx.commit_staged()
        assert idx.count == 2

        # Verify removed uid not in results
        results = idx.query("helper function", top_k=10)
        uids = {uid for uid, _ in results}
        assert "file.py::helper" not in uids

    def test_stage_remove_all(self, index_dir: Path, sample_defs: list[dict[str, Any]]) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        idx.stage_defs(sample_defs)
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
        idx.stage_defs(sample_defs)
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
        idx.stage_defs([updated_def])
        idx.commit_staged()
        # Count should still be 3 (replaced, not duplicated)
        assert idx.count == 3

    def test_clear_removes_everything(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()

        idx.stage_defs(sample_defs)
        idx.commit_staged()

        idx.clear()
        assert idx.count == 0
        assert not (index_dir / "embeddings.npz").exists()
        assert not (index_dir / "metadata.json").exists()

    def test_discard_staged(self, index_dir: Path, sample_defs: list[dict[str, Any]]) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx.stage_defs(sample_defs)
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
        assert len(text) <= 2000

    def test_fastembed_not_installed_disables_gracefully(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)

        with patch.dict("sys.modules", {"fastembed": None}):
            idx._ensure_model()

        assert idx._disabled is True
        # Staging and committing should be no-ops
        idx.stage_defs(sample_defs)
        count = idx.commit_staged()
        assert count == 0
        assert idx.query("anything") == []

    def test_reload_reloads_from_disk(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()
        idx.stage_defs(sample_defs)
        idx.commit_staged()

        # Wipe in-memory state
        idx._matrix = None
        idx._uids = []
        assert idx.count == 0

        idx.reload()
        assert idx.count == 3

    def test_save_and_load_preserves_query_results(
        self, index_dir: Path, sample_defs: list[dict[str, Any]]
    ) -> None:
        from codeplane.index._internal.indexing.embedding import EmbeddingIndex

        idx = EmbeddingIndex(index_dir)
        idx._model = _make_fake_embedder()
        idx.stage_defs(sample_defs)
        idx.commit_staged()

        # Query before save
        results_before = idx.query("class for testing", top_k=3)

        # Load into new instance and query
        idx2 = EmbeddingIndex(index_dir)
        idx2._model = _make_fake_embedder()
        idx2.load()
        results_after = idx2.query("class for testing", top_k=3)

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
            ]
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
        idx.stage_defs(sample_defs)
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
        idx.stage_defs([new_def])
        count = idx.commit_staged()
        assert count == 1  # Only the new def was embedded
        assert idx.count == 2  # my_function (kept) + NewClass (new)

        uids = set(idx._uids)
        assert "file.py::my_function" in uids
        assert "other.py::NewClass" in uids
        assert "file.py::MyClass" not in uids
        assert "file.py::helper" not in uids
