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

### Four-Tool Read Model

  search         -> semantic enumeration (spans + metadata, NEVER source text)
  read_scaffold  -> structural skeleton (imports, signatures, hierarchy — no source)
  read_source    -> bounded semantic retrieval (span-based or structural-unit-based)
  read_file_full -> gated bulk access (two-phase confirmation, resource-first delivery)

Search = find. Scaffold = orient. Read = retrieve. Full = gated.

`read_source` accepts multiple `targets` in one call — batch reads of independent spans.
`read_source` target format: `[{"path": "src/foo.py", "start_line": 10, "end_line": 50}]`
Response includes `file_sha256` per file — save it for `write_source` span edits.

### CRITICAL: After Every Code Change

After ANY edit via `write_source` or other mutation:

1. **`verify(changed_files=[...])`** — REQUIRED. Runs lint + affected tests. Do NOT use `pytest`, `ruff`, `mypy` in terminal.
2. **`commit(message=..., all=True, push=True)`** — REQUIRED to save. Do NOT use `git add/commit/push` in terminal.

**Terminal commands for lint, test, or git operations are ALWAYS WRONG in this repo.**

### First Steps When Starting a Task

1. `describe` — get repo metadata, language, active branch, index status
2. `map_repo(include=["structure", "dependencies", "test_layout"])` — understand repo shape
3. `search` to find relevant code — definitions, references, or lexical patterns
4. `read_source` on spans from search results — understand the code you'll modify
5. After changes: `verify(changed_files=[...])` — lint + affected tests in one call
6. `semantic_diff` — review structural impact before committing
7. `commit(message="...", all=True, push=True)` — stage, hooks, commit, push

**Testing rule**: NEVER run the full test suite or use test runners directly.
Always use `verify(changed_files=[...])` with the files you changed.
This runs lint + only the tests impacted by your changes — fast, targeted, sufficient.

### Reviewing Changes (PR Review)

1. `semantic_diff(base="main")` — structural overview of all changes vs main
2. `read_source` on changed symbols — review each change in context
3. `verify(changed_files=[...])` — lint + affected tests

`semantic_diff` first — NOT `git_diff`. It gives symbol-level changes, not raw patches.

### Required Tool Mapping

| Operation | REQUIRED Tool | FORBIDDEN Alternative |
|-----------|---------------|----------------------|
| File scaffold | `mcp_codeplane-codeplane_copy3_read_scaffold` | Manual traversal, `cat` for structure |
| Read source | `mcp_codeplane-codeplane_copy3_read_source` | `cat`, `head`, `less`, `tail` |
| Read full file | `mcp_codeplane-codeplane_copy3_read_file_full` | `cat`, `head`, bulk reads |
| Write/edit files | `mcp_codeplane-codeplane_copy3_write_source` | `sed`, `echo >>`, `awk`, `tee` |
| List directory | `mcp_codeplane-codeplane_copy3_list_files` | `ls`, `find`, `tree` |
| Search code | `mcp_codeplane-codeplane_copy3_search` | `grep`, `rg`, `ag`, `ack` |
| Repository overview | `mcp_codeplane-codeplane_copy3_map_repo` | Manual file traversal |
| Lint + test | `mcp_codeplane-codeplane_copy3_verify` | Running linters/test runners directly |
| Rename across files | `mcp_codeplane-codeplane_copy3_refactor_rename` | Find-and-replace, `sed` |
| Semantic diff | `mcp_codeplane-codeplane_copy3_semantic_diff` | `git_diff` for change review, manual comparison |
| Commit | `mcp_codeplane-codeplane_copy3_commit` | Raw `git add` + `git commit` |

### Before You Edit: Decision Gate

STOP before using `write_source` for multi-file changes:
- Changing a name across files? → `refactor_rename` (NOT write_source + search)
- Moving a file? → `refactor_move` (NOT write_source + delete)
- Deleting a symbol or file? → `refactor_impact`

### Before You Read: Decision Gate

STOP before using `read_file_full`:
- Need a file's structure or API shape? → `read_scaffold` (signatures, hierarchy, no source)
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

`search` params: `query` (str), `mode` (definitions|references|lexical|symbol), `enrichment` (none|minimal|standard|function|class).
`verify` params: `changed_files` (list of changed file paths) — runs lint + affected tests.
`commit` params: `message` (str), `all` (bool), `push` (bool) — stage, hooks, commit, push.

### Refactor: preview → inspect → apply/cancel

1. `refactor_rename`/`refactor_move`/`refactor_impact` — preview with `refactor_id`
2. If `verification_required`: `refactor_inspect` — review low-certainty matches
3. `refactor_apply` or `refactor_cancel`

### Span-Based Edits

`write_source` supports span edits: provide `start_line`, `end_line`, `expected_file_sha256`
(from `read_source`), and `new_content`. Server validates hash; mismatch → re-read.
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

- **DON'T** guess tool parameter names — use `describe(action='tool', name='...')` first
- **DON'T** use `search` expecting source text — it returns spans only
- **DON'T** pass `context:` to search — the parameter is `enrichment`
- **DON'T** use `read_files` — it's replaced by `read_source` and `read_file_full`
- **DON'T** use `refactor_rename` with file:line:col — pass the symbol NAME only
- **DON'T** skip `verify` after `write_source` — always lint + test your changes
- **DON'T** ignore `agentic_hint` in responses
- **DON'T** use raw `git add` + `git commit` — use `commit` (handles hooks, auto-fix, push)
<!-- /codeplane-instructions -->
