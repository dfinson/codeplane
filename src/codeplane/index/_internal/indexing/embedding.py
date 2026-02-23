"""Dense vector index for definition embeddings.

Peer subsystem of LexicalIndex (Tantivy) and StructuralIndexer (tree-sitter).
Uses fastembed (ONNX-based) for embedding computation and numpy for storage
and cosine similarity search.

Lifecycle mirrors LexicalIndex:
  - stage_defs()    → accumulate defs for embedding
  - stage_remove()  → mark def_uids for removal
  - commit_staged() → compute embeddings + persist
  - reload()        → reload from disk
  - clear()         → wipe all embeddings

Storage: .codeplane/embedding/
  - embeddings.npz   (float16 matrix + def_uid string array)
  - metadata.json    (model name, dim, count, version)
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger()

_MODEL_NAME = "jinaai/jina-embeddings-v2-base-code"
_EMBEDDING_DIM = 768
_MAX_TEXT_CHARS = 2000
_METADATA_VERSION = 1


def _detect_providers() -> list[str]:
    """Detect available ONNX Runtime execution providers.

    Returns a provider list suitable for ``fastembed.TextEmbedding(providers=...)``.
    Prefers GPU (CUDA) when available, falls back to CPU.
    """
    try:
        import onnxruntime as ort  # type: ignore[import-not-found]

        available = set(ort.get_available_providers())
    except Exception:  # onnxruntime not importable or provider query fails
        return []

    providers: list[str] = []
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    # Always include CPU as fallback
    providers.append("CPUExecutionProvider")
    return providers


class EmbeddingIndex:
    """Dense vector index for definition embeddings.

    Initialized in IndexCoordinator.__init__, participates in the same
    single-pass index/reindex cycle as LexicalIndex and StructuralIndexer.
    """

    def __init__(self, index_path: Path) -> None:
        """Create index.  Model loaded lazily on first commit_staged()."""
        self._index_path = Path(index_path)
        self._index_path.mkdir(parents=True, exist_ok=True)

        # In-memory state
        self._matrix: np.ndarray[Any, np.dtype[np.float16]] | None = None
        self._uids: list[str] = []  # parallel to matrix rows

        # Staging buffers
        self._staged_defs: list[dict[str, Any]] = []
        self._staged_removals: set[str] = set()

        # Lazy model handle
        self._model: Any | None = None
        self._disabled = False

    # ------------------------------------------------------------------
    # Staging API (mirrors LexicalIndex)
    # ------------------------------------------------------------------

    def stage_defs(self, defs: list[dict[str, Any]]) -> None:
        """Stage def dicts from ExtractionResult for embedding.

        Accumulates defs in memory.  Actual embedding computation
        happens in commit_staged() to batch efficiently.

        Args:
            defs: Raw def dicts from ExtractionResult.defs.
                  Required keys: def_uid, name, kind.
                  Optional: qualified_name, docstring, signature_text.
        """
        self._staged_defs.extend(defs)

    def stage_remove(self, def_uids: set[str]) -> None:
        """Stage def_uids for removal (file deleted or re-indexed)."""
        self._staged_removals |= def_uids

    def has_staged_changes(self) -> bool:
        """True if there are uncommitted staged changes."""
        return bool(self._staged_defs) or bool(self._staged_removals)

    def commit_staged(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Compute embeddings for all staged defs and persist.

        Args:
            on_progress: Optional callback(embedded_so_far, total) for progress.

        Returns count of defs embedded in this commit.
        """
        if not self.has_staged_changes():
            return 0

        # Apply removals first
        if self._staged_removals and self._matrix is not None and self._uids:
            keep_mask = np.array(
                [uid not in self._staged_removals for uid in self._uids],
                dtype=bool,
            )
            if not keep_mask.all():
                self._matrix = self._matrix[keep_mask]
                self._uids = [uid for uid, keep in zip(self._uids, keep_mask, strict=True) if keep]
                if len(self._uids) == 0:
                    self._matrix = None
        self._staged_removals.clear()

        # Embed new defs
        count = 0
        if self._staged_defs:
            if self._disabled:
                self._staged_defs.clear()
                return 0

            self._ensure_model()
            if self._disabled:
                self._staged_defs.clear()
                return 0

            # Deduplicate by def_uid (last wins)
            uid_to_def: dict[str, dict[str, Any]] = {}
            for d in self._staged_defs:
                uid = d.get("def_uid")
                if uid:
                    uid_to_def[uid] = d
            self._staged_defs.clear()

            if not uid_to_def:
                return 0

            # Remove any existing entries for these uids (re-index case)
            if self._matrix is not None and self._uids:
                new_uid_set = set(uid_to_def.keys())
                keep_mask = np.array(
                    [uid not in new_uid_set for uid in self._uids],
                    dtype=bool,
                )
                if not keep_mask.all():
                    self._matrix = self._matrix[keep_mask]
                    self._uids = [
                        uid for uid, keep in zip(self._uids, keep_mask, strict=True) if keep
                    ]
                    if len(self._uids) == 0:
                        self._matrix = None

            # Build text inputs
            texts: list[str] = []
            new_uids: list[str] = []
            for uid, d in uid_to_def.items():
                texts.append(self._def_to_text(d))
                new_uids.append(uid)

            # Batch embed via fastembed (consume generator for progress)
            assert self._model is not None  # guaranteed by _ensure_model
            total = len(texts)
            start = time.monotonic()
            embeddings_list: list[Any] = []
            for i, vec in enumerate(self._model.embed(texts)):
                embeddings_list.append(vec)
                if on_progress is not None and (i % 50 == 0 or i == total - 1):
                    on_progress(i + 1, total)
            elapsed = time.monotonic() - start
            log.info(
                "embedding.commit",
                count=total,
                elapsed_ms=round(elapsed * 1000),
            )

            # Convert to float16 L2-normed matrix
            new_matrix = np.array(embeddings_list, dtype=np.float32)
            # L2 normalize
            norms = np.linalg.norm(new_matrix, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-10)
            new_matrix = new_matrix / norms
            new_matrix = new_matrix.astype(np.float16)

            # Append to existing matrix
            if self._matrix is not None and len(self._uids) > 0:
                self._matrix = np.vstack([self._matrix, new_matrix])
            else:
                self._matrix = new_matrix
            self._uids.extend(new_uids)
            count = len(new_uids)

        # Persist to disk
        self._save()
        return count

    def discard_staged(self) -> int:
        """Discard staged changes.  Returns count discarded."""
        count = len(self._staged_defs) + len(self._staged_removals)
        self._staged_defs.clear()
        self._staged_removals.clear()
        return count

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def query(self, text: str, top_k: int = 50) -> list[tuple[str, float]]:
        """Cosine similarity search.

        Returns [(def_uid, similarity), ...] sorted descending.
        """
        results = self.query_batch([text], top_k=top_k)
        return results[0] if results else []

    def query_batch(
        self,
        texts: list[str],
        *,
        top_k: int = 50,
    ) -> list[list[tuple[str, float]]]:
        """Batch cosine similarity search across multiple query texts.

        Embeds all *texts* in a single ``model.embed()`` call (one ONNX
        forward pass) and runs each resulting vector against the index
        matrix.  Significantly faster than calling :meth:`query` in a
        loop when multiple views are needed.

        Returns one ``[(def_uid, similarity)]`` list per input text,
        each sorted descending.
        """
        if not texts:
            return []

        if self._matrix is None or len(self._uids) == 0:
            return [[] for _ in texts]

        if self._disabled:
            return [[] for _ in texts]

        self._ensure_model()
        if self._disabled:
            return [[] for _ in texts]

        # Embed all queries in one batch
        assert self._model is not None  # guaranteed by _ensure_model
        raw_vecs = list(self._model.embed(texts))
        query_matrix = np.array(raw_vecs, dtype=np.float32)

        # L2 normalize
        norms = np.linalg.norm(query_matrix, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        query_matrix = query_matrix / norms

        # Cosine similarity = dot product (index matrix is L2-normed)
        matrix_f32 = self._matrix.astype(np.float32)
        all_scores = query_matrix @ matrix_f32.T  # (n_queries, n_defs)

        # Top-k per query
        results: list[list[tuple[str, float]]] = []
        for row_idx in range(all_scores.shape[0]):
            scores = all_scores[row_idx]
            k = min(top_k, len(scores))
            top_indices = np.argpartition(scores, -k)[-k:]
            top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]
            results.append([(self._uids[i], float(scores[i])) for i in top_indices])

        return results

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """Load from disk.  Returns False if no index exists."""
        npz_path = self._index_path / "embeddings.npz"
        meta_path = self._index_path / "metadata.json"

        if not npz_path.exists() or not meta_path.exists():
            return False

        try:
            with meta_path.open() as f:
                meta = json.load(f)

            if meta.get("version") != _METADATA_VERSION:
                log.warning(
                    "embedding.version_mismatch",
                    expected=_METADATA_VERSION,
                    got=meta.get("version"),
                )
                return False

            data = np.load(npz_path, allow_pickle=False)
            self._matrix = data["matrix"]  # float16
            # UIDs stored as fixed-length byte strings → decode
            self._uids = [
                s.decode("utf-8") if isinstance(s, bytes) else str(s) for s in data["uids"]
            ]

            log.info(
                "embedding.loaded",
                count=len(self._uids),
                dim=self._matrix.shape[1] if self._matrix is not None else 0,
            )
            return True
        except Exception:
            log.warning("embedding.load_failed", exc_info=True)
            self._matrix = None
            self._uids = []
            return False

    def reload(self) -> None:
        """Reload embeddings from disk."""
        self.load()

    def clear(self) -> None:
        """Clear all embeddings (in-memory and on disk)."""
        self._matrix = None
        self._uids = []
        self._staged_defs.clear()
        self._staged_removals.clear()

        npz_path = self._index_path / "embeddings.npz"
        meta_path = self._index_path / "metadata.json"
        if npz_path.exists():
            npz_path.unlink()
        if meta_path.exists():
            meta_path.unlink()

    @property
    def count(self) -> int:
        """Number of embedded definitions."""
        return len(self._uids)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_model(self) -> None:
        """Lazy-load fastembed TextEmbedding model with GPU auto-detect.

        Probes ONNX Runtime execution providers and prefers CUDA when
        available, falling back to CPU.  The selected provider is logged
        so operators can confirm GPU acceleration is active.
        """
        if self._model is not None or self._disabled:
            return

        try:
            from fastembed import TextEmbedding  # type: ignore[import-not-found]

            providers = _detect_providers()
            start = time.monotonic()
            kwargs: dict[str, Any] = {"model_name": _MODEL_NAME}
            if providers:
                kwargs["providers"] = providers
            self._model = TextEmbedding(**kwargs)
            elapsed = time.monotonic() - start
            log.info(
                "embedding.model_loaded",
                model=_MODEL_NAME,
                providers=providers or ["CPUExecutionProvider"],
                elapsed_s=round(elapsed, 2),
            )
        except ImportError:
            log.warning(
                "embedding.fastembed_not_installed",
                hint="pip install fastembed",
            )
            self._disabled = True
        except Exception:
            log.warning("embedding.model_load_failed", exc_info=True)
            self._disabled = True

    @staticmethod
    def _def_to_text(d: dict[str, Any]) -> str:
        """Build embedding input from a def dict.

        Format: "{kind} {qualified_name}\\n{signature_text}\\n{docstring}"
        Truncated to ~2000 chars.
        """
        kind = d.get("kind", "")
        qname = d.get("qualified_name") or d.get("name", "")
        sig = d.get("signature_text") or ""
        doc = d.get("docstring") or ""

        text = f"{kind} {qname}"
        if sig:
            text += f"\n{sig}"
        if doc:
            text += f"\n{doc}"

        return text[:_MAX_TEXT_CHARS]

    def _save(self) -> None:
        """Persist to disk as compressed numpy arrays."""
        npz_path = self._index_path / "embeddings.npz"
        meta_path = self._index_path / "metadata.json"

        if self._matrix is None or len(self._uids) == 0:
            # Remove files if index is empty
            if npz_path.exists():
                npz_path.unlink()
            if meta_path.exists():
                meta_path.unlink()
            return

        # Save matrix + uids
        uids_array = np.array(self._uids, dtype="U")
        np.savez_compressed(
            npz_path,
            matrix=self._matrix,
            uids=uids_array,
        )

        # Save metadata
        meta = {
            "version": _METADATA_VERSION,
            "model": _MODEL_NAME,
            "dim": _EMBEDDING_DIM,
            "count": len(self._uids),
        }
        with meta_path.open("w") as f:
            json.dump(meta, f, indent=2)
