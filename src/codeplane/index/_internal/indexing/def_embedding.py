"""Per-DefFact embedding index using bge-small-en-v1.5.

Each code DefFact (function, method, class, struct, etc.) gets its own
embedding vector.  Non-code kinds (pair, key, table, target, heading) are
excluded — those are handled by the file-level embedding index.

The embedded text is a compact anglicized per-def scaffold::

    DEF_SCAFFOLD
    module <file path phrase>
    <kind> <anglicized name>(<compact signature>)
    calls <callees within this def's body>
    mentions <string literals within this def>
    describes <first sentence of docstring>

Components are ordered by measured marginal recall contribution
(callees > strings > signature > docstring) and greedily filled
until the model's 512-token context window is exhausted.

Per-def scaffolds are tiny: median ~48 chars (~14 tokens), p95 ~151 chars.
99.9% fall under 500 chars.  With adaptive batching (batch size ×4 for short
texts), ONNX attention cost is dominated by sequence length, not count.

Storage: .codeplane/def_embedding/
  - def_embeddings.npz    (float16 matrix + uid arrays)
  - def_meta.json          (model name, dim, count, version)

Lifecycle:
  - stage_defs(path, defs)         → queue defs from a file for embedding
  - stage_remove_by_path(paths)    → mark for removal by file path
  - commit_staged()                → compute embeddings + persist
  - reload() / load()              → reload from disk
  - clear()                        → wipe
"""

from __future__ import annotations

import gc
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from codeplane.index._internal.indexing.file_embedding import (
    FILE_EMBED_DIM,
    FILE_EMBED_MAX_CHARS,
    FILE_EMBED_MAX_LENGTH,
    FILE_EMBED_MODEL,
    _compact_sig,
    _detect_batch_size,
    _detect_providers,
    _path_to_phrase,
    _truncate_semantic,
    _word_split,
)

log = structlog.get_logger()

# ===================================================================
# Constants
# ===================================================================

DEF_EMBED_VERSION = 1
DEF_EMBED_SUBDIR = "def_embedding"
DEF_EMBED_BATCH_SIZE = 8  # default; overridden by _detect_batch_size()

# Code kinds that get per-def embedding. Non-code kinds (config/doc) are
# handled by the file-level embedding index.
_CODE_KINDS = frozenset(
    {
        "function",
        "method",
        "class",
        "struct",
        "interface",
        "trait",
        "enum",
        "property",
        "constant",
        "variable",
        "module",
    }
)

# Approximate char budget for greedy scaffold fill.
# bge-small-en-v1.5 context = 512 tokens ≈ 1,800 scaffold chars.
# Not arbitrary — derived from the model's fixed positional embedding.
_DEF_SCAFFOLD_CHAR_BUDGET = 1_800


# ===================================================================
# Per-def scaffold builder
# ===================================================================


