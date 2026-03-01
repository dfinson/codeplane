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

When the response exceeds the inline cap (sidecar delivery), the envelope includes
`resolved_meta` — an inline array of per-file metadata (path, candidate_id,
line_count, file_sha256) so you can proceed to `refactor_plan` without parsing
the sidecar.  Retrieve file content from the sidecar via the jq command in
`agentic_hint` when you need `old_content` for edits.

### Planning Edits

Before editing existing files, declare your edit set with `refactor_plan`:

```
refactor_plan(
    edit_targets=["<candidate_id from recon>"],
    expected_edit_calls=1,
    description="Brief description of what you're changing"
)
```

Returns a `plan_id` and an `edit_ticket` per file. Both are **required** by `refactor_edit`.

### Editing Files

Use `refactor_edit` for all file modifications:

```
refactor_edit(plan_id="<plan_id from refactor_plan>", edits=[{
    "path": "src/foo.py",
    "edit_ticket": "<edit_ticket from refactor_plan>",
    "old_content": "def hello():
    pass",
    "new_content": "def hello():
    return 'world'",
    "expected_file_sha256": "<sha256 from recon_resolve>"
}])
```

- `plan_id` and `edit_ticket` are required for updating existing files
- Find-and-replace: specify `old_content` to find and `new_content` to replace it with
- Optional `start_line`/`end_line` hints to disambiguate if old_content appears multiple times
- Omit `old_content` (or set to null) to create a new file (no plan or ticket needed)
- Set `delete: true` to delete a file

### Workflow

1. `recon(task="...", read_only=True/False)` — discover relevant files + declare intent
2. `recon_resolve(targets=[...])` — get full content (+ sha256/edit_tickets if read_only=False)
3. If read_only=False: `refactor_plan(edit_targets=[...])` — declare edit set, get plan_id + tickets
4. `refactor_edit(plan_id=..., edits=[...])` — make changes (include edit_ticket per file)
5. `checkpoint(changed_files=[...])` — ALWAYS called:
   - read_only=True: verifies clean working tree (no lint/test/commit)
   - read_only=False: lint + test + optionally commit

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

### Reviewing Multi-Domain Changes

`semantic_diff` automatically classifies changes by directory domain and includes
a `domains` key when changes span multiple subsystems. Use this for structured review:

1. `semantic_diff(base="main")` — get diff with domain groupings
2. Read `domains` — each entry has `name`, `files`, `review_priority`, risk counts
3. For each domain (priority order), call `recon(task="review <domain> changes", read_only=True, pinned_paths=<domain files>)`
4. `recon_resolve` to read changed files in context
5. Focus on breaking changes and cross-domain edges first
6. Summarize findings per domain

The `cross_domain_edges` key (when present) shows import relationships between
domains — review these interfaces for compatibility.

### Common Mistakes (Don't Do These)

- **DON'T** skip `recon` and manually search+read — `recon` is faster and more complete
- **DON'T** guess tool parameter names — use `describe(action='tool', name='...')` first
- **DON'T** use `refactor_rename` with file:line:col — pass the symbol NAME only
- **DON'T** skip `checkpoint` after `refactor_edit` — always lint + test your changes
- **DON'T** ignore `agentic_hint` in responses
- **DON'T** use raw `git add` + `git commit` — use `checkpoint` with `commit_message`
- **DON'T** dismiss lint/test failures as "pre-existing" or "not your problem" — fix ALL issues
<!-- /codeplane-instructions -->
