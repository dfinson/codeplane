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

### Required Tool Mapping

| Operation | REQUIRED Tool | FORBIDDEN Alternative |
|-----------|---------------|----------------------|
| Read files | `mcp_codeplane-codeplane_read_files` | `cat`, `head`, `less`, `tail` |
| Write/edit files | `mcp_codeplane-codeplane_write_files` | `sed`, `echo >>`, `awk`, `tee` |
| List directory | `mcp_codeplane-codeplane_list_files` | `ls`, `find`, `tree` |
| Search code | `mcp_codeplane-codeplane_search` | `grep`, `rg`, `ag`, `ack` |
| Repository overview | `mcp_codeplane-codeplane_map_repo` | Manual file traversal |
| All git operations | `mcp_codeplane-codeplane_git_*` | Raw `git` commands |
| Run linters/formatters | `mcp_codeplane-codeplane_lint_check` | `ruff`, `black`, `mypy` directly |
| Discover tests | `mcp_codeplane-codeplane_discover_test_targets` | Manual test file search |
| Run tests | `mcp_codeplane-codeplane_run_test_targets` | `pytest`, `jest` directly |
| Rename symbols | `mcp_codeplane-codeplane_refactor_rename` | Find-and-replace, `sed` |
| Move files | `mcp_codeplane-codeplane_refactor_move` | `mv` + manual import fixes |
| Semantic diff | `mcp_codeplane-codeplane_semantic_diff` | Manual comparison of git diffs |

### Critical Parameter Reference

**mcp_codeplane-codeplane_read_files**
```
targets: list[FileTarget]  # REQUIRED - NOT "line_ranges" or "ranges"
  path: str                # REQUIRED - file path this target applies to
  start_line: int          # optional, 1-indexed; must be provided together with end_line
  end_line: int            # optional, 1-indexed; must be provided together with start_line
cursor: str                # optional - pagination cursor from previous response
```

Response includes `not_found: list[str]` when requested files don't exist.

**mcp_codeplane-codeplane_write_files**
```
edits: list[EditParam]     # REQUIRED - array of edits
  path: str                # file path relative to repo root
  action: "create"|"update"|"delete"
  old_content: str         # for update: exact text to find (include enough context)
  new_content: str         # for update: replacement text
dry_run: bool              # optional, default false
```

**mcp_codeplane-codeplane_search**
```
query: str                 # REQUIRED
mode: "lexical"|"symbol"|"references"|"definitions"  # default "lexical", NOT "scope" or "text"
context: "none"|"minimal"|"standard"|"rich"|"function"|"class"  # default "standard"
                           # none=0, minimal=1, standard=5, rich=20 lines
                           # function/class: enclosing scope body (structural) with 25-line fallback
context_lines: int         # optional - override lines for line-based, or fallback for structural
limit: int                 # default 20, NOT "max_results"
cursor: str                # optional - pagination cursor from previous response
```

**mcp_codeplane-codeplane_list_files**
```
path: str                  # optional - directory to list, NOT "directory"
pattern: str               # optional - glob pattern (e.g., "*.py")
recursive: bool            # default false
limit: int                 # default 200
```

**mcp_codeplane-codeplane_map_repo**
```
include: list[str]         # optional - values: "structure", "languages", "entry_points",
                           #   "dependencies", "test_layout", "public_api"
depth: int                 # default 3
```

**mcp_codeplane-codeplane_git_stage_and_commit**
```
message: str               # REQUIRED
paths: list[str]           # REQUIRED - files to stage and commit
allow_empty: bool          # optional, default false
```

**Preferred tool for committing changes.** Stages paths, runs pre-commit hooks,
and commits in one call. If hooks auto-fix files (formatters, linters), changes are
automatically re-staged and retried once.

**mcp_codeplane-codeplane_git_commit**
```
message: str               # REQUIRED
paths: list[str]           # optional - files to stage before commit
```

