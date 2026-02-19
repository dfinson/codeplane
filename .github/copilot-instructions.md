# CodePlane — Copilot Instructions

Authority: SPEC.md wins. If unsure or there is a spec conflict, stop and ask.

## ⛔ CRITICAL: E2E Tests Are OFF-LIMITS ⛔

**DO NOT RUN E2E TESTS** unless ALL of the following are true:
1. The user has EXPLICITLY requested E2E tests by name
2. You have explained the cost (clones real repos, starts daemons, takes minutes, high CPU)
3. The user has CONFIRMED they want to proceed

E2E tests (`tests/e2e/`) are:
- **Excluded by default** from `pytest` runs
- **Resource-intensive**: Clone real GitHub repos, start CodePlane daemons
- **Slow**: Can take 5-15+ minutes and consume significant CPU
- **NOT for routine validation**: Use unit/integration tests instead

**When running tests, ALWAYS use:**
- `pytest tests/` (e2e excluded by default via pyproject.toml)
- `target_filter` with specific test paths (NOT `tests/e2e`)

**To run E2E tests (ONLY with explicit user confirmation):**
- `pytest tests/e2e/ --ignore=` (override the default ignore)

**Violating this wastes user resources and disrupts their workflow.**

---

1) MCP First (Default)
If a CodePlane MCP tool exists for an action, use it.
Terminal commands are fallback only when the tool does not exist.

- Read files: mcp_codeplane_read_files (not cat/head)
- Edit files: mcp_codeplane_atomic_edit_files (not sed/echo/awk)
- List files: mcp_codeplane_list_files (not ls/find)
- Map repo: mcp_codeplane_map_repo (not manual traversal)
- Search: CodePlane search tools (not grep/rg)
- Git ops: CodePlane git tools (not raw git)

2) Non-Negotiable Invariants
- Refactors are index-based (no regex, no guessing)
- No autonomous mutations (all reconciliation is triggered)
- Determinism over heuristics
- Structured outputs only (no raw text)
- Ledger is append-only (no updates or deletes)

3) No Hacks (Root Cause Only)
If something fails, diagnose and fix it properly. Do not “make it pass”.

Forbidden:
- # type: ignore, Any, dishonest cast()
- try/except or inline imports to dodge module issues
- regex or string parsing for structured data
- raw SQL to bypass ORM or typing
- empty except blocks or silent fallbacks
- “for now” workarounds

If you cannot solve it correctly with available tools or information, say so and ask.

4) All Checks Must Pass (Method-Agnostic)
Lint, typecheck, tests, and CI must be green.

- Prefer CodePlane MCP endpoints for lint/test/typecheck when available
- Terminal commands are acceptable only if MCP support does not exist
- The requirement is the result, not the invocation method

5) GitHub Remote Actions Must Be Exact
When asked to perform a specific remote action (merge, resolve threads, release, etc.):
- do exactly that action, or
- state it is not possible with available tools

No substitutions.

6) Change Discipline (Minimal)
- Before coding: read the issue, relevant SPEC.md sections, and match repo patterns
- Prefer minimal code; do not invent abstractions or reimplement libraries
- Tests should be small, behavioral, and parameterized when appropriate

7) Read MCP Response Hints
CodePlane MCP responses may include `agentic_hint`, `coverage_hint`, or `display_to_user` fields.
Always check for and follow these hints—they provide actionable guidance for next steps.

8) NEVER Reset Hard Without Approval
**ABSOLUTE PROHIBITION**: Never execute `git reset --hard` under any circumstances without explicit user approval.

This applies to:
- `git reset --hard` (any ref)
- `mcp_codeplane_git_reset` with `mode: hard`
- Any equivalent destructive operation that discards uncommitted changes

If you believe a hard reset is needed:
1. STOP and explain why you think it's necessary
2. List what uncommitted work will be lost
3. Wait for explicit user confirmation before proceeding

Violating this rule destroys work irreversibly and may affect parallel agent workflows.

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

### First Steps When Starting a Task

1. `describe` — get repo metadata, language, active branch, index status
2. `map_repo(include=["structure", "dependencies", "test_layout"])` — understand repo shape
3. `search` to find relevant code — definitions, references, or lexical patterns
4. `read_source` on spans from search results — understand the code you'll modify
5. After changes: `lint_check` → `run_test_targets(affected_by=["changed_file"])` for impact-aware testing
6. `semantic_diff` — review structural impact before committing
7. `git_stage_and_commit` — one-step commit with pre-commit hook handling

**Testing rule**: NEVER run the full test suite or use test runners directly.
Always use `run_test_targets(affected_by=[...])` with the files you changed.
This runs only the tests impacted by your changes — fast, targeted, sufficient.

### Reviewing Changes (PR Review)

1. `semantic_diff(base="main")` — structural overview of all changes vs main
2. `read_source` on changed symbols — review each change in context
3. `run_test_targets(affected_by=[...])` — verify correctness

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
| All git operations | `mcp_codeplane-codeplane_copy3_git_*` | Raw `git` commands |
| Run linters | `mcp_codeplane-codeplane_copy3_lint_check` | Running linters directly |
| Discover tests | `mcp_codeplane-codeplane_copy3_discover_test_targets` | Manual test file search |
| Run tests | `mcp_codeplane-codeplane_copy3_run_test_targets` | Test runners directly |
| Rename across files | `mcp_codeplane-codeplane_copy3_refactor_rename` | Find-and-replace, `sed` |
| Semantic diff | `mcp_codeplane-codeplane_copy3_semantic_diff` | `git_diff` for change review, manual comparison |
| Stage and commit | `mcp_codeplane-codeplane_copy3_git_stage_and_commit` | `git_stage` + `git_commit` separately |

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
`lint_check` takes no arguments — always lints the full repo.
`run_test_targets` params: `affected_by` (list of changed file paths) for post-change testing.

### Refactor: preview → inspect → apply/cancel

1. `refactor_rename`/`refactor_move`/`refactor_impact` — preview with `refactor_id`
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
- **DON'T** skip `lint_check` after `write_source`
- **DON'T** ignore `agentic_hint` in responses
- **DON'T** use `target_filter` for post-change testing — use `affected_by` on `run_test_targets`
- **DON'T** use `git_stage` + `git_commit` separately — use `git_stage_and_commit`
<!-- /codeplane-instructions -->
