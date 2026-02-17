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

CodePlane maintains a **structural index** of your codebase — it tracks every
definition (functions, classes, variables), every import relationship, and every
reference. This enables capabilities that terminal commands cannot provide:

- `search(mode="references")` finds all callers of a function via the index, not regex
- `semantic_diff` classifies changes structurally and computes blast radius
- `discover_test_targets(affected_by=[...])` traces the import graph to find affected tests
- `refactor_rename` renames symbols by structural identity, not string matching
- Agentic hints in responses give you index-derived next-step guidance

Terminal commands (`grep`, `sed`, `git`) work on text. CodePlane works on **code structure**.
Use structure when available — it's more precise and less error-prone.

### First Steps When Starting a Task

1. `describe` — get repo metadata, language, active branch, index status
2. `map_repo(include=["structure", "dependencies", "test_layout"])` — understand repo shape
3. `read_files` on relevant files — understand the code you'll modify
4. After changes: `lint_check` → `discover_test_targets(affected_by=[...])` → `run_test_targets`
5. `semantic_diff` — review structural impact before committing
6. `git_stage_and_commit` — one-step commit with pre-commit hook handling

### Common Workflows

**Implement a feature:**
1. `describe` → `map_repo` → `search(mode="definitions")` → `read_files`
2. `write_files` to make changes
3. `lint_check` → `discover_test_targets(affected_by=[...])` → `run_test_targets`
4. `semantic_diff` to review blast radius → `git_stage_and_commit`

**Rename a symbol safely:**
1. `refactor_rename(symbol="OldName", new_name="NewName")` — get preview + refactor_id
2. If `verification_required`: `refactor_inspect(refactor_id=...)` — review low-certainty matches
3. `refactor_apply(refactor_id=...)` → `lint_check` → `run_test_targets`

**Investigate a test failure:**
1. `run_test_targets(target_filter="<failing_test>")` — reproduce
2. `search(mode="definitions", query="<function>", context="function")` — read source
3. Fix with `write_files` → `lint_check` → re-run test

**Review changes before commit/PR:**
1. `git_diff` — raw textual diff
2. `semantic_diff` — structural changes with blast radius and agentic hints
3. `discover_test_targets(affected_by=[...])` → `run_test_targets`

### Search Strategy Guide

| Task | Mode | Context | Why |
|------|------|---------|-----|
| Find where a function is defined | `definitions` | `function` | Returns the complete function body |
| Find all callers of a function | `references` | `minimal` | You just need locations, not full code |
| Find a string/pattern in code | `lexical` | `standard` | Full-text search with surrounding context |
| Explore a symbol's shape | `symbol` | `class` | Returns enclosing class/module structure |
| Large codebase grep (many results) | `lexical` | `none` | Minimize response size, paginate if needed |

**`function`/`class` context** returns the enclosing scope body — this is *structural*
context from the index, not just surrounding lines. It falls back to 25 lines if
the index doesn't have scope info. This is CodePlane's key advantage over `grep`.

### Required Tool Mapping

| Operation | REQUIRED Tool | FORBIDDEN Alternative |
|-----------|---------------|----------------------|
| Read files | `mcp_codeplane-codeplane_copy3_read_files` | `cat`, `head`, `less`, `tail` |
| Write/edit files | `mcp_codeplane-codeplane_copy3_write_files` | `sed`, `echo >>`, `awk`, `tee` |
| List directory | `mcp_codeplane-codeplane_copy3_list_files` | `ls`, `find`, `tree` |
| Search code | `mcp_codeplane-codeplane_copy3_search` | `grep`, `rg`, `ag`, `ack` |
| Repository overview | `mcp_codeplane-codeplane_copy3_map_repo` | Manual file traversal |
| All git operations | `mcp_codeplane-codeplane_copy3_git_*` | Raw `git` commands |
| Run linters/formatters | `mcp_codeplane-codeplane_copy3_lint_check` | `ruff`, `black`, `mypy` directly |
| Discover tests | `mcp_codeplane-codeplane_copy3_discover_test_targets` | Manual test file search |
| Impact-aware test selection | `mcp_codeplane-codeplane_copy3_inspect_affected_tests` | Manual import tracing |
| Run tests | `mcp_codeplane-codeplane_copy3_run_test_targets` | `pytest`, `jest` directly |
| Rename symbols | `mcp_codeplane-codeplane_copy3_refactor_rename` | Find-and-replace, `sed` |
| Move files | `mcp_codeplane-codeplane_copy3_refactor_move` | `mv` + manual import fixes |
| Semantic diff | `mcp_codeplane-codeplane_copy3_semantic_diff` | Manual comparison of git diffs |