**Use only when you need low-level staging control.** When called without `paths`, it
commits what is already staged. When `paths` are provided, those files are staged
before committing; if hooks auto-fix files, they may be re-staged and the commit
retried once.

**mcp_codeplane-codeplane_git_stage**
```
action: "add"|"remove"|"all"|"discard"  # REQUIRED
paths: list[str]           # REQUIRED for add/remove/discard (not for "all")
```

**mcp_codeplane-codeplane_run_test_targets**
```
targets: list[str]         # optional - target_ids from discover_test_targets
target_filter: str         # optional - substring match on target paths
test_filter: str           # optional - filter test NAMES (pytest -k), does NOT filter targets
coverage: bool             # default false
coverage_dir: str          # REQUIRED when coverage=true
```

**mcp_codeplane-codeplane_semantic_diff**
```
base: str                  # default "HEAD" - git ref or "epoch:N"
target: str | None         # default None (working tree) - git ref or "epoch:M"
paths: list[str] | None    # optional - limit to specific file paths
```

**Structural change summary from index facts.** Compares definitions between two
states and classifies changes as added, removed, signature_changed, body_changed,
or renamed. Includes blast-radius enrichment (reference counts, importing files,
affected test files) and priority-ordered agentic hints.

Modes:
- Git mode (default): base/target are git refs
- Epoch mode: base="epoch:N", target="epoch:M"

### Refactor Tools Workflow

Refactor tools use a **preview → review → apply** workflow:

1. **`refactor_rename`** / **`refactor_move`** / **`refactor_delete`** — Returns preview with `refactor_id`
2. **`refactor_inspect`** — Review low-certainty matches with context (recommended)
3. **`refactor_apply`** or **`refactor_cancel`** — Execute or discard

**mcp_codeplane-codeplane_refactor_rename**
```
symbol: str                # REQUIRED - the symbol NAME only (e.g., "MyClass", "my_function")
                           # WRONG: "src/file.py:42:6" - do NOT use path:line:col format
new_name: str              # REQUIRED - new name for the symbol
include_comments: bool     # default true - also update comments/docs
```

**Certainty levels:**
- `high`: Proven by structural index (definitions, same-file refs)
- `medium`: Comment/docstring references
- `low`: Lexical text matches (cross-file refs, imports, strings)

**When low-certainty is safe:** Unique identifiers (e.g., `MyClassName`) — all matches likely correct.
**When to inspect first:** Common words (e.g., `data`, `result`) — may have false positives.

**Response fields:**
- `verification_required`: True if low-certainty matches exist
- `verification_guidance`: Instructions for reviewing
- `low_certainty_files`: Files needing inspection

### Response Handling

CodePlane responses include structured metadata. You must inspect and act on:
- `agentic_hint`: Direct instructions for your next action
- `coverage_hint`: Guidance on test coverage expectations
- `display_to_user`: Content that should be surfaced to the user

Ignoring these hints degrades agent performance and may cause incorrect behavior.

### Response Size Budget & Pagination

All data-returning endpoints enforce a per-response byte budget (~40 KB) to stay
within VS Code's output limits. When a response is truncated:

- The `pagination` object will contain `"truncated": true`
- If more data is available, `pagination.next_cursor` provides a cursor to fetch
  the next page (pass it as the `cursor` parameter on the next call)
- `pagination.total_estimate` may indicate the total number of results available

**If `truncated: true` but no `next_cursor`:** The data cannot be paginated (e.g.,
metadata alone exceeds budget). Check `agentic_hint` for guidance on narrowing the request.

**Affected endpoints:** `search`, `read_files`, `git_log`, `git_diff`, `git_inspect` (blame), `map_repo`

**Agent behavior when `truncated` is true:**
1. Process the results already returned
2. If `next_cursor` is present and more context is needed, call again with `cursor` set to `next_cursor`
3. If `next_cursor` is absent, narrow the request (e.g., filter by paths, reduce limit)

The first result is always included regardless of size (no empty pages).
<!-- /codeplane-instructions -->
