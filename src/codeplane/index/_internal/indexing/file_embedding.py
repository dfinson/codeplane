"""File-level embedding index using Jina v2 base with anglicified scaffold.

Each file produces one embedding record.  The embedded text is::

    FILE_SCAFFOLD
    <anglicified scaffold from tree-sitter extraction>

    FILE_CHUNK
    <file content (head+tail truncated if needed)>

The scaffold converts tree-sitter-extracted defs and imports into
English-like tokens (identifier splitting, signature compaction)
so that natural-language queries match code structure.  No
language-specific keyword lists — purely mechanical extraction.

Storage: .codeplane/file_embedding/
  - file_embeddings.npz   (float16 matrix + path arrays)
  - file_meta.json         (model name, dim, count, version)

Lifecycle:
  - stage_file(path, content, defs, imports) → queue for embedding
  - stage_remove(paths)                      → mark for removal
  - commit_staged()                          → compute embeddings + persist
  - reload() / load()                        → reload from disk
  - clear()                                  → wipe
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
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
FILE_EMBED_VERSION = 2  # v2: scaffold prefix
FILE_EMBED_SUBDIR = "file_embedding"

# Word split regex: camelCase / PascalCase / snake_case → words
_CAMEL_SPLIT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[0-9]+")


# ===================================================================
# Scaffold helpers (anglicification from tree-sitter extraction)
# ===================================================================


def _word_split(name: str) -> list[str]:
    """Split an identifier into lowercase natural words.

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
            p = p[len(prefix):]
            break
    dot = p.rfind(".")
    if dot > 0:
        p = p[:dot]
    parts: list[str] = []
    for segment in p.split("/"):
        parts.extend(_word_split(segment))
    return " ".join(parts)


def build_file_scaffold(
    file_path: str,
    defs: list[dict[str, Any]],
    imports: list[dict[str, Any]],
) -> str:
    """Build an anglicified scaffold from tree-sitter extraction data.

    Converts structural facts (defs, imports) into English-like tokens
    that bridge the gap between natural-language queries and code.
    Language-agnostic: uses only identifier splitting and structural
    metadata from tree-sitter, no per-language keyword lists.

    Returns empty string if no meaningful scaffold can be built.

    Example output::

        FILE_SCAFFOLD
        module auth middleware rate limiter
        imports os, logging, base handler, rate config
        defines class RateLimiter, function check_rate(request, limit),
          method reset(self)
    """
    lines: list[str] = []

    # Module line from file path
    path_phrase = _path_to_phrase(file_path)
    if path_phrase:
        lines.append(f"module {path_phrase}")

    # Imports line
    if imports:
        import_tokens: list[str] = []
        for imp in imports:
            name = imp.get("imported_name", "") or ""
            source = imp.get("source_literal", "") or imp.get("module_path", "") or ""
            if source:
                import_tokens.append(" ".join(_word_split(source.split(".")[-1])))
            elif name:
                import_tokens.append(" ".join(_word_split(name)))
        # Deduplicate preserving order
        seen: set[str] = set()
        unique_imports: list[str] = []
        for tok in import_tokens:
            if tok and tok not in seen:
                seen.add(tok)
                unique_imports.append(tok)
        if unique_imports:
            lines.append(f"imports {', '.join(unique_imports)}")

    # Defines line from defs (tree-sitter extraction)
    if defs:
        kind_order = {
            "class": 0, "interface": 0, "struct": 0, "enum": 1,
            "function": 2, "method": 3, "variable": 4,
        }
        sorted_defs = sorted(
            defs, key=lambda d: kind_order.get(d.get("kind", ""), 5)
        )

        class_names: list[str] = []
        method_parts: list[str] = []
        func_parts: list[str] = []

        for d in sorted_defs:
            kind = d.get("kind", "")
            name = d.get("name", "")
            if not name:
                continue
            sig = d.get("signature_text", "") or ""

            if kind in ("class", "interface", "struct", "enum"):
                words = " ".join(_word_split(name))
                class_names.append(f"{kind} {words}")
            elif kind == "function":
                compact = _compact_sig(name, sig)
                func_parts.append(compact)
            elif kind == "method":
                compact = _compact_sig(name, sig)
                method_parts.append(compact)

        define_tokens: list[str] = []
        define_tokens.extend(class_names)
        define_tokens.extend(func_parts)
        define_tokens.extend(method_parts)

        if define_tokens:
            lines.append(f"defines {', '.join(define_tokens)}")

        # Docstring hints: first meaningful class or function docstring
        for d in sorted_defs:
            doc = (d.get("docstring") or "").strip()
            if doc and len(doc) > 15:
                first_sentence = doc.split(".")[0].strip() if "." in doc else doc[:80]
                if first_sentence:
                    lines.append(f"describes {first_sentence}")
                    break

    if not lines:
        return ""

    return "\n".join(lines)


def _compact_sig(name: str, sig: str) -> str:
    """Build a compact anglicified signature for a def.

    Strips ``self`` and returns e.g. ``"check rate(request, limit)"``.
    """
    words = " ".join(_word_split(name))
    if sig:
        compact = (
            sig.replace("self, ", "")
            .replace("self,", "")
            .replace("self", "")
        )
        if compact and compact != "()":
            return f"{words}{compact}"
    return words


