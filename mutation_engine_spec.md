## CodePlane Mutation Engine Scope Specification

### Design Objectives
- Never leave the repo in a partial, corrupt, or indeterminate state.
- Always apply mutations atomically, or not at all.
- Permit concurrent mutations only when edits are disjoint.
- Maintain a clean separation between file mutations and Git state (except for rename tracking).
- Ensure predictable cross-platform behavior (line endings, permissions, fsync).
- Always emit a structured delta that reflects the full effect of the mutation.

---

### Apply Protocol
- All edits are planned externally (LSP or reducer).
- All file edits are staged in memory or temp files.
- Each target file is exclusively locked prior to apply.
- Contents are replaced wholesale via `os.replace()` (POSIX) or `ReplaceFile()` (Windows).
- `fsync()` is called on both the new file and the parent directory to ensure durability.
- CRLF is normalized to LF during planning; re-encoded on write to preserve original form.
- No in-place edits are performed.

### Concurrency Model
- Thread pool executor applies independent files in parallel.
- Thread count should default to number of vcores.
- Final file write + rename is serialized per file.
- Precondition check (hash or mtime+size) must pass before apply; otherwise abort.
- Overlapping file mutations must be detected and blocked.

### Scope Enforcement
- All file edits must fall within an explicit working set or allowlist.
- Files listed in `.cplignore` are categorically excluded.
- Git-ignored files (e.g. `.env`) are editable, but flagged for agent confirmation.
- New file paths created under an allowed directory are accepted.
- Mutations that touch unscoped paths are rejected pre-apply.

### Structured Delta Format
Every mutation returns a full structured JSON delta. Required fields:

Per-file:
- `path`: relative path
- `oldHash`: pre-edit SHA256
- `newHash`: post-edit SHA256
- `lineEnding`: LF | CRLF
- `edits`: array of `{ range: {start: {line, char}, end: {line, char}}, newText, semantic, symbol? }`

Global:
- `mutationId`: UUID or agent-generated key
- `repoFingerprint`: hash of full file state
- `symbolsChanged`: optional list of semantic symbols affected
- `testsAffected`: optional list of test names

### Failure and Rollback
- Any failure during write, rename, or precondition check aborts the batch.
- All temp files are deleted.
- File locks are released.
- The repo is left in its original state.
- No Git commands are run as part of rollback.

### Git Behavior
- `git mv` is the only allowed Git mutation, and only for clean, tracked files.
- Git index, HEAD, or refs are never modified.
- No Git status, reset, merge, or stash operations are triggered.

### LSP and Edit Planning
- All semantic refactors are sourced from LSP (`textDocument/rename`, etc.).
- No fallback to internal symbol index for semantic edit planning.
- Structured reducers (non-LSP) must output in the same enriched schema.
- All edits (regardless of origin) must conform to a unified diff format.

### Performance Constraints
- Full-batch application of ~20 files should complete in <1s on a modern SSD.
- Pre-write prep (diff, temp staging) is parallelized.
- Final apply (rename+fsync) is serialized and lock-guarded.
- No assumption of in-place edit savings.

### Out of Scope
- No Git commits, staging, reset, stash, or merge.
- No recovery using Git state.
- No in-place edits or patch files.
- No speculative edits or partial semantic ops.

---

This spec defines the full boundary and behavioral contract for CodePlane's mutation engine, aligned with its deterministic execution model and agent-mediated architecture.

