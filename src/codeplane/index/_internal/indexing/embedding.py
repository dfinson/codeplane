"""Dense vector index with evidence-record multiview architecture.

Peer subsystem of LexicalIndex (Tantivy) and StructuralIndexer (tree-sitter).
Uses fastembed (ONNX-based) for embedding computation and numpy for storage
and cosine similarity search.

Model: BAAI/bge-small-en-v1.5  (384-dim, 67 MB, 512-token context)
Architecture: Each definition produces 1–7 evidence records (NAME, DOC,
CTX_PATH, CTX_USAGE, LIT_HINTS, SEM_FACTS, BLOCK), each embedded independently.
Query retrieval uses ratio gate, per-record→per-uid aggregation, and
tiered acceptance rules.  See SPEC.md §16.

Lifecycle mirrors LexicalIndex:
  - stage_defs()    → accumulate defs for embedding
  - stage_remove()  → mark def_uids for removal
  - commit_staged() → compute embeddings + persist
  - reload()        → reload from disk
  - clear()         → wipe all embeddings

Storage: .codeplane/embedding/
  - embeddings.npz   (float16 matrix + kinds arrays)
  - metadata.json    (model name, dim, count, version, block_members)
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger()

# ===================================================================
# Constants (SPEC §16)
# ===================================================================

_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_EMBEDDING_DIM = 384
_MAX_TEXT_CHARS = 1500  # 512-token context window
_METADATA_VERSION = 3  # v3: evidence-record architecture
_EMBED_BATCH_SIZE = 256

# Evidence record kind constants
KIND_NAME = "NAME"
KIND_DOC = "DOC"
KIND_CTX_PATH = "CTX_PATH"
KIND_CTX_USAGE = "CTX_USAGE"
KIND_LIT_HINTS = "LIT_HINTS"
KIND_SEM_FACTS = "SEM_FACTS"
KIND_BLOCK = "BLOCK"

# Config block detection
_CONFIG_RATIO_THRESH = 0.80
_CONFIG_MIN_DEFS = 10
_BLOCK_BUDGET_CHARS = 400

# LIT_HINTS and DOC budgets
_LIT_HINTS_BUDGET = 120
_DOC_MAX_CHARS = 200

# SEM_FACTS rendering budget
_SEM_FACTS_BUDGET = 200
_SEM_FACTS_TOKEN_CAP = 30

# Frequency filtering
_FREQ_BASE = 0.05
_FREQ_CLAMP_LO = 0.02
_FREQ_CLAMP_HI = 0.15

# Query-time retrieval
RATIO_MIN = 1.10
K_DEFAULT = 50

# Word split regex: camelCase / PascalCase / snake_case → words
_CAMEL_SPLIT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[0-9]+")


# ===================================================================
# Helper functions
# ===================================================================


def _word_split(name: str) -> list[str]:
    """Split a definition name into lowercase natural words.

    Handles camelCase, PascalCase, snake_case, and mixed styles.
    Example: ``getUserById`` → ``["get", "user", "by", "id"]``
    """
    words: list[str] = []
    for part in name.split("_"):
        if not part:
            continue
        camel = _CAMEL_SPLIT.findall(part)
        if camel:
            words.extend(w.lower() for w in camel)
        else:
            words.append(part.lower())
    return words


def _path_to_phrase(file_path: str) -> str:
    """Convert a file path into a natural-language phrase.

    Example: ``src/auth/middleware/rate_limiter.py``
    → ``"auth middleware rate limiter"``
    """
    p = file_path.replace("\\", "/")
    for prefix in ("src/", "lib/", "app/", "pkg/", "internal/"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
            break
    dot = p.rfind(".")
    if dot > 0:
        p = p[:dot]
    parts: list[str] = []
    for segment in p.split("/"):
        parts.extend(_word_split(segment))
    return " ".join(parts)


def _render_sem_facts(
    sem_facts: dict[str, list[str]],
    word_df: dict[str, int],
    n_defs: int,
) -> str:
    """Render SEM_FACTS dict into English-structured tag string.

    Input: ``{"calls": ["getUserById", "validate"], "raises": ["ValueError"]}``
    Output: ``"calls get user by id validate raises value error"``

    Steps:
    1. Iterate categories in fixed order (calls, assigns, returns, raises, literals)
    2. For each raw identifier: word-split into tokens
    3. Drop high-frequency tokens (using corpus word_df)
    4. Deduplicate tokens within each category
    5. Cap total tokens to _SEM_FACTS_TOKEN_CAP
    6. Cap rendered string to _SEM_FACTS_BUDGET chars
    """
    from codeplane.index._internal.parsing._sem_queries import SEM_CATEGORY_ORDER

    threshold = _freq_threshold(n_defs)
    parts: list[str] = []
    total_tokens = 0

    for category in SEM_CATEGORY_ORDER:
        raw_items = sem_facts.get(category, [])
        if not raw_items:
            continue

        # Word-split all identifiers, drop high-freq, deduplicate
        cat_words: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            for w in _word_split(item):
                if w in seen:
                    continue
                # Drop corpus-high-frequency tokens
                if n_defs > 0 and word_df.get(w, 0) / max(n_defs, 1) > threshold:
                    continue
                seen.add(w)
                cat_words.append(w)
                total_tokens += 1
                if total_tokens >= _SEM_FACTS_TOKEN_CAP:
                    break
            if total_tokens >= _SEM_FACTS_TOKEN_CAP:
                break

        if cat_words:
            parts.append(category)
            parts.extend(cat_words)

        if total_tokens >= _SEM_FACTS_TOKEN_CAP:
            break

    text = " ".join(parts)
    return text[:_SEM_FACTS_BUDGET]


def _compute_word_frequencies(
    uid_to_def: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """Compute word-level document frequency across all def names.

    For each word w in word_split(name), df(w) = number of distinct
    defs whose name contains w.
    """
    word_df: dict[str, int] = defaultdict(int)
    for d in uid_to_def.values():
        name = d.get("name", "")
        seen_words = set(_word_split(name))
        for w in seen_words:
            word_df[w] += 1
    return dict(word_df)


def _freq_threshold(n_defs: int) -> float:
    """Compute repo-adaptive frequency threshold.

    Formula: ``0.05 * sqrt(N / 1000)``, clamped to [0.02, 0.15].
    """
    if n_defs <= 0:
        return _FREQ_CLAMP_HI
    raw = _FREQ_BASE * math.sqrt(n_defs / 1000)
    return max(_FREQ_CLAMP_LO, min(raw, _FREQ_CLAMP_HI))


def _is_name_frequency_filtered(
    name: str,
    word_df: dict[str, int],
    n_defs: int,
) -> bool:
    """Check if a def's NAME record should be suppressed.

    Suppressed when the name's most common word exceeds the
    repo-adaptive frequency threshold.
    """
    words = _word_split(name)
    if not words:
        return False
    max_df = max(word_df.get(w, 0) for w in words)
    threshold = _freq_threshold(n_defs)
    return max_df / max(n_defs, 1) > threshold


def _detect_config_file(defs: list[dict[str, Any]]) -> bool:
    """Check if a file is a config file based on def body sizes.

    A file is config if ≥80% of defs have body ≤ 3 lines and
    there are ≥ 10 total defs.
    """
    if len(defs) < _CONFIG_MIN_DEFS:
        return False
    small_body = sum(
        1
        for d in defs
        if (d.get("end_line", 0) - d.get("start_line", 0)) <= 3
    )
    return small_body / len(defs) >= _CONFIG_RATIO_THRESH


def _aggregate_config_blocks(
    defs: list[dict[str, Any]],
    file_path: str,
) -> tuple[list[tuple[str, str, str]], dict[str, list[str]]]:
    """Group config defs by name prefix into BLOCK records.

    Returns:
        (records, block_members) where records is list of
        (block_uid, KIND_BLOCK, text) and block_members maps
        block_uid → [original def_uids].
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in defs:
        words = _word_split(d.get("name", ""))
        prefix = words[0] if len(words) > 1 else "__no_prefix__"
        groups[prefix].append(d)

    misc: list[dict[str, Any]] = []
    final_groups: dict[str, list[dict[str, Any]]] = {}
    for prefix, members in groups.items():
        if len(members) < 3:
            misc.extend(members)
        else:
            final_groups[prefix] = members
    if misc:
        final_groups["__misc__"] = misc

    records: list[tuple[str, str, str]] = []
    block_members: dict[str, list[str]] = {}

    for prefix, members in final_groups.items():
        header = f"config block: {prefix}: "
        name_list = [d.get("name", "") for d in members]
        member_uids = [d.get("def_uid", "") for d in members]
        full_text = header + " ".join(name_list)

        if len(full_text) <= _BLOCK_BUDGET_CHARS:
            block_uid = f"__block__{file_path}::{prefix}"
            records.append((block_uid, KIND_BLOCK, full_text))
            block_members[block_uid] = member_uids
        else:
            remaining_budget = _BLOCK_BUDGET_CHARS - len(header)
            current_names: list[str] = []
            current_uids: list[str] = []
            chunk_idx = 0
            for name, uid in zip(name_list, member_uids, strict=True):
                prospective = " ".join([*current_names, name])
                if len(prospective) > remaining_budget and current_names:
                    block_uid = f"__block__{file_path}::{prefix}_{chunk_idx}"
                    records.append(
                        (block_uid, KIND_BLOCK, header + " ".join(current_names))
                    )
                    block_members[block_uid] = current_uids[:]
                    current_names = [name]
                    current_uids = [uid]
                    chunk_idx += 1
                else:
                    current_names.append(name)
                    current_uids.append(uid)
            if current_names:
                block_uid = f"__block__{file_path}::{prefix}_{chunk_idx}"
                records.append(
                    (block_uid, KIND_BLOCK, header + " ".join(current_names))
                )
                block_members[block_uid] = current_uids

    return records, block_members