def build_def_scaffold(
    file_path: str,
    d: dict[str, Any],
) -> str:
    """Build an anglicized scaffold for a single DefFact.

    Returns a compact text suitable for embedding that captures the
    def's identity, purpose, and context within the file.

    Components are filled in priority order derived from ablation data
    (marginal recall@100): name > callees(+4.8%) > strings(+1.5%)
    > signature(+0.7%) > docstring(+0.2%). Decorators are excluded
    (net negative: -0.4%). Filling stops when the model's 512-token
    context window (~1800 chars) is exhausted.
    """
    kind = d.get("kind", "")
    name = d.get("name", "")
    if not name:
        return ""

    budget = _DEF_SCAFFOLD_CHAR_BUDGET

    # --- Fixed header (always present: identity) ---
    lines: list[str] = []

    # Module context from file path
    path_phrase = _path_to_phrase(file_path)
    if path_phrase:
        lines.append(f"module {path_phrase}")

    # Kind + name (signature added later at its priority)
    name_words = " ".join(_word_split(name))
    lines.append(f"{kind} {name_words}")

    # Parent class/module context
    qualified = d.get("qualified_name", "") or d.get("lexical_path", "") or ""
    if qualified and "." in qualified:
        parent = qualified.rsplit(".", 1)[0]
        parent_words = " ".join(_word_split(parent))
        if parent_words:
            lines.append(f"in {parent_words}")

    used = sum(len(ln) + 1 for ln in lines)  # +1 for newline

    # --- Priority 1: Callees (+4.8% marginal recall) ---
    sf = d.get("_sem_facts", {})
    calls = sf.get("calls", []) if isinstance(sf, dict) else []
    if calls:
        sorted_calls = sorted({c for c in calls if c and len(c) >= 2})
        call_line = f"calls {', '.join(sorted_calls)}"
        if used + len(call_line) + 1 <= budget:
            lines.append(call_line)
            used += len(call_line) + 1
        else:
            # Fit as many callees as the remaining budget allows
            remaining = budget - used - len("calls ") - 1
            if remaining > 4:
                partial: list[str] = []
                partial_len = 0
                for c in sorted_calls:
                    add_len = len(c) + (2 if partial else 0)
                    if partial_len + add_len > remaining:
                        break
                    partial.append(c)
                    partial_len += add_len
                if partial:
                    call_line = f"calls {', '.join(partial)}"
                    lines.append(call_line)
                    used += len(call_line) + 1

    # --- Priority 2: String literals (+1.5% marginal recall) ---
    lits = d.get("_string_literals", [])
    if lits and used < budget:
        clean_lits: list[str] = []
        chars_used = 0
        remaining = budget - used - len("mentions ") - 1
        for lit in lits:
            lit_clean = lit.strip()
            if lit_clean.lower() in ("true", "false", "none", "", "0", "1"):
                continue
            if len(lit_clean) < 3:
                continue
            add_len = len(lit_clean) + (2 if clean_lits else 0)
            if chars_used + add_len > remaining:
                break
            clean_lits.append(lit_clean)
            chars_used += add_len
        if clean_lits:
            lit_line = f"mentions {', '.join(clean_lits)}"
            lines.append(lit_line)
            used += len(lit_line) + 1

    # --- Priority 3: Signature (+0.7% marginal recall) ---
    sig = d.get("signature_text", "") or ""
    if sig and used < budget:
        compact = _compact_sig(name, sig)
        # Replace the kind+name line with kind+compact_sig
        kind_line = f"{kind} {compact}"
        old_kind_line = f"{kind} {name_words}"
        for i, ln in enumerate(lines):
            if ln == old_kind_line:
                extra = len(kind_line) - len(old_kind_line)
                if used + extra <= budget:
                    lines[i] = kind_line
                    used += extra
                break

    # --- Priority 4: Docstring (+0.2% marginal recall) ---
    doc = (d.get("docstring") or "").strip()
    if doc and len(doc) > 15 and used < budget:
        first_sentence = doc.split(".")[0].strip() if "." in doc else doc
        remaining = budget - used - len("describes ") - 1
        if remaining > 10:
            doc_text = first_sentence[:remaining]
            doc_line = f"describes {doc_text}"
            lines.append(doc_line)
            used += len(doc_line) + 1

    # Decorators intentionally excluded (ablation: -0.4% net negative)

    if not lines:
        return ""

    return "DEF_SCAFFOLD\n" + "\n".join(lines)


def _is_code_kind(kind: str) -> bool:
    """Return True if the def kind should get a per-def embedding."""
    return kind in _CODE_KINDS


# ===================================================================
# DefEmbeddingIndex
# ===================================================================


