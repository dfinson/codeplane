# CodePlane — Copilot Instructions

Authority: SPEC.md wins. If unsure or there is a spec conflict, stop and ask.

## ⛔ CRITICAL: Use `checkpoint` — NEVER Terminal ⛔

**After ANY code change**, you MUST call:
- `checkpoint(changed_files=[...], commit_message="...", push=True)` — lint + test + commit + push + semantic diff

**FORBIDDEN alternatives** (these are WRONG, do NOT use them):
- `pytest`, `python -m pytest`, `ruff`, `ruff check`, `ruff format`, `mypy` in terminal
- `git add`, `git commit`, `git push` in terminal
- Any test runner or linter invoked via `run_in_terminal`

The `checkpoint` tool runs lint + affected tests + commit + push in one call.
Terminal commands for these operations are ALWAYS wrong in this repo.

## ⛔ CHECKPOINT IS A BLOCKING GATE ⛔

`checkpoint`, `semantic_diff`, and `map_repo` are **exclusive tools**.
The server enforces a session lock — no other tool runs concurrently.

**After checkpoint returns, you MUST fully process the result before ANY other work:**
1. Read `passed` (true/false) — stop here if false
2. Read `lint` section — check for errors in YOUR changed files
3. Read `tests` section — check for failures
4. Read `commit` section — confirm commit/push status
5. Read `agentic_hint` — follow its instructions
6. ONLY THEN resume other work

**Lint/test failures are YOUR fault.** You passed `changed_files` — checkpoint runs
lint and tests ONLY on code affected by those files. Do NOT dismiss failures as
"pre-existing" or "not related to my changes". They almost certainly ARE your changes.
Errors in OTHER files are often transitive — caused by your changes breaking
downstream dependents. These are still YOUR responsibility. Fix ALL issues.

**If checkpoint fails or you cannot read the full result: STOP and tell the user.**
Do NOT continue editing files after a failed or unread checkpoint.

## ⛔ Mutation Budget — Batching Rules ⛔

**Session hard limit: 2 mutation batches before checkpoint.**

Each `refactor_edit` call = 1 batch, regardless of how many files or edits it contains.

**CRITICAL: One call can edit MULTIPLE files.** Each edit in the `edits` array has its
own `path` via its `edit_ticket`. Batch source + test edits together in ONE call.
One call editing 3 files = 1 batch. Three calls editing 1 file each = 3 batches (over limit).

**`expected_edit_calls` in `refactor_plan`:**
- Defaults to 1. Prefer 1.
- Automatically clamped to remaining session budget (cannot exceed it).
- If >1, you MUST provide `batch_justification` (100+ chars) explaining why.

**Checkpoint failure is a recovery point, not a dead end:**
- Budget RESETS (back to 0 mutations)
- A `fix_plan` with pre-minted `EditTicket`s is returned
- Call `refactor_edit` directly with those tickets — no new `refactor_plan` needed
- Batch ALL fix edits (source + tests) into ONE call, then retry checkpoint

## ⛔ E2E Tests Are OFF-LIMITS ⛔

**DO NOT RUN E2E TESTS** unless ALL of the following are true:
1. The user has EXPLICITLY requested E2E tests by name
2. You have explained the cost (clones real repos, starts daemons, takes minutes, high CPU)
3. The user has CONFIRMED they want to proceed

E2E tests (`tests/e2e/`) are:
- **Excluded by default** from `pytest` runs
- **Resource-intensive**: Clone real GitHub repos, start CodePlane daemons
- **Slow**: Can take 5-15+ minutes and consume significant CPU
- **NOT for routine validation**: Use `checkpoint` instead

**To run E2E tests (ONLY with explicit user confirmation):**
- `pytest tests/e2e/ --ignore=` (override the default ignore)

**Violating this wastes user resources and disrupts their workflow.**

---

1) Non-Negotiable Invariants
- Refactors are index-based (no regex, no guessing)
- No autonomous mutations (all reconciliation is triggered)
- Determinism over heuristics
- Structured outputs only (no raw text)
- Ledger is append-only (no updates or deletes)

2) No Hacks (Root Cause Only)
If something fails, diagnose and fix it properly. Do not "make it pass".

Forbidden:
- # type: ignore, Any, dishonest cast()
- try/except or inline imports to dodge module issues
- regex or string parsing for structured data
- raw SQL to bypass ORM or typing
- empty except blocks or silent fallbacks
- "for now" workarounds

If you cannot solve it correctly with available tools or information, say so and ask.

3) All Checks Must Pass (Method-Agnostic)
Lint, typecheck, tests, and CI must be green.

- Prefer CodePlane MCP endpoints for lint/test/typecheck when available
- Terminal commands are acceptable only if MCP support does not exist
- The requirement is the result, not the invocation method

4) GitHub Remote Actions Must Be Exact
When asked to perform a specific remote action (merge, resolve threads, release, etc.):
- do exactly that action, or
- state it is not possible with available tools