def _build_embed_text(
    scaffold: str,
    content: str,
    defs: list[dict[str, Any]] | None = None,
) -> str:
    """Compose the final embed text: scaffold prefix + file content.

    Format::

        FILE_SCAFFOLD
        <scaffold lines>

        FILE_CHUNK
        <file content>

    Scaffold is preserved in full.  If the combined text exceeds the
    model's context window, content is truncated at semantic boundaries
    (def spans from tree-sitter) rather than at arbitrary character
    positions.
    """
    # Compute actual structural overhead (no magic numbers)
    scaffold_block = f"FILE_SCAFFOLD\n{scaffold}\n\n" if scaffold else ""
    chunk_header = "FILE_CHUNK\n"
    overhead = len(scaffold_block) + len(chunk_header)

    content_budget = FILE_EMBED_MAX_CHARS - overhead
    truncated = _truncate_semantic(content, max_chars=content_budget, defs=defs)

    return f"{scaffold_block}{chunk_header}{truncated}"


# ===================================================================
# Semantic truncation
# ===================================================================


def _truncate_semantic(
    text: str,
    max_chars: int,
    defs: list[dict[str, Any]] | None = None,
) -> str:
    """Truncate content at semantic boundaries.

    When defs (tree-sitter spans) are available, keeps complete
    definitions from the start of the file until the budget is
    exhausted.  Falls back to line-boundary splitting when no
    structural data is available.

    The budget comes from the model's context window minus scaffold
    overhead — it is NOT an arbitrary constant.
    """
    if len(text) <= max_chars:
        return text

    lines = text.split("\n")

    if defs:
        # Use def end_lines to find the last complete semantic unit
        # that fits within budget.  Defs are sorted by position so
        # we greedily include from the top of the file.
        end_lines = sorted(
            {d.get("end_line", 0) for d in defs if d.get("end_line", 0) > 0}
        )
        last_included = 0
        for end_line in end_lines:
            # end_line is 1-indexed; slice is 0-indexed
            candidate = "\n".join(lines[:end_line])
            if len(candidate) <= max_chars:
                last_included = end_line
            else:
                break

        if last_included > 0:
            included = "\n".join(lines[:last_included])
            omitted = len(lines) - last_included
            if omitted > 0:
                included += f"\n\n... ({omitted} lines omitted)"
            return included

    # Fallback: split at last line boundary within budget
    char_count = 0
    split_line = 0
    for i, line in enumerate(lines):
        added = len(line) + (1 if i > 0 else 0)  # +1 for newline separator
        if char_count + added > max_chars:
            break
        char_count += added
        split_line = i + 1

    if split_line > 0:
        included = "\n".join(lines[:split_line])
        omitted = len(lines) - split_line
        if omitted > 0:
            included += f"\n\n... ({omitted} lines omitted)"
        return included

    # Absolute fallback for single very long lines
    return text[:max_chars]


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

    def stage_file(
        self,
        path: str,
        content: str,
        defs: list[dict[str, Any]] | None = None,
        imports: list[dict[str, Any]] | None = None,
    ) -> None:
        """Stage a file for embedding with anglicified scaffold prefix.

        Args:
            path: Relative file path.
            content: Full UTF-8 file content.
            defs: Tree-sitter extracted definitions (from ExtractionResult).
            imports: Tree-sitter extracted imports (from ExtractionResult).

        The scaffold is built from defs/imports and prepended to the
        content before embedding.  If defs/imports are not provided
        the content is embedded as-is (graceful degradation).
        """
        scaffold = ""
        if defs or imports:
            scaffold = build_file_scaffold(path, defs or [], imports or [])

        embed_text = _build_embed_text(scaffold, content, defs=defs)
        self._staged_files[path] = embed_text

    def stage_remove(self, paths: list[str]) -> None:
        """Mark file paths for removal from the index."""
        self._staged_removals.update(paths)

    def has_staged_changes(self) -> bool:
        """Return True if there are pending changes."""
        return bool(self._staged_files) or bool(self._staged_removals)

    # --- Commit ---

    def commit_staged(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Compute embeddings for staged files and persist.

        Args:
            on_progress: Optional callback(embedded_so_far, total_to_embed)
                called after each batch during embedding computation.

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
            # staged_files already contain composed embed text
            # (scaffold + truncated content) from stage_file()
            texts = [self._staged_files[p] for p in paths_to_embed]

            self._ensure_model()
            vectors = self._embed_batch(texts, on_progress=on_progress)

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
        """Embed a batch of texts, return L2-normalized float16 matrix."""
        if not texts:
            return np.empty((0, FILE_EMBED_DIM), dtype=np.float16)

        total = len(texts)
        all_vecs: list[np.ndarray] = []
        for i in range(0, total, FILE_EMBED_BATCH_SIZE):
            batch = texts[i : i + FILE_EMBED_BATCH_SIZE]
            vecs = list(self._model.embed(batch, batch_size=len(batch)))
            all_vecs.extend(vecs)
            if on_progress is not None:
                on_progress(min(i + len(batch), total), total)

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
