# CodePlane Refactor Engine — Final Scope Spec

## ✅ Purpose

Implement a **deterministic, language-agnostic refactor engine** that handles symbol renames, file/module moves, and safe deletions across multi-language repositories. All planning is delegated to **persistent, statically configured LSP servers**. CodePlane executes the resulting edits atomically, tracks state precisely, and maintains correctness via reindexing.

## ✅ Core Operations

- `rename_symbol(from, to, at)`
- `rename_file(from_path, to_path)`
- `move_file(from_path, to_path)`
- `delete_symbol(at)`

All operations:
- Return **structured diff output** with `files_changed`, `edits`, `symbol`, `new_name`, etc.
- Provide **preview → apply → rollback** semantics
- Are **atomic** at the patching level
- Operate across **tracked and untracked (overlay) files**
- Apply LSP-driven semantics across **all languages**

## ✅ Architecture Overview

### LSP-Only Execution

- All refactor planning (rename, move, delete) is handled via LSP (`textDocument/rename`, `workspace/willRenameFiles`, etc.)
- No fallback to CodePlane index logic
- CodePlane maintains full control of edit application, version tracking, and reindexing

### Persistent LSP Daemons

- One subprocess per supported language
- Launched at daemon startup (`cpl up`) based on static config
- Not started dynamically
- Restart of daemon required to support new languages

### File State Virtualization

- CodePlane injects file contents into LSP via `didOpen` and `didChange`
- No LSP reads files directly from disk
- File versioning is maintained in memory by CodePlane

### Edit Application and Reindexing

- `WorkspaceEdit` results from LSP are transformed into structured diffs
- File edits are applied atomically
- All affected files are reindexed into lexical index, structural metadata, and symbol/reference graph
- Overlay/untracked files are updated as first-class citizens

## ✅ Git-Aware File Move Semantics

- If a file rename or move affects a Git-tracked file:
  - CodePlane will perform a `git mv`-equivalent operation
  - This updates Git’s index to reflect the move (preserving history)
  - Only performed if the file is clean and tracked
  - Fails safely if the working tree state is inconsistent (e.g. modified, unstaged)
- If the file is untracked or ignored (e.g. overlay files):
  - CodePlane performs a normal filesystem move only
- This ensures Git rename detection and downstream agent operations remain correct

Structured diff will reflect:
```json
{
  "file_moved": true,
  "from": "src/old_path.py",
  "to": "src/new_path.py",
  "git_mv": true
}
```

## ✅ Language Support Model

- **All languages use LSPs exclusively**
- Language support is statically declared at project init
- Unsupported languages cannot execute refactor operations
- No runtime auto-detection or fallback logic
- LSPs persist for the daemon’s lifecycle

## ❌ Out of Scope

- Git commits, staging, revert, or history manipulation
- Test execution or build validation
- Refactor logs beyond structured diff response
- Dynamic language inference (e.g., `eval`, `getattr`)
- Partial or speculative refactors
- Multi-symbol refactors

## ✅ Guarantees

- **Deterministic**: Same refactor input → same result
- **Isolated**: Edits are applied only to confirmed, LSP-authorized files
- **Audit-safe**: Git-aware moves preserve index correctness
- **Overlay-compatible**: Untracked files handled equally
- **Agent-delegated commit control**: CodePlane never stages or commits

## 📝 Comment and Markdown Reference Handling

- LSP-based renames **do not affect** comments, docstrings, or markdown files.
  - Examples of unaffected references:
    - `# MyClassA` (comment)
    - `"""Used in MyClassA."""` (docstring)
    - `README.md` references to `MyClassA`
- To maintain coherence, CodePlane performs a **post-refactor sweep**:
  - Searches for exact string matches of the original symbol name
  - Scans:
    - Comments in source code (from structural index)
    - Markdown and text files (README, docs, etc.)
    - Overlay files, if applicable
  - Generates a separate, deterministic patch set for these changes
  - Annotates these as **non-semantic edits**, separate from LSP edits
  - User or agent may preview, accept, or reject them

This ensures textual references to renamed symbols are coherently updated without being conflated with semantic LSP-backed mutations.

## ⚙️ Optional Refactor Engine Support

The deterministic LSP-backed refactor engine is **enabled by default**, but may be disabled via configuration or CLI for environments with limited resources.

### Why

- LSPs are persistent subprocesses and consume non-trivial memory per language
- On large, multi-language repos, total steady-state memory may exceed 2–4 GB
- Some users may prefer to delegate refactors to agents or external tools

### How

Disable in config:

```yaml
refactor:
  enabled: false
```

Or via CLI:

```bash
cpl up --no-refactor
```

When disabled:
- No refactor endpoints are exposed
- No LSP servers are launched
- CodePlane still provides indexing, search, and patch execution