No substitutions.

5) Change Discipline (Minimal)
- Before coding: read the issue, relevant SPEC.md sections, and match repo patterns
- Prefer minimal code; do not invent abstractions or reimplement libraries
- Tests should be small, behavioral, and parameterized when appropriate

6) Read MCP Response Hints
CodePlane MCP responses may include `agentic_hint`, `coverage_hint`, or `display_to_user` fields.
Always check for and follow these hints—they provide actionable guidance for next steps.

7) NEVER Reset Hard Without Approval
**ABSOLUTE PROHIBITION**: Never execute `git reset --hard` under any circumstances without explicit user approval.

This applies to:
- `git reset --hard` (any ref)
- Any equivalent destructive operation that discards uncommitted changes

If you believe a hard reset is needed:
1. STOP and explain why you think it's necessary
2. List what uncommitted work will be lost
3. Wait for explicit user confirmation before proceeding

Violating this rule destroys work irreversibly and may affect parallel agent workflows.

## Benchmarking (cpl-bench)

The benchmarking script lives at `benchmarking/cpl_bench/setup_and_run.py`.
It handles CodePlane init, daemon lifecycle, and EVEE evaluation in one command.

```bash
# Basic run (recon experiment, port 7777, no reindex)
python benchmarking/cpl_bench/setup_and_run.py /path/to/target/repo

# Choose experiment
python benchmarking/cpl_bench/setup_and_run.py /path/to/repo --experiment agent-ab

# Custom port and timeout
python benchmarking/cpl_bench/setup_and_run.py /path/to/repo --port 8888 --timeout 180

# Force full reindex (deletes .codeplane/ first)
python benchmarking/cpl_bench/setup_and_run.py /path/to/repo --reindex
```

**Key flags:**
- `--experiment {recon,agent-ab}` — which evaluation to run (default: `recon`)
- `--port PORT` — daemon port (default: `7777`)
- `--timeout SECS` — MCP call timeout (default: `120`)
- `--reindex` — delete `.codeplane/` and rebuild the index from scratch (default: off)

The script automatically kills the daemon when the experiment finishes.

For CodePlane MCP tool usage instructions (recon → resolve → plan → edit → checkpoint workflow),
see [AGENTS.md](../AGENTS.md). The mutation budget rules above are the most critical subset.

<!-- codeplane-instructions -->
## CodePlane MCP: Mandatory Tool Selection

This repository uses CodePlane MCP. **You MUST use CodePlane tools instead of terminal commands.**

Terminal fallback is permitted ONLY when no CodePlane tool exists for the operation.

### What CodePlane Provides

CodePlane maintains a **structural index** of your codebase — definitions, imports,
references, embeddings. This enables task-aware code discovery, semantic diff,
impact-aware test selection, and safe refactoring that terminal commands cannot provide.

### Start Every Task With `recon`

**`recon` is the PRIMARY entry point.** It replaces manual search + read loops.

One call to `recon` returns everything you need to start working:
- **SCAFFOLD** — imports + signatures for top-ranked files (edit targets)
- **LITE** — path + description for peripheral context files
- **repo_map** — structural overview of the entire repository

```
recon(task="<describe the task in natural language>", read_only=<True or False>)
```

**Parameters:**
- `task` (required): Natural language task description. Be specific — include symbol
  names, file paths, or domain terms. The server extracts signals automatically.
- `read_only` (required): Declare task intent. `True` = research/read-only session
  (mutation tools blocked, sha256/edit_tickets skipped). `False` = read-write session
  (full edit workflow enabled). You MUST explicitly declare intent every time.
- `seeds`: Optional list of symbol names to anchor on (e.g., `["IndexCoordinator"]`).
- `pinned_paths`: Optional file paths to force-include (e.g., `["src/core/base.py"]`).
- `expand_reason`: REQUIRED on 2nd consecutive recon call — explain what was missing.
- `gate_token` / `gate_reason`: Required on 3rd+ calls (gated to prevent waste).

**Consecutive call discipline:**
- 1st call: just `task` (and optionally `seeds`/`pinned_paths`)
- 2nd call: MUST include `expand_reason` explaining what was missing
- 3rd+ call: requires `gate_token` from previous response + `gate_reason` (500+ chars)

### After Recon: Resolve Files You Need

After recon identifies relevant files, use `recon_resolve` to get full content:

```
recon_resolve(targets=[{"path": "src/foo.py"}])
```

Returns full file content + `sha256` hash per file (sha256 skipped in read-only mode).
The sha256 is **required** by `refactor_edit` to ensure edits target the correct version.

### Planning and Editing Files

Before editing, declare your edit set:
`refactor_plan(edit_targets=["<candidate_id from recon>"])` → returns `plan_id` + `edit_ticket` per file.

Then apply changes with `refactor_edit`:

