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

### First Steps When Starting a Task

1. `describe` — get repo metadata, language, active branch, index status
2. `map_repo(include=["structure", "dependencies", "test_layout"])` — understand repo shape
3. `read_source` on relevant files — understand the code you'll modify
4. After changes: `lint_check` → `discover_test_targets(affected_by=[...])` → `run_test_targets`
5. `semantic_diff` — review structural impact before committing
6. `git_stage_and_commit` — one-step commit with pre-commit hook handling

### Required Tool Mapping

| Operation | REQUIRED Tool | FORBIDDEN Alternative |
|-----------|---------------|----------------------|
| Read source | `mcp_codeplane-codeplane_read_source` | `cat`, `head`, `less`, `tail` |
| Read full file | `mcp_codeplane-codeplane_read_file_full` | `cat`, `head`, bulk reads |
| Write/edit files | `mcp_codeplane-codeplane_write_files` | `sed`, `echo >>`, `awk`, `tee` |
| List directory | `mcp_codeplane-codeplane_list_files` | `ls`, `find`, `tree` |
| Search code | `mcp_codeplane-codeplane_search` | `grep`, `rg`, `ag`, `ack` |
| Repository overview | `mcp_codeplane-codeplane_map_repo` | Manual file traversal |
| All git operations | `mcp_codeplane-codeplane_git_*` | Raw `git` commands |
| Run linters | `mcp_codeplane-codeplane_lint_check` | `ruff`, `black`, `mypy` directly |
| Discover tests | `mcp_codeplane-codeplane_discover_test_targets` | Manual test file search |
| Run tests | `mcp_codeplane-codeplane_run_test_targets` | `pytest`, `jest` directly |
| Rename symbols | `mcp_codeplane-codeplane_refactor_rename` | Find-and-replace, `sed` |
| Semantic diff | `mcp_codeplane-codeplane_semantic_diff` | Manual comparison |

### Search Strategy Guide (enrichment, no source text)

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

`write_files` supports span edits: provide `start_line`, `end_line`, `expected_file_sha256`
(from `read_source`), and `new_content`. Server validates hash; mismatch → re-read.

### CRITICAL: Follow Agentic Hints

Responses may include `agentic_hint` — these are **direct instructions for your next
action**, not suggestions. Always read and execute them before proceeding.

Also check for: `coverage_hint`, `display_to_user`.

### Common Mistakes (Don't Do These)

- **DON'T** use `search` expecting source text — it returns spans only
- **DON'T** pass `context:` to search — the parameter is `enrichment`
- **DON'T** use `read_files` — it's replaced by `read_source` and `read_file_full`
- **DON'T** use `refactor_rename` with file:line:col — pass the symbol NAME only
- **DON'T** skip `lint_check` after `write_files`
- **DON'T** ignore `agentic_hint` in responses
<!-- /codeplane-instructions -->