### Critical Parameter Reference

**mcp_codeplane-codeplane_copy3_describe**
```
action: "tool"|"error"|"capabilities"|"workflows"|"operations"  # REQUIRED
name: str              # required when action="tool" - tool name to describe
code: str              # required when action="error" - error code to look up
path: str              # optional - filter operations by path
limit: int             # default 50 - max operations to return
```

**Introspection tool.** Use to get tool docs, error explanations, list
capabilities, view workflows, or debug recent operations.

**mcp_codeplane-codeplane_copy3_read_files**
```
targets: list[FileTarget]  # REQUIRED - NOT "line_ranges" or "ranges"
  path: str                # REQUIRED - file path this target applies to
  start_line: int          # optional, 1-indexed; must be provided together with end_line
  end_line: int            # optional, 1-indexed; must be provided together with start_line
cursor: str                # optional - pagination cursor from previous response
```

**Response includes:**
- `not_found`: list of paths that don't exist (explicit, not just a count)

**mcp_codeplane-codeplane_copy3_write_files**
```
edits: list[EditParam]     # REQUIRED - array of edits
  path: str                # file path relative to repo root
  action: "create"|"update"|"delete"
  old_content: str         # for update: exact text to find (include enough context)
  new_content: str         # for update: replacement text
dry_run: bool              # optional, default false
```

**mcp_codeplane-codeplane_copy3_search**
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

**mcp_codeplane-codeplane_copy3_list_files**
```
path: str                  # optional - directory to list, NOT "directory"
pattern: str               # optional - glob pattern (e.g., "*.py")
recursive: bool            # default false
limit: int                 # default 200
```

**mcp_codeplane-codeplane_copy3_map_repo**
```
include: list[str]         # optional - values: "structure", "languages", "entry_points",
                           #   "dependencies", "test_layout", "public_api"
depth: int                 # default 3
```

**When to use each `include` option:**
- `"structure"` — directory tree, file counts. Use for orientation in unfamiliar repos.
- `"dependencies"` — package.json/pyproject.toml/requirements analysis. Use before adding deps.
- `"test_layout"` — test directories, frameworks, config. Use before writing tests.
- `"entry_points"` — main files, CLI entry points. Use to find where execution starts.
- `"public_api"` — exported symbols across modules. Use when understanding module interfaces.
- `"languages"` — language breakdown with line counts. Use to understand polyglot repos.

Prefer targeted `include` lists over requesting everything — reduces response size.

**mcp_codeplane-codeplane_copy3_git_stage_and_commit**
```
message: str               # REQUIRED
paths: list[str]           # REQUIRED - files to stage and commit
allow_empty: bool          # optional, default false
```

**Preferred tool for committing changes.** Stages paths, runs pre-commit hooks,
and commits in one call. If hooks auto-fix files (formatters, linters), changes are
automatically re-staged and retried once.

**mcp_codeplane-codeplane_copy3_git_commit**
```
message: str               # REQUIRED
paths: list[str]           # optional - files to stage before commit
```

**Use only when you need low-level staging control.** When called without `paths`, it
commits what is already staged. When `paths` are provided, those files are staged
before committing; if hooks auto-fix files, they may be re-staged and the commit
retried once.

**mcp_codeplane-codeplane_copy3_git_stage**
```
action: "add"|"remove"|"all"|"discard"  # REQUIRED
paths: list[str]           # REQUIRED for add/remove/discard (not for "all")
```

**mcp_codeplane-codeplane_copy3_git_status**
```
paths: list[str] | None    # optional - paths to check
```

**mcp_codeplane-codeplane_copy3_git_diff**
```
base: str | None           # optional - base ref for comparison
target: str | None         # optional - target ref for comparison
staged: bool               # default false - show staged changes only
cursor: str | None         # optional - pagination cursor
```