```
refactor_edit(plan_id="<plan_id>", edits=[{
    "path": "src/foo.py",
    "edit_ticket": "<ticket from plan>",
    "old_content": "def hello():\n    pass",
    "new_content": "def hello():\n    return 'world'",
    "expected_file_sha256": "<sha256 from recon_resolve>"
}])
```

- Omit `old_content` to create a new file (no plan/ticket needed). Set `delete: true` to delete.
- One call can edit **multiple files** — each edit has its own `path` via `edit_ticket`.

### Edit Budget

- **2 mutation batches** max before checkpoint. Each `refactor_edit` call = 1 batch.
- Batch source + test edits into ONE call. Prefer 1 batch.
- On checkpoint failure: budget RESETS, `fix_plan` with pre-minted tickets returned.
  Batch ALL fixes into one `refactor_edit` call, then retry checkpoint.

### Workflow

1. `recon(task="...", read_only=True/False)` — discover relevant files + declare intent
2. `recon_resolve(targets=[...])` — get full content + sha256
3. `refactor_plan(edit_targets=[...])` — declare edit set, get plan_id + tickets
4. `refactor_edit(plan_id=..., edits=[...])` — make changes (batch into ONE call)
5. `checkpoint(changed_files=[...])` — lint + test + optionally commit

### CRITICAL: After Every Code Change

**`checkpoint(changed_files=[...], commit_message="...")`** — lint → test → commit → push.

Omit `commit_message` to lint+test only (no commit).
Include `push=True` to push after commit (ask the user before pushing).

**FORBIDDEN**: `pytest`, `ruff`, `mypy`, `git add`, `git commit`, `git push` in terminal.

### Reviewing Changes

`semantic_diff(base="main")` for structural overview, then `recon_resolve` changed files to review.

### Required Tool Mapping

| Operation | REQUIRED Tool | FORBIDDEN Alternative |
|-----------|---------------|----------------------|
| Task-aware discovery | `mcp_codeplane-codeplane_recon` | Manual search + read loops |
| Fetch file content | `mcp_codeplane-codeplane_recon_resolve` | `cat`, `head`, `less`, `tail` |
| Edit files | `mcp_codeplane-codeplane_refactor_edit` | `sed`, `echo >>`, `awk`, `tee` |
| Rename symbol | `mcp_codeplane-codeplane_refactor_rename` | Find-and-replace, `sed` |
| Move file | `mcp_codeplane-codeplane_refactor_move` | `mv` + manual import fixup |
| Impact analysis | `mcp_codeplane-codeplane_refactor_impact` | `grep` for references |
| Apply/inspect refactor | `mcp_codeplane-codeplane_refactor_commit` | Manual verification |
| Cancel refactor | `mcp_codeplane-codeplane_refactor_cancel` | — |
| Lint + test + commit | `mcp_codeplane-codeplane_checkpoint` | Running linters/test runners/git directly |
| Structural diff | `mcp_codeplane-codeplane_semantic_diff` | `git diff` for change review |
| Tool/error docs | `mcp_codeplane-codeplane_describe` | Guessing parameter names |

### Before You Edit: Decision Gate

STOP before using `refactor_edit` for multi-file changes:
- Changing a name across files? → `refactor_rename` (NOT refactor_edit + manual fixup)
- Moving a file? → `refactor_move` (NOT refactor_edit + delete)
- Deleting a symbol or file? → `refactor_impact` first

### Refactor: preview → commit/cancel

1. `refactor_rename`/`refactor_move`/`refactor_impact` — preview with `refactor_id`
2. If `verification_required`: `refactor_commit(refactor_id=..., inspect_path=...)` — review low-certainty matches
3. `refactor_commit(refactor_id=...)` to apply, or `refactor_cancel(refactor_id=...)` to discard

### Follow Agentic Hints

`agentic_hint` in responses = **direct instructions for your next action**. Always execute
before proceeding. Also check: `coverage_hint`, `display_to_user`.

If `delivery` = `"sidecar_cache"`, run `agentic_hint` commands to fetch content sections.

### Common Mistakes (Don't Do These)

- **DON'T** skip `recon` and manually search+read — `recon` is faster and more complete
- **DON'T** guess tool parameter names — use `describe(action='tool', name='...')` first
- **DON'T** use `refactor_rename` with file:line:col — pass the symbol NAME only
- **DON'T** skip `checkpoint` after `refactor_edit` — always lint + test your changes
- **DON'T** ignore `agentic_hint` in responses
- **DON'T** use raw `git add` + `git commit` — use `checkpoint` with `commit_message`
- **DON'T** dismiss lint/test failures as "pre-existing" or "not your problem" — fix ALL issues
- **DON'T** use one `refactor_edit` call per file — batch ALL edits into ONE call
- **DON'T** panic on checkpoint failure — budget resets, use the `fix_plan` tickets provided
<!-- /codeplane-instructions -->
