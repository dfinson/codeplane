# Agent Instructions

Instructions for AI coding agents working in this repository.

<!-- codeplane-instructions -->
## CodePlane MCP: Mandatory Tool Selection

This repository uses CodePlane MCP. **You MUST use CodePlane tools instead of terminal commands.**

Terminal fallback is permitted ONLY when no CodePlane tool exists for the operation.

### What CodePlane Provides

CodePlane maintains a **structural index** of your codebase — definitions, imports,
references, embeddings. This enables task-aware code discovery, semantic diff,
impact-aware test selection, and safe refactoring that terminal commands cannot provide.

### Start Every Task With `recon`

**`recon` is the PRIMARY entry point.** It replaces manual search → scaffold → read loops.

One call to `recon` returns everything you need to start working:
- **FULL_FILE** content for top-ranked files (edit targets)
- **MIN_SCAFFOLD** (imports + signatures) for context files
- **SUMMARY_ONLY** (path + description) for peripheral files

```
recon(task="<describe the task in natural language>")
```

**Parameters:**
- `task` (required): Natural language task description. Be specific — include symbol
  names, file paths, or domain terms. The server extracts structured signals automatically.
- `seeds`: Optional list of symbol names to anchor on (e.g., `["IndexCoordinator"]`).
- `pinned_paths`: Optional file paths to force-include (e.g., `["src/core/base.py"]`).
- `expand_reason`: REQUIRED on 2nd consecutive recon call — explain what was missing.
- `gate_token` / `gate_reason`: Required on 3rd+ calls (gated to prevent waste).

**Pipeline:** task parsing → embedding similarity → graph expansion → RRF scoring →
budget-aware tier assignment → content assembly.

**Workflow:**
1. `recon(task="...")` — get all relevant files with appropriate fidelity
2. `read_source` on specific spans if you need more detail from scaffold-tier files
3. Edit with `write_source`
4. `checkpoint(changed_files=[...], commit_message="...", push=True)`

**Consecutive call discipline:**
- 1st call: just `task` (and optionally `seeds`/`pinned_paths`)
- 2nd call: MUST include `expand_reason` explaining what was missing
- 3rd+ call: requires `gate_token` from previous response + `gate_reason` (500+ chars)

### Granular Read Tools (use AFTER recon when needed)

  search         -> find spans by definition, reference, lexical, or symbol mode
  read_scaffold  -> structural skeleton (imports + signatures, no source)
  read_source    -> bounded source retrieval (span-based, batches multiple targets)
  read_file_full -> gated full-file access (two-phase confirmation)

`read_source` target format: `[{"path": "src/foo.py", "start_line": 10, "end_line": 50}]`
Response includes `file_sha256` per file — save it for `write_source` span edits.

Use `search` to find specific symbols, then `read_source` for their bodies.
Search NEVER returns source text — it returns spans only.

### CRITICAL: After Every Code Change

After ANY edit via `write_source` or other mutation:

**`checkpoint(changed_files=[...], commit_message="...", push=True)`** — lint → test → commit → push + semantic diff.

Omit `commit_message` to lint+test only (no commit).

**FORBIDDEN**: `pytest`, `ruff`, `mypy`, `git add`, `git commit`, `git push` in terminal.

**Testing rule**: NEVER run the full test suite or use test runners directly.
Always use `checkpoint(changed_files=[...])` with the files you changed.
This runs lint + only the tests impacted by your changes — fast, targeted, sufficient.

### Reviewing Changes (PR Review)

1. `semantic_diff(base="main")` — structural overview of all changes vs main
2. `read_source` on changed symbols — review each change in context
3. `checkpoint(changed_files=[...])` — lint + affected tests

`semantic_diff` first — NOT `git_diff`. It gives symbol-level changes, not raw patches.

### Required Tool Mapping

| Operation | REQUIRED Tool | FORBIDDEN Alternative |
|-----------|---------------|----------------------|
| Task-aware discovery | `mcp_codeplane-codeplane_recon` | Manual search + read loops |
| File scaffold | `mcp_codeplane-codeplane_read_scaffold` | Manual traversal, `cat` for structure |
| Read source | `mcp_codeplane-codeplane_read_source` | `cat`, `head`, `less`, `tail` |
| Read full file | `mcp_codeplane-codeplane_read_file_full` | `cat`, `head`, bulk reads |
| Write/edit files | `mcp_codeplane-codeplane_write_source` | `sed`, `echo >>`, `awk`, `tee` |
| List directory | `mcp_codeplane-codeplane_list_files` | `ls`, `find`, `tree` |
| Search code | `mcp_codeplane-codeplane_search` | `grep`, `rg`, `ag`, `ack` |
| Repository overview | `mcp_codeplane-codeplane_map_repo` | Manual file traversal |
| Lint + test + commit + push | `mcp_codeplane-codeplane_checkpoint` | Running linters/test runners/git directly |
| Rename across files | `mcp_codeplane-codeplane_refactor_rename` | Find-and-replace, `sed` |
| Semantic diff | `mcp_codeplane-codeplane_semantic_diff` | `git_diff` for change review, manual comparison |

### Before You Edit: Decision Gate

STOP before using `write_source` for multi-file changes:
- Changing a name across files? → `refactor_rename` (NOT write_source + search)
- Moving a file? → `refactor_move` (NOT write_source + delete)
- Deleting a symbol or file? → `refactor_impact`

### Refactor: preview → inspect → apply/cancel

1. `refactor_rename`/`refactor_move`/`refactor_impact` — preview with `refactor_id`
2. If `verification_required`: `refactor_inspect` — review low-certainty matches
3. `refactor_apply` or `refactor_cancel`

### Span-Based Edits

`write_source` supports span edits: provide `start_line`, `end_line`, `expected_file_sha256`
(from `read_source` or `recon`), and `new_content`. Server validates hash; mismatch → re-read.
For updates, always include `expected_content` — the server fuzzy-matches nearby lines.

**Batching**: `edits` accepts multiple files — batch independent edits into one call.

### CRITICAL: Follow Agentic Hints

Responses may include `agentic_hint` — these are **direct instructions for your next
action**, not suggestions. Always read and execute them before proceeding.

Also check for: `coverage_hint`, `display_to_user`.

### Unknown Parameters

If you are unsure of a tool's parameters, call `describe(action='tool', name='<tool>')`
before guessing. Validation errors also include this hint, but calling `describe` proactively
avoids wasted round-trips.

### Common Mistakes (Don't Do These)

- **DON'T** skip `recon` and manually search+read — `recon` is faster and more complete
- **DON'T** guess tool parameter names — use `describe(action='tool', name='...')` first
- **DON'T** use `search` expecting source text — it returns spans only
- **DON'T** pass `context:` to search — the parameter is `enrichment`
- **DON'T** use `read_files` — it's replaced by `read_source` and `read_file_full`
- **DON'T** use `refactor_rename` with file:line:col — pass the symbol NAME only
- **DON'T** skip `checkpoint` after `write_source` — always lint + test your changes
- **DON'T** ignore `agentic_hint` in responses
- **DON'T** use raw `git add` + `git commit` — use `checkpoint` with `commit_message`
- **DON'T** dismiss lint/test failures as "pre-existing" or "not your problem" — fix ALL issues
<!-- /codeplane-instructions -->
