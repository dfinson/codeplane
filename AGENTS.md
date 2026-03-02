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
    "old_content": "def hello():
    pass",
    "new_content": "def hello():
    return 'world'",
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
