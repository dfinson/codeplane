#!/usr/bin/env python3
"""Benchmark: scaffold-only vs whole-file embedding quality + speed.

Compares cosine similarity for natural-language queries against:
  1. Scaffold-only text (anglicified structural skeleton)
  2. Full file content (raw source code)

Uses real files from this repo with hand-crafted scaffold approximations.
"""

from __future__ import annotations

import time
import numpy as np

# ── Model setup ──────────────────────────────────────────────────────
MODEL = "jinaai/jina-embeddings-v2-base-code"
MAX_LENGTH = 512  # our production setting


def load_model():
    from fastembed import TextEmbedding
    t0 = time.monotonic()
    m = TextEmbedding(model_name=MODEL, max_length=MAX_LENGTH)
    print(f"Model loaded in {time.monotonic()-t0:.1f}s  (max_length={MAX_LENGTH})")
    return m


def embed(model, texts: list[str]) -> np.ndarray:
    vecs = list(model.embed(texts, batch_size=len(texts)))
    mat = np.array(vecs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms > 0, norms, 1.0)
    mat /= norms
    return mat


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b)


# ── Test data: scaffold vs full content ──────────────────────────────
# Using real files from this repo

FILES = {
    "file_embedding.py": {
        "scaffold": """\
FILE_SCAFFOLD
module index internal indexing file embedding
imports numpy, structlog, fastembed, json, pathlib, gc
defines class FileEmbeddingIndex, function build_file_scaffold(file_path, defs, imports), \
function _word_split(name), function _path_to_phrase(file_path), \
function _build_embed_text(scaffold, content, defs), \
function _truncate_semantic(text, max_chars, defs), \
function _detect_providers, function _detect_batch_size, \
method stage_file(path, content, defs, imports), \
method commit_staged(on_progress), method query(text, top_k), \
method load, method clear, method _ensure_model, method _embed_batch(texts, on_progress)
describes file embedding index: File-level dense vector index
describes build file scaffold: Build an anglicified scaffold from tree-sitter extraction data
describes word split: Split an identifier into lowercase natural words""",

        "full": open("src/codeplane/index/_internal/indexing/file_embedding.py").read()[:4000],
    },

    "config/loader.py": {
        "scaffold": """\
FILE_SCAFFOLD
module config loader
imports pathlib, toml, json, structlog, pydantic
defines class ConfigLoader, function load_config(path), function find_config_file(start_dir), \
function merge_configs(base, override), method validate, method get_tool_config(tool_name)
describes config loader: Load and validate CodePlane configuration from pyproject.toml or codeplane.toml
describes load config: Load config from the given path, searching parent directories if needed""",

        "full": open("src/codeplane/config/loader.py").read()[:4000],
    },

    "git/ops.py": {
        "scaffold": """\
FILE_SCAFFOLD
module git ops
imports subprocess, pathlib, structlog, dataclasses
defines class GitOps, function run_git(args, cwd), function get_repo_root(path), \
function get_current_branch(cwd), function get_changed_files(cwd, base), \
function get_file_status(cwd), function stage_files(cwd, paths), \
function commit(cwd, message), function push(cwd, remote, branch), \
method diff_stat(base), method is_clean
describes git ops: High-level git operations for CodePlane daemon
describes run git: Run a git subprocess and return stdout""",

        "full": open("src/codeplane/git/ops.py").read()[:4000],
    },
}

QUERIES = [
    "embedding model for code search",
    "load configuration from toml file",
    "git commit and push changes",
    "rate limiting middleware",
    "tree-sitter extract definitions and imports",
    "cosine similarity vector search",
    "find all Python files in directory",
    "batch processing with progress callback",
]

# ── Run benchmark ────────────────────────────────────────────────────

def main():
    model = load_model()

    # Embed all scaffolds and full contents
    file_names = list(FILES.keys())
    scaffolds = [FILES[f]["scaffold"] for f in file_names]
    fulls = [FILES[f]["full"] for f in file_names]

    print(f"\n{'='*70}")
    print(f"{'File':<25} {'Scaffold chars':>15} {'Full chars':>12}")
    print(f"{'-'*70}")
    for name in file_names:
        sc = len(FILES[name]["scaffold"])
        fc = len(FILES[name]["full"])
        print(f"{name:<25} {sc:>15,} {fc:>12,}")

    # Time scaffold embedding
    t0 = time.monotonic()
    scaffold_vecs = embed(model, scaffolds)
    scaffold_time = time.monotonic() - t0

    # Time full-content embedding
    t0 = time.monotonic()
    full_vecs = embed(model, fulls)
    full_time = time.monotonic() - t0

    print(f"\nEmbed time — scaffold: {scaffold_time*1000:.0f}ms | full: {full_time*1000:.0f}ms")
    print(f"Speedup: {full_time/scaffold_time:.1f}x faster with scaffold")

    # Embed queries
    query_vecs = embed(model, QUERIES)

    # Compare
    print(f"\n{'='*70}")
    print(f"{'Query':<40} {'File':<22} {'Scaffold':>9} {'Full':>9} {'Δ':>7}")
    print(f"{'-'*70}")

    scaffold_wins = 0
    full_wins = 0
    total_comparisons = 0

    for qi, query in enumerate(QUERIES):
        q_vec = query_vecs[qi]
        for fi, fname in enumerate(file_names):
            s_sim = cosine(q_vec, scaffold_vecs[fi])
            f_sim = cosine(q_vec, full_vecs[fi])
            delta = s_sim - f_sim
            marker = "  ✓" if abs(delta) < 0.05 else (" S+" if delta > 0 else " F+")

            if s_sim > f_sim:
                scaffold_wins += 1
            elif f_sim > s_sim:
                full_wins += 1
            total_comparisons += 1

            print(f"{query[:39]:<40} {fname:<22} {s_sim:>8.3f} {f_sim:>8.3f} {delta:>+6.3f}{marker}")
        print()

    print(f"{'='*70}")
    print(f"Summary: scaffold wins {scaffold_wins}/{total_comparisons}, "
          f"full wins {full_wins}/{total_comparisons}")
    print(f"Embed speed: scaffold {scaffold_time*1000:.0f}ms vs full {full_time*1000:.0f}ms "
          f"({full_time/scaffold_time:.1f}x)")


if __name__ == "__main__":
    main()