def _detect_providers() -> list[str]:
    """Detect available ONNX Runtime execution providers."""
    try:
        import onnxruntime as ort  # type: ignore[import-not-found]

        available = set(ort.get_available_providers())
    except Exception:
        return []

    providers: list[str] = []
    if "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    providers.append("CPUExecutionProvider")
    return providers


# ===================================================================
# EmbeddingIndex
# ===================================================================


class EmbeddingIndex:
    """Dense vector index with evidence-record multiview architecture.

    Each definition produces multiple evidence records (NAME, DOC,
    CTX_PATH, CTX_USAGE, LIT_HINTS, SEM_FACTS, BLOCK).  Query retrieval
    uses ratio gate, per-record→per-uid aggregation, and tiered acceptance.

    Initialized in IndexCoordinator.__init__, participates in the same
    single-pass index/reindex cycle as LexicalIndex and StructuralIndexer.
    """

    def __init__(self, index_path: Path) -> None:
        """Create index.  Model loaded lazily on first commit_staged()."""
        self._index_path = Path(index_path)
        self._index_path.mkdir(parents=True, exist_ok=True)

        # In-memory state
        self._matrix: np.ndarray[Any, np.dtype[np.float16]] | None = None
        self._kinds: list[tuple[str, str]] = []  # (uid, kind) parallel to matrix rows
        self._block_members: dict[str, list[str]] = {}

        # Staging buffers
        self._staged_defs: list[dict[str, Any]] = []
        self._staged_removals: set[str] = set()

        # Lazy model handle
        self._model: Any | None = None
        self._disabled = False

    # ------------------------------------------------------------------
    # Staging API
    # ------------------------------------------------------------------

    def stage_defs(
        self,
        defs: list[dict[str, Any]],
        *,
        file_path: str = "",
    ) -> None:
        """Stage def dicts from ExtractionResult for embedding.

        Args:
            defs: Raw def dicts from ExtractionResult.defs.
                  Required keys: def_uid, name, kind.
                  Optional: docstring, _string_literals (from structural).
            file_path: Source file path (for CTX_PATH records).
        """
        for d in defs:
            d["_file_path"] = file_path
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
        usage_lookup: Callable[[str], list[str]] | None = None,
    ) -> int:
        """Compute embeddings for all staged defs and persist.

        Builds evidence records per def, applies frequency filtering
        and config block aggregation, then embeds all records.

        Args:
            on_progress: callback(embedded_so_far, total) for progress.
            usage_lookup: callback(def_uid) → [caller_def_names].

        Returns count of defs embedded in this commit.
        """
        if not self.has_staged_changes():
            return 0

        # Apply removals first
        if self._staged_removals and self._matrix is not None and self._kinds:
            removal_set = self._staged_removals
            keep_mask = np.array(
                [uid not in removal_set for uid, _kind in self._kinds],
                dtype=bool,
            )
            if not keep_mask.all():
                self._matrix = self._matrix[keep_mask]
                self._kinds = [
                    k for k, keep in zip(self._kinds, keep_mask, strict=True) if keep
                ]
                if not self._kinds:
                    self._matrix = None
            # Clean up block_members referencing removed uids
            for block_uid in list(self._block_members.keys()):
                members = self._block_members[block_uid]
                self._block_members[block_uid] = [
                    u for u in members if u not in removal_set
                ]
                if not self._block_members[block_uid]:
                    del self._block_members[block_uid]
        self._staged_removals.clear()

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

            # Remove existing records for these uids (re-index case)
            if self._matrix is not None and self._kinds:
                new_uid_set = set(uid_to_def.keys())
                keep_mask = np.array(
                    [uid not in new_uid_set for uid, _kind in self._kinds],
                    dtype=bool,
                )
                if not keep_mask.all():
                    self._matrix = self._matrix[keep_mask]
                    self._kinds = [
                        k
                        for k, keep in zip(self._kinds, keep_mask, strict=True)
                        if keep
                    ]
                    if not self._kinds:
                        self._matrix = None
                # Remove blocks whose members are being re-indexed
                for block_uid in list(self._block_members.keys()):
                    if any(u in new_uid_set for u in self._block_members[block_uid]):
                        block_keep = np.array(
                            [uid != block_uid for uid, _ in self._kinds],
                            dtype=bool,
                        )
                        if self._matrix is not None and not block_keep.all():
                            self._matrix = self._matrix[block_keep]
                            self._kinds = [
                                k
                                for k, keep in zip(
                                    self._kinds, block_keep, strict=True
                                )
                                if keep
                            ]
                            if not self._kinds:
                                self._matrix = None
                        del self._block_members[block_uid]

            # Build evidence records
            records = self._build_evidence_records(uid_to_def, usage_lookup)

            if not records:
                self._save()
                return 0

            texts = [text for _uid, _kind, text in records]
            record_kinds = [(uid, kind) for uid, kind, _text in records]

            # Batch embed
            assert self._model is not None
            total = len(texts)
            start = time.monotonic()
            embeddings_list: list[Any] = []
            for i, vec in enumerate(
                self._model.embed(texts, batch_size=_EMBED_BATCH_SIZE)
            ):
                embeddings_list.append(vec)
                if on_progress is not None and (i % 50 == 0 or i == total - 1):
                    on_progress(i + 1, total)
            elapsed = time.monotonic() - start
            log.info(
                "embedding.commit",
                defs=len(uid_to_def),
                records=total,
                elapsed_ms=round(elapsed * 1000),
            )

            # Convert to float16 L2-normed matrix
            new_matrix = np.array(embeddings_list, dtype=np.float32)
            norms = np.linalg.norm(new_matrix, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-10)
            new_matrix = new_matrix / norms
            new_matrix = new_matrix.astype(np.float16)

            # Append to existing matrix
            if self._matrix is not None and self._kinds:
                self._matrix = np.vstack([self._matrix, new_matrix])
            else:
                self._matrix = new_matrix
            self._kinds.extend(record_kinds)
            count = len(uid_to_def)

        self._save()
        return count

    def _build_evidence_records(
        self,
        uid_to_def: dict[str, dict[str, Any]],
        usage_lookup: Callable[[str], list[str]] | None = None,
    ) -> list[tuple[str, str, str]]:
        """Build evidence records for all staged defs.

        Returns list of (uid, kind, text) tuples ready for embedding.
        """
        n_defs = len(uid_to_def)
        word_df = _compute_word_frequencies(uid_to_def)
        records: list[tuple[str, str, str]] = []

        # Usage IDF corpus
        usage_df: dict[str, int] = defaultdict(int)
        usage_texts_raw: dict[str, list[str]] = {}
        if usage_lookup is not None:
            for uid in uid_to_def:
                caller_names = usage_lookup(uid)
                if caller_names:
                    usage_texts_raw[uid] = caller_names
                    for name in set(caller_names):
                        usage_df[name] += 1
        total_usage_records = len(usage_texts_raw)
        threshold = _freq_threshold(n_defs)

        # Group defs by file for config detection
        file_defs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for d in uid_to_def.values():
            fp = d.get("_file_path", "")
            file_defs[fp].append(d)

        # Detect config files → BLOCK records
        config_uids: set[str] = set()
        for fp, defs_in_file in file_defs.items():
            if _detect_config_file(defs_in_file):
                block_records, block_members = _aggregate_config_blocks(
                    defs_in_file, fp
                )
                records.extend(block_records)
                self._block_members.update(block_members)
                config_uids.update(d.get("def_uid", "") for d in defs_in_file)
                # Config defs still get CTX_PATH
                for d in defs_in_file:
                    uid = d.get("def_uid", "")
                    fp_val = d.get("_file_path", "")
                    if fp_val:
                        phrase = _path_to_phrase(fp_val)
                        if phrase:
                            records.append((uid, KIND_CTX_PATH, phrase))

        # Build records for non-config defs
        for uid, d in uid_to_def.items():
            if uid in config_uids:
                continue

            name = d.get("name", "")
            docstring = d.get("docstring") or ""

            # (a) NAME — unless frequency-filtered
            if not _is_name_frequency_filtered(name, word_df, n_defs):
                name_text = " ".join(_word_split(name))
                if name_text:
                    records.append((uid, KIND_NAME, name_text))

            # (b) DOC — if docstring present and > 10 chars
            doc_stripped = docstring.strip()
            has_doc = len(doc_stripped) > 10
            if has_doc:
                first_para = doc_stripped.split("\n\n")[0].strip()
                records.append(
                    (uid, KIND_DOC, first_para[:_DOC_MAX_CHARS])
                )

            # (c) CTX_PATH — always
            fp = d.get("_file_path", "")
            if fp:
                phrase = _path_to_phrase(fp)
                if phrase:
                    records.append((uid, KIND_CTX_PATH, phrase))

            # (d) CTX_USAGE — if refs exist, after usage-IDF filtering
            if uid in usage_texts_raw:
                raw_names = usage_texts_raw[uid]
                if total_usage_records > 0:
                    filtered = [
                        n
                        for n in raw_names
                        if usage_df.get(n, 0) / max(total_usage_records, 1)
                        <= threshold
                    ]
                else:
                    filtered = raw_names
                if filtered:
                    usage_words: list[str] = []
                    for n in filtered:
                        usage_words.extend(_word_split(n))
                    usage_text = " ".join(usage_words)
                    if usage_text:
                        records.append(
                            (uid, KIND_CTX_USAGE, usage_text[:_MAX_TEXT_CHARS])
                        )

            # (e) LIT_HINTS — only if DOC absent
            if not has_doc:
                lit_texts = d.get("_string_literals", [])
                if lit_texts:
                    lit_combined = " ".join(lit_texts)[:_LIT_HINTS_BUDGET]
                    if lit_combined.strip():
                        records.append((uid, KIND_LIT_HINTS, lit_combined))

            # (f) SEM_FACTS — structured tags from tree-sitter queries
            sem_facts: dict[str, list[str]] = d.get("_sem_facts", {})
            if sem_facts:
                sem_text = _render_sem_facts(sem_facts, word_df, n_defs)
                if sem_text:
                    records.append((uid, KIND_SEM_FACTS, sem_text))

        return records

    def discard_staged(self) -> int:
        """Discard staged changes.  Returns count discarded."""
        count = len(self._staged_defs) + len(self._staged_removals)
        self._staged_defs.clear()
        self._staged_removals.clear()
        return count

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def query(self, text: str, top_k: int = K_DEFAULT) -> list[tuple[str, float]]:
        """Cosine similarity search with single text.

        Wraps query_multiview with a single view.
        Returns [(def_uid, similarity), ...] sorted descending.
        """
        return self.query_multiview([text], top_k=top_k)

    def query_batch(
        self,
        texts: list[str],
        *,
        top_k: int = K_DEFAULT,
    ) -> list[list[tuple[str, float]]]:
        """Batch cosine similarity search — raw per-view results.

        Returns one ``[(def_uid, similarity)]`` list per input text.
        Aggregates records → uids by max score for backward compat.
        """
        if not texts:
            return []

        if self._matrix is None or not self._kinds:
            return [[] for _ in texts]

        if self._disabled:
            return [[] for _ in texts]

        self._ensure_model()
        if self._disabled:
            return [[] for _ in texts]

        assert self._model is not None
        raw_vecs = list(self._model.embed(texts))
        query_matrix = np.array(raw_vecs, dtype=np.float32)

        norms = np.linalg.norm(query_matrix, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        query_matrix = query_matrix / norms

        matrix_f32 = self._matrix.astype(np.float32)
        all_scores = query_matrix @ matrix_f32.T

        results: list[list[tuple[str, float]]] = []
        for row_idx in range(all_scores.shape[0]):
            scores = all_scores[row_idx]
            uid_best: dict[str, float] = {}
            for idx, (uid, _kind) in enumerate(self._kinds):
                s = float(scores[idx])
                if uid in self._block_members:
                    for member_uid in self._block_members[uid]:
                        if member_uid not in uid_best or s > uid_best[member_uid]:
                            uid_best[member_uid] = s
                else:
                    if uid not in uid_best or s > uid_best[uid]:
                        uid_best[uid] = s

            sorted_uids = sorted(uid_best.items(), key=lambda x: (-x[1], x[0]))
            results.append(sorted_uids[:top_k])

        return results

    def query_multiview(
        self,
        views: list[str],
        *,
        top_k: int = K_DEFAULT,
    ) -> list[tuple[str, float]]:
        """Multi-view query with distribution-aware retrieval.

        Implements the full evidence-record retrieval pipeline:
        1. Embed all views in one batch
        2. Per-view top-K retrieval
        3. Ratio gate (view quality filter)
        4. Per-record → per-uid aggregation
        5. Tiered acceptance rules (A/B/C/D)

        Returns [(def_uid, best_score)] sorted descending.
        """
        if not views:
            return []

        if self._matrix is None or not self._kinds:
            return []

        if self._disabled:
            return []

        self._ensure_model()
        if self._disabled:
            return []

        assert self._model is not None
        raw_vecs = list(self._model.embed(views))
        query_matrix = np.array(raw_vecs, dtype=np.float32)
        norms = np.linalg.norm(query_matrix, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        query_matrix = query_matrix / norms

        matrix_f32 = self._matrix.astype(np.float32)
        all_scores = query_matrix @ matrix_f32.T  # (n_views, n_records)

        n_records = all_scores.shape[1]
        k = min(top_k, n_records)
        if k == 0:
            return []

        # Phase 2+3: Per-view top-K + ratio gate
        valid_views: list[int] = []
        view_topk_scores: dict[int, np.ndarray] = {}
        view_topk_indices: dict[int, np.ndarray] = {}

        for v_idx in range(all_scores.shape[0]):
            scores_v = all_scores[v_idx]
            topk_idx = np.argpartition(scores_v, -k)[-k:]
            topk_sc = scores_v[topk_idx]
            sorted_order = np.argsort(topk_sc)[::-1]
            topk_idx = topk_idx[sorted_order]
            topk_sc = topk_sc[sorted_order]

            ratio = float(topk_sc[0]) / max(float(topk_sc[-1]), 1e-6)
            if ratio >= RATIO_MIN:
                valid_views.append(v_idx)
                view_topk_scores[v_idx] = topk_sc
                view_topk_indices[v_idx] = topk_idx

        if not valid_views:
            return []

        # Phase 4: Per-record → per-uid aggregation
        uid_kind_scores: dict[str, dict[str, float]] = defaultdict(dict)

        for v_idx in valid_views:
            topk_idx = view_topk_indices[v_idx]
            scores_v = all_scores[v_idx]
            for idx in topk_idx:
                uid, kind = self._kinds[int(idx)]
                score = float(scores_v[int(idx)])
                if (
                    kind not in uid_kind_scores[uid]
                    or score > uid_kind_scores[uid][kind]
                ):
                    uid_kind_scores[uid][kind] = score

        # Phase 5: strong_cutoff + best view stats
        best_view_idx = max(
            valid_views,
            key=lambda v: float(view_topk_scores[v][0])
            / max(float(view_topk_scores[v][-1]), 1e-6),
        )
        best_topk = view_topk_scores[best_view_idx]

        median_score = float(np.median(best_topk))
        p75_score = float(np.percentile(best_topk, 75))

        # Per-view strong_cutoff for rule (a)
        view_strong_cutoff: dict[int, float] = {}
        for v_idx in valid_views:
            topk_sc = view_topk_scores[v_idx]
            k_v = len(topk_sc)
            strong_index = max(0, min(math.floor(0.10 * k_v) - 1, k_v - 1))
            view_strong_cutoff[v_idx] = float(topk_sc[strong_index])

        # Find Tier A records passing rule (a) per-view
        tier_a_passed: set[str] = set()
        for v_idx in valid_views:
            cutoff_v = view_strong_cutoff[v_idx]
            scores_v = all_scores[v_idx]
            topk_idx = view_topk_indices[v_idx]
            for idx in topk_idx:
                uid, kind = self._kinds[int(idx)]
                if kind in (KIND_DOC, KIND_BLOCK) and float(scores_v[int(idx)]) >= cutoff_v:
                    tier_a_passed.add(uid)

        # Phase 6: Tiered acceptance
        accepted: list[tuple[str, float]] = []

        for uid, kind_scores in uid_kind_scores.items():
            best_score = max(kind_scores.values())

            has_doc_or_block = KIND_DOC in kind_scores or KIND_BLOCK in kind_scores
            has_name = KIND_NAME in kind_scores
            has_ctx = (
                KIND_CTX_PATH in kind_scores
                or KIND_CTX_USAGE in kind_scores
                or KIND_SEM_FACTS in kind_scores
            )
            only_lit = set(kind_scores.keys()) == {KIND_LIT_HINTS}

            # Rule (a): Tier A with strong_cutoff
            if has_doc_or_block and uid in tier_a_passed:
                accepted.append((uid, best_score))
                continue

            # Rule (b): NAME + context, best_score >= median
            if has_name and has_ctx and best_score >= median_score:
                accepted.append((uid, best_score))
                continue

            # Tier A that didn't pass cutoff can still pass via rule (b)
            if has_doc_or_block and has_name and has_ctx and best_score >= median_score:
                accepted.append((uid, best_score))
                continue

            # Rule (c): NAME or CTX_PATH alone, best_score >= P75
            if (has_name or KIND_CTX_PATH in kind_scores) and not only_lit and best_score >= p75_score:
                accepted.append((uid, best_score))
                continue

            # Rule (d): LIT_HINTS alone → reject

        # Phase 7: Expand blocks and deduplicate
        final: list[tuple[str, float]] = []
        for uid, score in accepted:
            if uid in self._block_members:
                for member_uid in self._block_members[uid]:
                    final.append((member_uid, score))
            else:
                final.append((uid, score))

        seen: set[str] = set()
        result: list[tuple[str, float]] = []
        for uid, score in sorted(final, key=lambda x: (-x[1], x[0])):
            if uid not in seen:
                seen.add(uid)
                result.append((uid, score))

        return result

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
            self._matrix = data["matrix"]

            # Load kinds (v3 format: separate uid and kind arrays)
            if "kinds_uids" in data and "kinds_kinds" in data:
                kinds_uids = data["kinds_uids"]
                kinds_kinds = data["kinds_kinds"]
                self._kinds = [
                    (
                        s.decode("utf-8") if isinstance(s, bytes) else str(s),
                        k.decode("utf-8") if isinstance(k, bytes) else str(k),
                    )
                    for s, k in zip(kinds_uids, kinds_kinds, strict=True)
                ]
            else:
                # Legacy v2 format: uids array, one record per uid
                uids_raw = data["uids"]
                self._kinds = [
                    (
                        s.decode("utf-8") if isinstance(s, bytes) else str(s),
                        KIND_NAME,
                    )
                    for s in uids_raw
                ]

            self._block_members = meta.get("block_members", {})

            log.info(
                "embedding.loaded",
                records=len(self._kinds),
                unique_defs=self.count,
                dim=self._matrix.shape[1] if self._matrix is not None else 0,
            )
            return True
        except Exception:
            log.warning("embedding.load_failed", exc_info=True)
            self._matrix = None
            self._kinds = []
            self._block_members = {}
            return False

    def reload(self) -> None:
        """Reload embeddings from disk."""
        self.load()

    def clear(self) -> None:
        """Clear all embeddings (in-memory and on disk)."""
        self._matrix = None
        self._kinds = []
        self._block_members = {}
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
        """Number of unique embedded definitions (not records)."""
        if not self._kinds:
            return 0
        unique_uids = {uid for uid, _kind in self._kinds}
        for block_uid, members in self._block_members.items():
            if block_uid in unique_uids:
                unique_uids.discard(block_uid)
                unique_uids.update(members)
        return len(unique_uids)

    @property
    def record_count(self) -> int:
        """Number of evidence records (rows in matrix)."""
        return len(self._kinds)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_model(self) -> None:
        """Lazy-load fastembed TextEmbedding model with GPU auto-detect."""
        if self._model is not None or self._disabled:
            return

        try:
            from fastembed import TextEmbedding  # type: ignore[import-not-found]

            providers = _detect_providers()
            threads = max(1, (os.cpu_count() or 4) // 2)
            start = time.monotonic()
            kwargs: dict[str, Any] = {
                "model_name": _MODEL_NAME,
                "threads": threads,
            }
            if providers:
                kwargs["providers"] = providers
            self._model = TextEmbedding(**kwargs)
            elapsed = time.monotonic() - start
            log.info(
                "embedding.model_loaded",
                model=_MODEL_NAME,
                providers=providers or ["CPUExecutionProvider"],
                threads=threads,
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
        """Build embedding input from a def dict (legacy compat).

        Format: "{kind} {qualified_name}\\n{signature_text}\\n{docstring}"
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
        """Persist to disk as compressed numpy arrays + JSON metadata."""
        npz_path = self._index_path / "embeddings.npz"
        meta_path = self._index_path / "metadata.json"

        if self._matrix is None or not self._kinds:
            if npz_path.exists():
                npz_path.unlink()
            if meta_path.exists():
                meta_path.unlink()
            return

        kinds_uids = np.array([uid for uid, _ in self._kinds], dtype="U")
        kinds_kinds = np.array([kind for _, kind in self._kinds], dtype="U")
        np.savez_compressed(
            npz_path,
            matrix=self._matrix,
            kinds_uids=kinds_uids,
            kinds_kinds=kinds_kinds,
        )

        meta = {
            "version": _METADATA_VERSION,
            "model": _MODEL_NAME,
            "dim": _EMBEDDING_DIM,
            "record_count": len(self._kinds),
            "unique_defs": self.count,
            "block_members": self._block_members,
        }
        with meta_path.open("w") as f:
            json.dump(meta, f, indent=2)