class DefEmbeddingIndex:
    """Per-DefFact dense vector index for code objects.

    One embedding per code DefFact. Non-code kinds (pair, key, table,
    target, heading) are excluded — handled by file-level embedding.

    Keyed by def_uid for direct lookup. File path is tracked for
    bulk removal on file change.
    """

    def __init__(self, index_path: Path) -> None:
        self._dir = index_path / DEF_EMBED_SUBDIR
        self._dir.mkdir(parents=True, exist_ok=True)

        # In-memory state
        self._matrix: np.ndarray | None = None  # (N, DIM) float16
        self._uids: list[str] = []  # parallel to matrix rows
        self._paths: list[str] = []  # parallel to matrix rows (file path per def)
        self._uid_to_idx: dict[str, int] = {}  # uid → row index

        # Staging buffers
        # path → list of (uid, scaffold_text)
        self._staged_defs: dict[str, list[tuple[str, str]]] = {}
        self._staged_removals: set[str] = set()  # file paths to remove

        # Lazy model handle + dynamic batch size
        self._model: Any = None
        self._batch_size: int = DEF_EMBED_BATCH_SIZE

    def set_shared_model(self, model: Any, batch_size: int) -> None:
        """Use a shared model instance instead of loading a separate one.

        Call this before any embed operation to avoid loading the ONNX
        model twice (saves ~15-30s of ONNX compilation).

        Takes the model but uses its OWN batch size (higher than file
        embedding because def scaffolds are much shorter).
        """
        self._model = model
        # Don't use the caller's batch_size — def scaffolds are ~7× shorter
        # than file scaffolds so we can batch much more aggressively.
        # Use the higher of our current batch size or 4× the caller's.
        self._batch_size = max(self._batch_size, batch_size * 4)

    # --- Staging API ---

    def stage_defs(
        self,
        file_path: str,
        defs: list[dict[str, Any]],
    ) -> None:
        """Stage defs from a file for embedding.

        All def kinds (code and non-code) get per-def embeddings.
        Non-code kinds (pair, key, table, target, heading) use
        a simplified scaffold with name + kind + file context.

        Args:
            file_path: Relative file path.
            defs: Tree-sitter extracted definitions (from ExtractionResult).
        """
        entries: list[tuple[str, str]] = []
        for d in defs:
            uid = d.get("def_uid", "")
            if not uid:
                uid = d.get("lexical_path", "")
            if not uid:
                continue
            scaffold = build_def_scaffold(file_path, d)
            if scaffold:
                entries.append((uid, scaffold))

        if entries:
            self._staged_defs[file_path] = entries

    def stage_remove_by_path(self, paths: list[str]) -> None:
        """Mark all defs from these file paths for removal."""
        self._staged_removals.update(paths)

    def has_staged_changes(self) -> bool:
        """Return True if there are pending changes."""
        return bool(self._staged_defs) or bool(self._staged_removals)

    # --- Commit ---

    def commit_staged(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Compute embeddings for staged defs and persist.

        Returns number of defs newly embedded.
        """
        if not self.has_staged_changes():
            return 0

        t0 = time.monotonic()

        # 1. Apply removals (by file path)
        if self._staged_removals and self._matrix is not None:
            keep_mask = [p not in self._staged_removals for p in self._paths]
            if not all(keep_mask):
                keep_indices = [i for i, k in enumerate(keep_mask) if k]
                if keep_indices:
                    self._matrix = self._matrix[keep_indices]
                    self._uids = [self._uids[i] for i in keep_indices]
                    self._paths = [self._paths[i] for i in keep_indices]
                else:
                    self._matrix = None
                    self._uids = []
                    self._paths = []
                self._rebuild_index()

        # 2. Remove defs from files that will be re-embedded
        if self._staged_defs and self._matrix is not None:
            re_embed_paths = set(self._staged_defs.keys()) & set(self._paths)
            if re_embed_paths:
                keep_mask = [p not in re_embed_paths for p in self._paths]
                keep_indices = [i for i, k in enumerate(keep_mask) if k]
                if keep_indices:
                    self._matrix = self._matrix[keep_indices]
                    self._uids = [self._uids[i] for i in keep_indices]
                    self._paths = [self._paths[i] for i in keep_indices]
                else:
                    self._matrix = None
                    self._uids = []
                    self._paths = []
                self._rebuild_index()

        # 3. Embed new defs
        new_count = 0
        if self._staged_defs:
            all_texts: list[str] = []
            all_uids: list[str] = []
            all_paths: list[str] = []
            for file_path, entries in self._staged_defs.items():
                for uid, scaffold_text in entries:
                    all_texts.append(scaffold_text)
                    all_uids.append(uid)
                    all_paths.append(file_path)

            self._ensure_model()
            vectors = self._embed_batch(all_texts, on_progress=on_progress)

            if self._matrix is not None and len(self._uids) > 0:
                self._matrix = np.vstack([self._matrix, vectors])
            else:
                self._matrix = vectors

            self._uids.extend(all_uids)
            self._paths.extend(all_paths)
            self._rebuild_index()
            new_count = len(all_uids)

        # 4. Clear staging
        self._staged_defs.clear()
        self._staged_removals.clear()

        # 5. Persist
        self._save()

        elapsed = time.monotonic() - t0
        log.info(
            "def_embedding.commit",
            new_defs=new_count,
            total_defs=len(self._uid_to_idx),
            elapsed_ms=round(elapsed * 1000),
        )
        return new_count

    # --- Query API ---

    def query(self, text: str) -> list[tuple[str, float]]:
        """Embed query text and compute cosine similarity against all defs.

        Returns list of (def_uid, similarity) sorted descending.
        All defs with positive similarity are returned.
        """
        if self._matrix is None or len(self._uids) == 0:
            return []

        self._ensure_model()
        q_vec = self._embed_single(text)

        # Cosine similarity (matrix is L2-normalized)
        sims = self._matrix @ q_vec  # (N,)

        # Sort descending
        top_indices = np.argsort(sims)[::-1]

        results: list[tuple[str, float]] = []
        for idx in top_indices:
            sim = float(sims[idx])
            if sim <= 0:
                break
            results.append((self._uids[idx], sim))

        return results

    def query_by_uids(self, text: str, uids: list[str]) -> dict[str, float]:
        """Compute similarity for specific def_uids only.

        Returns dict of {def_uid: similarity} for requested uids that exist
        in the index.
        """
        if self._matrix is None or not uids:
            return {}

        self._ensure_model()
        q_vec = self._embed_single(text)

        result: dict[str, float] = {}
        for uid in uids:
            idx = self._uid_to_idx.get(uid)
            if idx is not None:
                result[uid] = float(self._matrix[idx] @ q_vec)
        return result

    @property
    def count(self) -> int:
        """Number of indexed defs."""
        return len(self._uid_to_idx)

    # --- Lifecycle ---

    def load(self) -> bool:
        """Load index from disk. Returns True if loaded successfully."""
        npz_path = self._dir / "def_embeddings.npz"
        meta_path = self._dir / "def_meta.json"

        if not npz_path.exists() or not meta_path.exists():
            return False

        try:
            meta = json.loads(meta_path.read_text())
            if meta.get("version") != DEF_EMBED_VERSION:
                log.warning("def_embedding.version_mismatch", expected=DEF_EMBED_VERSION)
                return False

            data = np.load(str(npz_path), allow_pickle=False)
            self._matrix = data["matrix"].astype(np.float16)
            self._uids = list(data["uids"])
            self._paths = list(data["paths"])
            self._rebuild_index()

            log.info(
                "def_embedding.loaded",
                defs=len(self._uids),
                dim=self._matrix.shape[1] if self._matrix is not None else 0,
            )
            return True
        except Exception:
            log.exception("def_embedding.load_error")
            return False

    def reload(self) -> bool:
        """Reload from disk (alias for load)."""
        return self.load()

    def prune_missing(self, repo_root: Path) -> int:
        """Remove embeddings for files that no longer exist on disk.

        Returns number of defs pruned.
        """
        if self._matrix is None or not self._paths:
            return 0

        stale_paths = {p for p in set(self._paths) if not (repo_root / p).exists()}
        if not stale_paths:
            return 0

        keep_mask = [p not in stale_paths for p in self._paths]
        keep_indices = [i for i, k in enumerate(keep_mask) if k]
        before = len(self._uids)
        if keep_indices:
            self._matrix = self._matrix[keep_indices]
            self._uids = [self._uids[i] for i in keep_indices]
            self._paths = [self._paths[i] for i in keep_indices]
        else:
            self._matrix = None
            self._uids = []
            self._paths = []
        self._rebuild_index()
        self._save()

        pruned = before - len(self._uids)
        log.info(
            "def_embedding.pruned_missing",
            removed=pruned,
            remaining=len(self._uid_to_idx),
        )
        return pruned

    def clear(self) -> None:
        """Wipe all embeddings (memory + disk)."""
        self._matrix = None
        self._uids = []
        self._paths = []
        self._uid_to_idx = {}
        self._staged_defs.clear()
        self._staged_removals.clear()

        npz_path = self._dir / "def_embeddings.npz"
        meta_path = self._dir / "def_meta.json"
        if npz_path.exists():
            npz_path.unlink()
        if meta_path.exists():
            meta_path.unlink()

    # --- Internals ---

    def _ensure_model(self) -> None:
        """Lazy-load the embedding model and detect optimal batch size.

        Uses a higher base batch size than file embedding because per-def
        scaffolds are tiny (median ~48 chars / ~14 tokens).  ONNX attention
        cost scales quadratically with sequence length, so these short texts
        can use much larger batches without extra memory or time.
        """
        if self._model is not None:
            return

        from fastembed import TextEmbedding

        gc.collect()

        providers = _detect_providers()
        threads = max(1, (os.cpu_count() or 2) // 2)
        # Use 4× the file embedding base batch size — def scaffolds are
        # ~7× shorter than file scaffolds, so quadratic attention cost is
        # ~50× lower per element.  This makes large batches safe and fast.
        self._batch_size = _detect_batch_size() * 4

        self._model = TextEmbedding(
            model_name=FILE_EMBED_MODEL,
            providers=providers,
            threads=threads,
            max_length=FILE_EMBED_MAX_LENGTH,
        )
        log.info(
            "def_embedding.model_loaded",
            model=FILE_EMBED_MODEL,
            providers=providers,
            threads=threads,
            batch_size=self._batch_size,
        )

    def _embed_single(self, text: str) -> np.ndarray:
        """Embed a single text (query), return L2-normalized float32 vector."""
        truncated = _truncate_semantic(text, max_chars=FILE_EMBED_MAX_CHARS)
        vecs = list(self._model.embed([truncated], batch_size=1))
        vec = np.array(vecs[0], dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def _embed_batch(
        self,
        texts: list[str],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> np.ndarray:
        """Embed a batch of texts, return L2-normalized float16 matrix.

        Uses the same adaptive batching strategy as file embedding:
        texts sorted by length, short texts use larger batches (ONNX
        attention cost is quadratic in sequence length).
        """
        if not texts:
            return np.empty((0, FILE_EMBED_DIM), dtype=np.float16)

        total = len(texts)
        base_batch = getattr(self, "_batch_size", DEF_EMBED_BATCH_SIZE)

        # Sort by length → similar-length texts batch together → less padding
        order = sorted(range(total), key=lambda i: len(texts[i]))
        sorted_texts = [texts[i] for i in order]

        sorted_vecs: list[np.ndarray] = []
        embedded = 0
        while embedded < total:
            char_len = len(sorted_texts[embedded])
            if char_len < 500:
                batch_size = base_batch * 4
            elif char_len < 1200:
                batch_size = base_batch * 2
            else:
                batch_size = base_batch

            batch = sorted_texts[embedded : embedded + batch_size]
            vecs = list(self._model.embed(batch, batch_size=len(batch)))
            sorted_vecs.extend(vecs)
            embedded += len(batch)

            if on_progress is not None:
                on_progress(min(embedded, total), total)

        # Restore original order
        inverse = [0] * total
        for new_pos, orig_pos in enumerate(order):
            inverse[orig_pos] = new_pos

        all_vecs = [sorted_vecs[inverse[i]] for i in range(total)]

        matrix = np.array(all_vecs, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms > 0, norms, 1.0)
        matrix /= norms
        return matrix.astype(np.float16)

    def _rebuild_index(self) -> None:
        """Rebuild the uid→index lookup."""
        self._uid_to_idx = {}
        for i, uid in enumerate(self._uids):
            if uid not in self._uid_to_idx:
                self._uid_to_idx[uid] = i

    def _save(self) -> None:
        """Persist to disk."""
        npz_path = self._dir / "def_embeddings.npz"
        meta_path = self._dir / "def_meta.json"

        if self._matrix is not None and len(self._uids) > 0:
            np.savez_compressed(
                str(npz_path),
                matrix=self._matrix,
                uids=np.array(self._uids, dtype=str),
                paths=np.array(self._paths, dtype=str),
            )
        elif npz_path.exists():
            npz_path.unlink()

        meta = {
            "version": DEF_EMBED_VERSION,
            "model": FILE_EMBED_MODEL,
            "dim": FILE_EMBED_DIM,
            "def_count": len(self._uid_to_idx),
        }
        meta_path.write_text(json.dumps(meta, indent=2))