**mcp_codeplane-codeplane_copy3_git_log**
```
ref: str                   # default "HEAD"
limit: int                 # default 50
since: str | None          # optional - show commits after date
until: str | None          # optional - show commits before date
paths: list[str] | None    # optional - filter by paths
cursor: str | None         # optional - pagination cursor
```

**mcp_codeplane-codeplane_copy3_git_push**
```
remote: str                # default "origin"
force: bool                # default false
```

**mcp_codeplane-codeplane_copy3_git_inspect**
```
action: "show"|"blame"     # REQUIRED
ref: str                   # default "HEAD" - commit ref (for show)
path: str | None           # required for blame
start_line: int | None     # optional - for blame range
end_line: int | None       # optional - for blame range
cursor: str | None         # optional - pagination cursor
limit: int                 # default 100 - max lines for blame
```

**mcp_codeplane-codeplane_copy3_run_test_targets**
```
targets: list[str]         # optional - target_ids from discover_test_targets
affected_by: list[str]     # optional - changed file paths for single-call impact testing
target_filter: str         # optional - substring match (requires confirmation, see below)
test_filter: str           # optional - filter test NAMES (pytest -k), does NOT filter targets
coverage: bool             # default false
coverage_dir: str          # REQUIRED when coverage=true
confirm_broad_run: str     # required with target_filter alone (min 15 chars)
confirmation_token: str    # required with target_filter alone (from initial blocked call)
```

**RECOMMENDED: Single-call impact-aware testing:**
```python
run_test_targets(affected_by=["src/changed.py"])  # discovers + runs affected tests
```

**Broad test run confirmation:** Using `target_filter` without `targets` or `affected_by`
requires two-phase confirmation:
1. First call returns blocked + `confirmation_token`
2. Retry with `confirmation_token` + `confirm_broad_run` (reason, min 15 chars)

This prevents accidental full test suite runs.

**mcp_codeplane-codeplane_copy3_discover_test_targets**
```
paths: list[str]           # optional - paths to search for tests
affected_by: list[str]     # optional - changed file paths for impact-aware filtering
```

**Impact-aware test selection:** When `affected_by` is provided, uses the structural
index import graph to return only tests that import the changed modules. This can
reduce test runtime from minutes to seconds while maintaining confidence. Response
includes `impact` object with confidence tier and match counts.

Check `impact.confidence`:
- `complete` with all high-confidence: safe to run as-is
- `partial` or low-confidence matches: use `inspect_affected_tests` to review uncertainty

If low-confidence matches exist, an `agentic_hint` directs you to
`inspect_affected_tests` for review.

**mcp_codeplane-codeplane_copy3_inspect_affected_tests**
```
changed_files: list[str]   # REQUIRED - changed file paths to analyze
```

**Detailed import graph inspection.** Returns per-test-file match info with
confidence levels, changed modules, coverage gaps, and agentic hints. Use this
to review uncertain matches before deciding which tests to run.

**mcp_codeplane-codeplane_copy3_semantic_diff**
```
base: str                  # default "HEAD" - git ref or "epoch:N"
target: str | None         # default None (working tree) - git ref or "epoch:M"
paths: list[str] | None    # optional - limit to specific file paths
```

