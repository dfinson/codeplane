"""File-level embedding index using Jina v2 base.

One embedding per file.  Input text = full file content with
deterministic head+tail truncation.  No language-dependent logic.

Storage: .codeplane/file_embedding/
  - file_embeddings.npz   (float16 matrix + path arrays)
  - file_meta.json         (model name, dim, count, version)

Lifecycle mirrors EmbeddingIndex:
  - stage_file(path, content) → queue for embedding
  - stage_remove(paths)       → mark for removal
  - commit_staged()           → compute embeddings + persist
  - reload()                  → reload from disk
  - clear()                   → wipe
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger()

# ===================================================================
# Constants (centralized)
# ===================================================================

FILE_EMBED_MODEL = "jinaai/jina-embeddings-v2-base-en"
FILE_EMBED_DIM = 768
FILE_EMBED_MAX_CHARS = 24_000  # ~8000 tokens; Jina v2 supports 8192 tokens
FILE_EMBED_BATCH_SIZE = 8  # larger model → smaller batches
FILE_EMBED_VERSION = 1
FILE_EMBED_SUBDIR = "file_embedding"

# Head/tail truncation: keep 75% head + 25% tail
_HEAD_RATIO = 0.75


# ===================================================================
# Helpers
# ===================================================================


def _truncate_head_tail(text: str, max_chars: int = FILE_EMBED_MAX_CHARS) -> str:
    """Deterministic head+tail truncation.

    Keeps first 75% of budget from the head and last 25% from the tail.
    No language-dependent logic.
    """
    if len(text) <= max_chars:
        return text
    head_budget = int(max_chars * _HEAD_RATIO)
    tail_budget = max_chars - head_budget
    return text[:head_budget] + "\n...\n" + text[-tail_budget:]


def _detect_providers() -> list[str]:
    """Detect ONNX Runtime execution providers (GPU-aware)."""
    try:
        import onnxruntime as ort

        available = ort.get_available_providers()
        if "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    except ImportError:
        pass
    return ["CPUExecutionProvider"]


# ===================================================================
# FileEmbeddingIndex
# ===================================================================


class FileEmbeddingIndex:
    """File-level dense vector index.

    One embedding per file.  Incremental updates: only changed files
    are re-embedded.
    """

    def __init__(self, index_path: Path) -> None:
        self._dir = index_path / FILE_EMBED_SUBDIR
        self._dir.mkdir(parents=True, exist_ok=True)

        # In-memory state
        self._matrix: np.ndarray | None = None  # (N, DIM) float16
        self._paths: list[str] = []  # parallel to matrix rows
        self._path_to_idx: dict[str, int] = {}

        # Staging buffers
        self._staged_files: dict[str, str] = {}  # path → content
        self._staged_removals: set[str] = set()

        # Lazy model handle
        self._model: Any = None

    # --- Staging API ---

    def stage_file(self, path: str, content: str) -> None:
        """Stage a file for embedding (or re-embedding on change)."""
        self._staged_files[path] = content

    def stage_remove(self, paths: list[str]) -> None:
        """Mark file paths for removal from the index."""
        self._staged_removals.update(paths)

    def has_staged_changes(self) -> bool:
        """Return True if there are pending changes."""
        return bool(self._staged_files) or bool(self._staged_removals)

    # --- Commit ---

    def commit_staged(self) -> int:
        """Compute embeddings for staged files and persist.

        Returns number of files newly embedded.
        """
        if not self.has_staged_changes():
            return 0

        t0 = time.monotonic()

        # 1. Apply removals
        if self._staged_removals and self._matrix is not None:
            keep_mask = [p not in self._staged_removals for p in self._paths]
            if not all(keep_mask):
                keep_indices = [i for i, k in enumerate(keep_mask) if k]
                if keep_indices:
                    self._matrix = self._matrix[keep_indices]
                    self._paths = [self._paths[i] for i in keep_indices]
                else:
                    self._matrix = None
                    self._paths = []
                self._rebuild_index()

        # 2. Remove files that will be re-embedded
        if self._staged_files and self._matrix is not None:
            re_embed_paths = set(self._staged_files.keys()) & set(self._paths)
            if re_embed_paths:
                keep_mask = [p not in re_embed_paths for p in self._paths]
                keep_indices = [i for i, k in enumerate(keep_mask) if k]
                if keep_indices:
                    self._matrix = self._matrix[keep_indices]
                    self._paths = [self._paths[i] for i in keep_indices]
                else:
                    self._matrix = None
                    self._paths = []
                self._rebuild_index()

        # 3. Embed new files
        new_count = 0
        if self._staged_files:
            paths_to_embed = list(self._staged_files.keys())
            texts = [_truncate_head_tail(self._staged_files[p]) for p in paths_to_embed]

            self._ensure_model()
            vectors = self._embed_batch(texts)

            if self._matrix is not None and len(self._paths) > 0:
                self._matrix = np.vstack([self._matrix, vectors])
            else:
                self._matrix = vectors

            self._paths.extend(paths_to_embed)
            self._rebuild_index()
            new_count = len(paths_to_embed)

        # 4. Clear staging
        self._staged_files.clear()
        self._staged_removals.clear()

        # 5. Persist
        self._save()

        elapsed = time.monotonic() - t0
        log.info(
            "file_embedding.commit",
            new_files=new_count,
            total_files=len(self._paths),
            elapsed_ms=round(elapsed * 1000),
        )
        return new_count

    # --- Query API ---

    def query(self, text: str, top_k: int = 100) -> list[tuple[str, float]]:
        """Embed query text and compute cosine similarity against all files.

        Returns list of (path, similarity) sorted descending, top_k.
        """
        if self._matrix is None or len(self._paths) == 0:
            return []

        self._ensure_model()
        q_vec = self._embed_single(text)

        # Cosine similarity (matrix is L2-normalized)
        sims = self._matrix @ q_vec  # (N,)
        if len(sims) <= top_k:
            indices = np.argsort(-sims)
        else:
            # Partial sort for efficiency
            top_indices = np.argpartition(-sims, top_k)[:top_k]
            indices = top_indices[np.argsort(-sims[top_indices])]

        results: list[tuple[str, float]] = []
        for idx in indices:
            sim = float(sims[idx])
            if sim <= 0:
                break
            results.append((self._paths[idx], sim))

        return results

    @property
    def count(self) -> int:
        """Number of indexed files."""
        return len(self._paths)

    @property
    def paths(self) -> list[str]:
        """All indexed file paths."""
        return list(self._paths)

    def get_embedding(self, path: str) -> np.ndarray | None:
        """Get the embedding vector for a specific file path."""
        idx = self._path_to_idx.get(path)
        if idx is None or self._matrix is None:
            return None
        return self._matrix[idx].astype(np.float32)

    # --- Lifecycle ---

    def load(self) -> bool:
        """Load index from disk.  Returns True if loaded successfully."""
        npz_path = self._dir / "file_embeddings.npz"
        meta_path = self._dir / "file_meta.json"

        if not npz_path.exists() or not meta_path.exists():
            return False

        try:
            meta = json.loads(meta_path.read_text())
            if meta.get("version") != FILE_EMBED_VERSION:
                log.warning("file_embedding.version_mismatch", expected=FILE_EMBED_VERSION)
                return False

            data = np.load(str(npz_path), allow_pickle=False)
            self._matrix = data["matrix"].astype(np.float16)
            # numpy stores strings as fixed-width; decode to Python strings
            self._paths = list(data["paths"])
            self._rebuild_index()

            log.info(
                "file_embedding.loaded",
                files=len(self._paths),
                dim=self._matrix.shape[1] if self._matrix is not None else 0,
            )
            return True
        except Exception:
            log.exception("file_embedding.load_error")
            return False

    def reload(self) -> bool:
        """Reload from disk (alias for load)."""
        return self.load()

    def clear(self) -> None:
        """Wipe all embeddings (memory + disk)."""
        self._matrix = None
        self._paths = []
        self._path_to_idx = {}
        self._staged_files.clear()
        self._staged_removals.clear()

        npz_path = self._dir / "file_embeddings.npz"
        meta_path = self._dir / "file_meta.json"
        if npz_path.exists():
            npz_path.unlink()
        if meta_path.exists():
            meta_path.unlink()

    # --- Internals ---

    def _ensure_model(self) -> None:
        """Lazy-load the embedding model."""
        if self._model is not None:
            return

        from fastembed import TextEmbedding

        providers = _detect_providers()
        threads = max(1, (os.cpu_count() or 2) // 2)

        self._model = TextEmbedding(
            model_name=FILE_EMBED_MODEL,
            providers=providers,
            threads=threads,
        )
        log.info(
            "file_embedding.model_loaded",
            model=FILE_EMBED_MODEL,
            providers=providers,
            threads=threads,
        )

    def _embed_single(self, text: str) -> np.ndarray:
        """Embed a single text, return L2-normalized float32 vector."""
        truncated = _truncate_head_tail(text)
        vecs = list(self._model.embed([truncated], batch_size=1))
        vec = np.array(vecs[0], dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        """Embed a batch of texts, return L2-normalized float16 matrix."""
        if not texts:
            return np.empty((0, FILE_EMBED_DIM), dtype=np.float16)

        all_vecs: list[np.ndarray] = []
        for i in range(0, len(texts), FILE_EMBED_BATCH_SIZE):
            batch = texts[i : i + FILE_EMBED_BATCH_SIZE]
            vecs = list(self._model.embed(batch, batch_size=len(batch)))
            all_vecs.extend(vecs)

        matrix = np.array(all_vecs, dtype=np.float32)
        # L2-normalize each row
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms > 0, norms, 1.0)
        matrix /= norms
        return matrix.astype(np.float16)

    def _rebuild_index(self) -> None:
        """Rebuild the path→index lookup."""
        self._path_to_idx = {p: i for i, p in enumerate(self._paths)}

    def _save(self) -> None:
        """Persist to disk."""
        npz_path = self._dir / "file_embeddings.npz"
        meta_path = self._dir / "file_meta.json"

        if self._matrix is not None and len(self._paths) > 0:
            np.savez_compressed(
                str(npz_path),
                matrix=self._matrix,
                paths=np.array(self._paths, dtype=str),
            )
        elif npz_path.exists():
            npz_path.unlink()

        meta = {
            "version": FILE_EMBED_VERSION,
            "model": FILE_EMBED_MODEL,
            "dim": FILE_EMBED_DIM,
            "file_count": len(self._paths),
        }
        meta_path.write_text(json.dumps(meta, indent=2))
