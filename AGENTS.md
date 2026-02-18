# Agent Instructions

Instructions for AI coding agents working in this repository.

<!-- codeplane-instructions -->
## CodePlane MCP: Mandatory Tool Selection

This repository uses CodePlane MCP. **You MUST use CodePlane tools instead of terminal commands.**

Terminal fallback is permitted ONLY when no CodePlane tool exists for the operation.

### What CodePlane Provides

CodePlane maintains a **structural index** of your codebase — definitions, imports,
references. This enables structural search, semantic diff, impact-aware test selection,
and safe refactoring that terminal commands cannot provide.

### Three-Tool Read Model

  search        -> semantic enumeration (spans + metadata, NEVER source text)
  read_source   -> bounded semantic retrieval (span-based or structural-unit-based)
  read_file_full -> gated bulk access (two-phase confirmation, resource-first delivery)

Search = find. Read = retrieve. Full = gated.

`read_source` accepts multiple `targets` in one call — batch reads of independent spans.
Response includes `file_sha256` per file — save it for `write_source` span edits.

### First Steps When Starting a Task

1. `describe` — get repo metadata, language, active branch, index status
2. `map_repo(include=["structure", "dependencies", "test_layout"])` — understand repo shape
3. `search` to find relevant code — definitions, references, or lexical patterns
4. `read_source` on spans from search results — understand the code you'll modify
5. After changes: `lint_check` → `run_test_targets(affected_by=["changed_file.py"])` for impact-aware testing
6. `semantic_diff` — review structural impact before committing
7. `git_stage_and_commit` — one-step commit with pre-commit hook handling

**Testing rule**: NEVER run the full test suite or use test runners directly.
Always use `run_test_targets(affected_by=[...])` with the files you changed.
This runs only the tests impacted by your changes — fast, targeted, sufficient.

### Required Tool Mapping

| Operation | REQUIRED Tool | FORBIDDEN Alternative |
|-----------|---------------|----------------------|
| Read source | `mcp_codeplane-codeplane_copy3_read_source` | `cat`, `head`, `less`, `tail` |
| Read full file | `mcp_codeplane-codeplane_copy3_read_file_full` | `cat`, `head`, bulk reads |
| Write/edit files | `mcp_codeplane-codeplane_copy3_write_source` | `sed`, `echo >>`, `awk`, `tee` |
| List directory | `mcp_codeplane-codeplane_copy3_list_files` | `ls`, `find`, `tree` |
| Search code | `mcp_codeplane-codeplane_copy3_search` | `grep`, `rg`, `ag`, `ack` |
| Repository overview | `mcp_codeplane-codeplane_copy3_map_repo` | Manual file traversal |
| All git operations | `mcp_codeplane-codeplane_copy3_git_*` | Raw `git` commands |
| Run linters | `mcp_codeplane-codeplane_copy3_lint_check` | `ruff`, `black`, `mypy` directly |
| Discover tests | `mcp_codeplane-codeplane_copy3_discover_test_targets` | Manual test file search |
| Run tests | `mcp_codeplane-codeplane_copy3_run_test_targets` | Test runners directly |
| Rename across files | `mcp_codeplane-codeplane_copy3_refactor_rename` | Find-and-replace, `sed` |
| Semantic diff | `mcp_codeplane-codeplane_copy3_semantic_diff` | Manual comparison |

### Before You Edit: Decision Gate

STOP before using `write_source` for multi-file changes:
- Changing a name across files? → `refactor_rename` (NOT write_source + search)
- Moving a file? → `refactor_move` (NOT write_source + delete)
- Deleting a symbol or file? → `refactor_delete`

### Before You Read: Decision Gate

STOP before using `read_file_full`:
- Need to find call sites or consumers? → `search(mode=references)` + `read_source`
- Need to understand a specific function? → `search(mode=definitions)` + `read_source`
- Need the ENTIRE file content with no alternative? → ONLY then `read_file_full`

| Task | Mode | Enrichment | Why |
|------|------|------------|-----|
| Find where a function is defined | `definitions` | `minimal` | Returns span, use read_source for body |
| Find all callers of a function | `references` | `none` | You just need locations |
| Find a string/pattern in code | `lexical` | `none` | Spans only, read_source for content |
| Explore a symbol's shape | `symbol` | `standard` | Metadata without source text |

Search NEVER returns source text. Use `read_source` with spans from search results.

### Refactor: preview → inspect → apply/cancel

1. `refactor_rename`/`refactor_move`/`refactor_delete` — preview with `refactor_id`
2. If `verification_required`: `refactor_inspect` — review low-certainty matches
3. `refactor_apply` or `refactor_cancel`

### Span-Based Edits

`write_source` supports span edits: provide `start_line`, `end_line`, `expected_file_sha256`
(from `read_source`), and `new_content`. Server validates hash; mismatch → re-read.
`edits` accepts multiple entries across different files — always batch independent edits
into a single `write_source` call to minimize round-trips.
For updates, always include `expected_content` (the old text at the span) — the server
fuzzy-matches nearby lines if your line numbers are slightly off, auto-correcting
within a few lines. This is required.

### CRITICAL: Follow Agentic Hints

Responses may include `agentic_hint` — these are **direct instructions for your next
action**, not suggestions. Always read and execute them before proceeding.

Also check for: `coverage_hint`, `display_to_user`.

### Common Mistakes (Don't Do These)

- **DON'T** use `search` expecting source text — it returns spans only
- **DON'T** pass `context:` to search — the parameter is `enrichment`
- **DON'T** use `read_files` — it's replaced by `read_source` and `read_file_full`
- **DON'T** use `refactor_rename` with file:line:col — pass the symbol NAME only
- **DON'T** skip `lint_check` after `write_source`
- **DON'T** ignore `agentic_hint` in responses
- **DON'T** use `target_filter` for post-change testing — use `affected_by` on `run_test_targets`
<!-- /codeplane-instructions -->