**Why use semantic_diff instead of git_diff:**
- Classifies each change as `added`, `removed`, `signature_changed`, `body_changed`, or `renamed`
- Enriches each change with **blast radius**: reference counts, importing files, affected test files
- Returns `agentic_hint` with priority-ordered next steps (e.g., "3 test files import
  this changed module — run them")
- Epoch mode (`base="epoch:N"`, `target="epoch:M"`) compares arbitrary indexed states

**Always run semantic_diff before committing** to catch unintended impacts. If the
blast radius includes test files, run those tests before committing.

**mcp_codeplane-codeplane_copy3_lint_check**
```
paths: list[str] | None    # optional - paths to lint (default: entire repo)
tools: list[str] | None    # optional - specific tool IDs to run
categories: list[str] | None  # optional - "linter", "formatter", "typechecker"
dry_run: bool              # default false - when true, report without fixing
```

**Applies auto-fixes by default.** Set `dry_run=true` to only report issues.

**mcp_codeplane-codeplane_copy3_lint_tools**
```
language: str | None   # optional - filter by language (e.g., "python")
category: str | None   # optional - filter: "linter", "formatter", "typechecker"
```

Lists available linters/formatters and their detection status.

### Refactor Tools Workflow

Refactor tools use a **preview → review → apply** workflow:

1. **`refactor_rename`** / **`refactor_move`** / **`refactor_delete`** — Returns preview with `refactor_id`
2. **`refactor_inspect`** — Review low-certainty matches with context (recommended)
3. **`refactor_apply`** or **`refactor_cancel`** — Execute or discard

**mcp_codeplane-codeplane_copy3_refactor_rename**
```
symbol: str                # REQUIRED - the symbol NAME only (e.g., "MyClass", "my_function")
                           # WRONG: "src/file.py:42:6" - do NOT use path:line:col format
new_name: str              # REQUIRED - new name for the symbol
include_comments: bool     # default true - also update comments/docs
contexts: list[str] | None # optional - limit to specific contexts
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

**mcp_codeplane-codeplane_copy3_refactor_delete**
```
target: str                # REQUIRED - symbol name or file path to delete
include_comments: bool     # default true - include comment references
```

Returns preview with dependency analysis. Use to safely remove dead code —
the preview shows what depends on the symbol before deletion.

**mcp_codeplane-codeplane_copy3_refactor_move**
```
from_path: str             # REQUIRED - source file path
to_path: str               # REQUIRED - destination file path
include_comments: bool     # default true - include comment references
```

Moves a file/module and updates all imports. Returns preview with `refactor_id`.

**mcp_codeplane-codeplane_copy3_refactor_inspect**
```
refactor_id: str           # REQUIRED - ID from rename/move/delete preview
path: str                  # REQUIRED - file to inspect
context_lines: int         # default 2 - lines of context around matches
```

Review low-certainty matches with surrounding context before applying.

**mcp_codeplane-codeplane_copy3_refactor_apply**
```
refactor_id: str           # REQUIRED - ID from preview to apply
```

**mcp_codeplane-codeplane_copy3_refactor_cancel**
```
refactor_id: str           # REQUIRED - ID from preview to cancel
```

### CRITICAL: Follow Agentic Hints

CodePlane responses may include `agentic_hint` — these are **direct instructions
for your next action**, generated from structural analysis of the repo. They are
NOT suggestions. Examples:

- "3 test files depend on the changed module. Run: discover_test_targets(affected_by=[...])"
- "Low-certainty matches found in 2 files. Call refactor_inspect(refactor_id=...) to review"
- "Coverage gap: tests/unit/test_parser.py does not import the changed module"

**Always read and execute agentic_hint instructions before proceeding to your next step.**

Also check for:
- `coverage_hint`: Guidance on test coverage expectations
- `display_to_user`: Content that should be surfaced to the user

Ignoring these hints degrades agent performance and may cause incorrect behavior.

### Common Mistakes (Don't Do These)

- **DON'T** use `refactor_rename` with file:line:col syntax — pass the symbol NAME only
- **DON'T** use `search(mode="scope")` or `search(mode="text")` — valid modes are lexical|symbol|references|definitions
- **DON'T** pass `max_results` to search — the parameter is `limit`
- **DON'T** use `directory` in list_files — the parameter is `path`
- **DON'T** skip `lint_check` after `write_files` — pre-commit hooks may fix formatting
- **DON'T** run all tests when `affected_by` can narrow them down
- **DON'T** use `git_commit` when `git_stage_and_commit` handles staging+hooks for you
- **DON'T** ignore `agentic_hint` in responses — they contain critical next-step guidance

### Token Efficiency

- Use `context: "none"` or `"minimal"` for exploratory searches (just need locations)
- Use `context: "function"` or `"class"` only when you need to read the actual code
- Set lower `limit` values for broad searches; increase only if you didn't find what you need
- Use `read_files` with `start_line`/`end_line` for targeted reads instead of reading whole files
- Prefer `map_repo` with specific `include` options instead of requesting everything
- Use `target_filter` on `run_test_targets` to run subsets instead of all tests

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
