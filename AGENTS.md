# Agent Instructions

Instructions for AI coding agents working in this repository.

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

### Critical Parameter Reference

**mcp_codeplane-codeplane_read_files**
```
paths: list[str]           # REQUIRED - file paths relative to repo root
targets: list[FileTarget]  # optional - NOT "line_ranges" or "ranges"
  path: str                # REQUIRED - file path this target applies to
  start_line: int          # 1-indexed, NOT "start"
  end_line: int            # 1-indexed, NOT "end"
```

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
limit: int                 # default 20, NOT "max_results"
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
<!-- /codeplane-instructions -->
