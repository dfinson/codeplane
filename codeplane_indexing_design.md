# CodePlane Indexing & Retrieval Architecture (Lexical + Structural + Graph)

## Overview
CodePlane builds a deterministic, incrementally updated hybrid index with:
- A fast **lexical search engine** (Tantivy) for identifiers, paths, and tokens.
- A structured **metadata store** (SQLite) for symbols, spans, file hashes, and references.
- A dependency and symbol **graph** for bounded, explainable expansions.

All indexing is split across:
- A **shared, tracked index** (from Git-tracked files, CI-buildable).
- A **local overlay index** (for untracked or sensitive files, private to device).

No embeddings or background model reasoning is required. The system serves agent and CLI requests in <1s under normal load.

---

## Lexical Index
- **Engine:** Tantivy via PyO3 bindings.
- **Scope:** Paths, identifiers, docstrings (optional), with BM25 scoring.
- **Update model:** Immutable segment + delete+add on change.
- **Indexing throughput:** 5k–50k docs/sec depending on hardware.
- **Query latency:** <10ms on warm cache for top-K.
- **Incremental updates:** Based on Git blob hash and file content hash diff.
- **Atomicity:** Full index built in temp dir and swapped in (`os.replace()`), ensures read-safe transitions.

---

## Structural Metadata
- **Store:** SQLite, single-file, ACID, WAL mode.
- **Schema includes:**
  - `chunk_registry`: file/chunk id, blob hash, spans
  - `symbols`: name, kind, location, language
  - `relations`: edges between symbols (calls, imports, contains, inherits)
- **Concurrency:** Readers always non-blocking; writer blocked only during batch update (~10–100ms).
- **Consistency:** Metadata update is transactionally coupled to index revision swap.

---

## Parser (Tree-sitter)
- **Default parser:** Tree-sitter via Python bindings.
- **Languages:** 10+ bundled grammars (~10 MB total). Version-pinned to avoid drift.
- **Failure mode:** If grammar fails or file is unsupported, crash gracefully and skip.
- **No fallback to tokenization** — lexical index already handles fuzzy matching.
- **Not sufficient for cross-file refactors** (e.g. rename all uses) — requires LSP.

---

## LSP Support
- **Usage:** Only for semantic refactors (rename symbol, move module).
- **Integration strategy:**
  - Bundled Tree-sitter grammars.
  - Dynamic LSP binary install via setup wizard (opt-in).
  - Per-language cache under `~/.codeplane/lsp/` keyed by lang+version.
- **Invocation:** Async subprocesses (via JSON-RPC) per language; isolated and optional.

---

## Graph Index
- **Structure:**
  - Nodes = symbols
  - Edges = semantic links: calls, imports, inherits, contains
- **Schema:**
  - `relation(src_id, dst_id, type, weight)`
- **Traversal:**
  - Depth cap = 2–3
  - Fanout cap per node based on role (e.g. utility capped at 3, class at 10)
  - Deterministic order: lexicographic on symbol name
- **Purpose:**
  - Expand context for symbol search and rerank
  - Serve as input to refactor targets, reference resolution

---

## Indexing Mechanics
- **Change detection:**
  - Git blob hash + mtime for tracked files
  - Content hash for untracked files
- **Chunk granularity:**
  - Function/class-level when possible
  - Fallback to full file if necessary
- **Update triggers:**
  - On daemon start
  - Pre/post each MCP call
  - On detected repo state change
- **Deleted reference cleanup:**
  - On chunk deletion, remove all edges targeting the chunk
  - Update relation tables and affected symbols accordingly

---

## Atomic Update Protocol
- **All index writes go to a temp dir/db**
- On success:
  - `os.replace()` old `index/` and `meta.db` atomically
  - Optional backup of previous revision (e.g. `index.prev/`)
- **Performance target:** Full diff update (10–20 files) under 1–2s
- **Crash safety:** No intermediate state ever visible; recovery via Git + clean rebuild

---

## Startup Wizard & Extensibility

### 🔄 Dynamic Grammar Management (Update)

In addition to the initial setup (`codeplane init`), the system must also:
- **Detect new language usage** during incremental updates or file additions.
- **Dynamically download and register** grammars and/or LSPs as needed on-the-fly.
- Update grammar registry in the cache without requiring a full reinit.

This ensures language coverage tracks the evolving repository without user intervention or wheel bloat.

- First-time `codeplane init` prompts:
  - Detect dominant languages
  - Offer to download language-specific LSPs
  - Set preferences for grammar overrides, expansion heuristics
- Future: allow language packs via versioned plugin registry (no wheel bloat)
