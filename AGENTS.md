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
recon(task="<describe the task in natural language>")
```

**Parameters:**
- `task` (required): Natural language task description. Be specific — include symbol
  names, file paths, or domain terms. The server extracts signals automatically.
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

Returns full file content + `sha256` hash per file.
The sha256 is **required** by `refactor_edit` to ensure edits target the correct version.

### Editing Files

Use `refactor_edit` for all file modifications:

```
refactor_edit(edits=[{
    "path": "src/foo.py",
    "old_content": "def hello():
    pass",
    "new_content": "def hello():
    return 'world'",
    "expected_file_sha256": "<sha256 from recon_resolve>"
}])
```

- Find-and-replace: specify `old_content` to find and `new_content` to replace it with
- Optional `start_line`/`end_line` hints to disambiguate if old_content appears multiple times
- Set `old_content` to empty string to create a new file
- Set `delete: true` to delete a file

### Workflow

1. `recon(task="...")` — discover relevant files
2. `recon_resolve(targets=[...])` — get full content + sha256 for files you need
3. `refactor_edit(edits=[...])` — make changes
4. `checkpoint(changed_files=[...], commit_message="...")` — lint + test + commit

### CRITICAL: After Every Code Change

**`checkpoint(changed_files=[...], commit_message="...")`** — lint → test → commit → push.

Omit `commit_message` to lint+test only (no commit).
Include `push=True` to push after commit (ask the user before pushing).

**FORBIDDEN**: `pytest`, `ruff`, `mypy`, `git add`, `git commit`, `git push` in terminal.

### Reviewing Changes

1. `semantic_diff(base="main")` — structural overview of all changes vs main
2. `recon_resolve` on changed files — review each change in context
3. `checkpoint(changed_files=[...])` — lint + affected tests

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

### CRITICAL: Follow Agentic Hints

Responses may include `agentic_hint` — these are **direct instructions for your next
action**, not suggestions. Always read and execute them before proceeding.

Also check for: `coverage_hint`, `display_to_user`.

### Large Responses (Sidecar Delivery)

When a response exceeds the inline budget, it is cached server-side and you receive
terminal commands instead of the full payload. Run those commands to retrieve sections.
Check `delivery` in the response: `"inline"` = full payload present, `"sidecar_cache"` =
run the commands in `agentic_hint` to fetch content.

### Common Mistakes (Don't Do These)

- **DON'T** skip `recon` and manually search+read — `recon` is faster and more complete
- **DON'T** guess tool parameter names — use `describe(action='tool', name='...')` first
- **DON'T** use `refactor_rename` with file:line:col — pass the symbol NAME only
- **DON'T** skip `checkpoint` after `refactor_edit` — always lint + test your changes
- **DON'T** ignore `agentic_hint` in responses
- **DON'T** use raw `git add` + `git commit` — use `checkpoint` with `commit_message`
- **DON'T** dismiss lint/test failures as "pre-existing" or "not your problem" — fix ALL issues
<!-- /codeplane-instructions -->
