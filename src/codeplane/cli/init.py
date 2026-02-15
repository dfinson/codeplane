"""cpl init command - initialize a repository for CodePlane."""

import asyncio
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import click
import json5
from rich.table import Table

from codeplane.config.user_config import (
    DEFAULT_PORT,
    RuntimeState,
    UserConfig,
    load_user_config,
    write_runtime_state,
    write_user_config,
)
from codeplane.core.progress import (
    PhaseBox,
    get_console,
    phase_box,
    status,
)
from codeplane.templates import get_cplignore_template

# =============================================================================
# Agent Instruction Snippet
# =============================================================================

_CODEPLANE_SNIPPET_MARKER = "<!-- codeplane-instructions -->"


def _make_codeplane_snippet(tool_prefix: str) -> str:
    """Generate the CodePlane instruction snippet with the actual tool prefix.

    Args:
        tool_prefix: The MCP tool prefix (e.g., 'mcp_codeplane_myrepo')
    """
    # Note: Using {{}} to escape braces that should appear literally in output
    # The f-string only interpolates {tool_prefix}
    return f"""
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
| Read files | `{tool_prefix}_read_files` | `cat`, `head`, `less`, `tail` |
| Write/edit files | `{tool_prefix}_write_files` | `sed`, `echo >>`, `awk`, `tee` |
| List directory | `{tool_prefix}_list_files` | `ls`, `find`, `tree` |
| Search code | `{tool_prefix}_search` | `grep`, `rg`, `ag`, `ack` |
| Repository overview | `{tool_prefix}_map_repo` | Manual file traversal |
| All git operations | `{tool_prefix}_git_*` | Raw `git` commands |
| Run linters/formatters | `{tool_prefix}_lint_check` | `ruff`, `black`, `mypy` directly |
| Discover tests | `{tool_prefix}_discover_test_targets` | Manual test file search |
| Impact-aware test selection | `{tool_prefix}_inspect_affected_tests` | Manual import tracing |
| Run tests | `{tool_prefix}_run_test_targets` | `pytest`, `jest` directly |
| Rename symbols | `{tool_prefix}_refactor_rename` | Find-and-replace, `sed` |
| Move files | `{tool_prefix}_refactor_move` | `mv` + manual import fixes |
| Semantic diff | `{tool_prefix}_semantic_diff` | Manual comparison of git diffs |

### Critical Parameter Reference

**{tool_prefix}_describe**
```
action: "tool"|"error"|"capabilities"|"workflows"|"operations"  # REQUIRED
name: str              # required when action="tool" - tool name to describe
code: str              # required when action="error" - error code to look up
path: str              # optional - filter operations by path
limit: int             # default 50 - max operations to return
```

**Introspection tool.** Use to get tool docs, error explanations, list
capabilities, view workflows, or debug recent operations.

**{tool_prefix}_read_files**
```
targets: list[FileTarget]  # REQUIRED - NOT "line_ranges" or "ranges"
  path: str                # REQUIRED - file path this target applies to
  start_line: int          # optional, 1-indexed; must be provided together with end_line
  end_line: int            # optional, 1-indexed; must be provided together with start_line
cursor: str                # optional - pagination cursor from previous response
```

**Response includes:**
- `not_found`: list of paths that don't exist (explicit, not just a count)

**{tool_prefix}_write_files**
```
edits: list[EditParam]     # REQUIRED - array of edits
  path: str                # file path relative to repo root
  action: "create"|"update"|"delete"
  old_content: str         # for update: exact text to find (include enough context)
  new_content: str         # for update: replacement text
dry_run: bool              # optional, default false
```

**{tool_prefix}_search**
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

**{tool_prefix}_list_files**
```
path: str                  # optional - directory to list, NOT "directory"
pattern: str               # optional - glob pattern (e.g., "*.py")
recursive: bool            # default false
limit: int                 # default 200
```

**{tool_prefix}_map_repo**
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

**{tool_prefix}_git_stage_and_commit**
```
message: str               # REQUIRED
paths: list[str]           # REQUIRED - files to stage and commit
allow_empty: bool          # optional, default false
```

**Preferred tool for committing changes.** Stages paths, runs pre-commit hooks,
and commits in one call. If hooks auto-fix files (formatters, linters), changes are
automatically re-staged and retried once.

**{tool_prefix}_git_commit**
```
message: str               # REQUIRED
paths: list[str]           # optional - files to stage before commit
```

**Use only when you need low-level staging control.** When called without `paths`, it
commits what is already staged. When `paths` are provided, those files are staged
before committing; if hooks auto-fix files, they may be re-staged and the commit
retried once.

**{tool_prefix}_git_stage**
```
action: "add"|"remove"|"all"|"discard"  # REQUIRED
paths: list[str]           # REQUIRED for add/remove/discard (not for "all")
```

**{tool_prefix}_git_status**
```
paths: list[str] | None    # optional - paths to check
```

**{tool_prefix}_git_diff**
```
base: str | None           # optional - base ref for comparison
target: str | None         # optional - target ref for comparison
staged: bool               # default false - show staged changes only
cursor: str | None         # optional - pagination cursor
```

**{tool_prefix}_git_log**
```
ref: str                   # default "HEAD"
limit: int                 # default 50
since: str | None          # optional - show commits after date
until: str | None          # optional - show commits before date
paths: list[str] | None    # optional - filter by paths
cursor: str | None         # optional - pagination cursor
```

**{tool_prefix}_git_push**
```
remote: str                # default "origin"
force: bool                # default false
```

**{tool_prefix}_git_inspect**
```
action: "show"|"blame"     # REQUIRED
ref: str                   # default "HEAD" - commit ref (for show)
path: str | None           # required for blame
start_line: int | None     # optional - for blame range
end_line: int | None       # optional - for blame range
cursor: str | None         # optional - pagination cursor
limit: int                 # default 100 - max lines for blame
```

**{tool_prefix}_run_test_targets**
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

**{tool_prefix}_discover_test_targets**
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

**{tool_prefix}_inspect_affected_tests**
```
changed_files: list[str]   # REQUIRED - changed file paths to analyze
```

**Detailed import graph inspection.** Returns per-test-file match info with
confidence levels, changed modules, coverage gaps, and agentic hints. Use this
to review uncertain matches before deciding which tests to run.

**{tool_prefix}_semantic_diff**
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

**{tool_prefix}_lint_check**
```
paths: list[str] | None    # optional - paths to lint (default: entire repo)
tools: list[str] | None    # optional - specific tool IDs to run
categories: list[str] | None  # optional - "linter", "formatter", "typechecker"
dry_run: bool              # default false - when true, report without fixing
```

**Applies auto-fixes by default.** Set `dry_run=true` to only report issues.

**{tool_prefix}_lint_tools**
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

**{tool_prefix}_refactor_rename**
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

**{tool_prefix}_refactor_delete**
```
target: str                # REQUIRED - symbol name or file path to delete
include_comments: bool     # default true - include comment references
```

Returns preview with dependency analysis. Use to safely remove dead code —
the preview shows what depends on the symbol before deletion.

**{tool_prefix}_refactor_move**
```
from_path: str             # REQUIRED - source file path
to_path: str               # REQUIRED - destination file path
include_comments: bool     # default true - include comment references
```

Moves a file/module and updates all imports. Returns preview with `refactor_id`.

**{tool_prefix}_refactor_inspect**
```
refactor_id: str           # REQUIRED - ID from rename/move/delete preview
path: str                  # REQUIRED - file to inspect
context_lines: int         # default 2 - lines of context around matches
```

Review low-certainty matches with surrounding context before applying.

**{tool_prefix}_refactor_apply**
```
refactor_id: str           # REQUIRED - ID from preview to apply
```

**{tool_prefix}_refactor_cancel**
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
"""


def _inject_agent_instructions(repo_root: Path, tool_prefix: str) -> list[str]:
    """Inject CodePlane snippet into agent instruction files.

    Args:
        repo_root: Path to the repository root
        tool_prefix: The MCP tool prefix (e.g., 'mcp_codeplane_myrepo')

    Returns list of files that were created or updated.
    """
    modified: list[str] = []
    snippet = _make_codeplane_snippet(tool_prefix)

    # Target files for agent instructions
    targets = [
        repo_root / "AGENTS.md",
        repo_root / ".github" / "copilot-instructions.md",
    ]

    for target in targets:
        if target.exists():
            content = target.read_text()
            # Check if snippet already present
            if _CODEPLANE_SNIPPET_MARKER in content:
                # Replace existing snippet with updated one
                import re

                new_content = re.sub(
                    r"<!-- codeplane-instructions -->.*?<!-- /codeplane-instructions -->",
                    snippet.strip(),
                    content,
                    flags=re.DOTALL,
                )
                if new_content != content:
                    target.write_text(new_content)
                    modified.append(str(target.relative_to(repo_root)))
            else:
                # Append snippet
                new_content = content.rstrip() + "\n" + snippet
                target.write_text(new_content)
                modified.append(str(target.relative_to(repo_root)))
        else:
            # Create file with snippet
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.name == "AGENTS.md":
                target.write_text(
                    "# Agent Instructions\n\n"
                    "Instructions for AI coding agents working in this repository.\n" + snippet
                )
            else:  # copilot-instructions.md
                target.write_text(
                    "# Copilot Instructions\n\n"
                    "Project-specific instructions for GitHub Copilot.\n" + snippet
                )
            modified.append(str(target.relative_to(repo_root)))

    return modified


# =============================================================================
# VS Code MCP Configuration
# =============================================================================


def _get_mcp_server_name(repo_root: Path) -> str:
    """Get the normalized MCP server name for a repo."""
    repo_name = repo_root.name
    normalized = repo_name.lower().replace(".", "_").replace("-", "_")
    return f"codeplane-{normalized}"


def _ensure_vscode_mcp_config(repo_root: Path, port: int) -> tuple[bool, str]:
    """Ensure .vscode/mcp.json has the CodePlane server entry with static port.

    Creates or updates the MCP server entry with the actual port number.
    Call sync_vscode_mcp_port() from 'cpl up' to update port if changed.

    Returns tuple of (was_modified, server_name).
    """
    vscode_dir = repo_root / ".vscode"
    mcp_json_path = vscode_dir / "mcp.json"
    server_name = _get_mcp_server_name(repo_root)

    expected_url = f"http://127.0.0.1:{port}/mcp"
    expected_config: dict[str, Any] = {
        "type": "http",
        "url": expected_url,
    }

    if mcp_json_path.exists():
        content = mcp_json_path.read_text()
        try:
            existing: dict[str, Any] = json5.loads(content)
        except ValueError:
            # Unparseable JSONC — don't risk overwriting existing servers
            status(
                "Warning: .vscode/mcp.json is not valid JSON(C), skipping update",
                style="warning",
            )
            return False, server_name

        servers = existing.get("servers", {})

        # Check if our server entry already exists with correct config
        if server_name in servers:
            current_url = servers[server_name].get("url", "")

            # If URL matches exactly, no change needed
            if current_url == expected_url:
                return False, server_name

            # Update with new native HTTP config
            servers[server_name] = expected_config
        else:
            # Add new server entry
            servers[server_name] = expected_config

        existing["servers"] = servers
        output = json.dumps(existing, indent=2) + "\n"
        mcp_json_path.write_text(output)
        return True, server_name
    else:
        # Create new mcp.json
        vscode_dir.mkdir(parents=True, exist_ok=True)
        config = {"servers": {server_name: expected_config}}
        output = json.dumps(config, indent=2) + "\n"
        mcp_json_path.write_text(output)
        return True, server_name


def sync_vscode_mcp_port(repo_root: Path, port: int) -> bool:
    """Update port in .vscode/mcp.json if it differs from configured port.

    Called by 'cpl up' to ensure mcp.json matches the running server port.
    Returns True if file was modified.
    """
    mcp_json_path = repo_root / ".vscode" / "mcp.json"
    if not mcp_json_path.exists():
        # Create mcp.json if it doesn't exist
        return _ensure_vscode_mcp_config(repo_root, port)[0]

    server_name = _get_mcp_server_name(repo_root)
    expected_url = f"http://127.0.0.1:{port}/mcp"

    content = mcp_json_path.read_text()
    try:
        existing: dict[str, Any] = json5.loads(content)
    except ValueError:
        # Unparseable JSONC — don't risk overwriting existing servers
        return False

    servers = existing.get("servers", {})
    if server_name not in servers:
        # Our server entry doesn't exist, add it
        servers[server_name] = {
            "type": "http",
            "url": expected_url,
        }
        existing["servers"] = servers
        output = json.dumps(existing, indent=2) + "\n"
        mcp_json_path.write_text(output)
        return True

    current_url = servers[server_name].get("url", "")

    if current_url == expected_url:
        return False

    # Update config to native HTTP format
    # Preserve existing settings (headers, env, etc.) while updating type/url
    existing_entry = servers.get(server_name, {})
    if isinstance(existing_entry, dict):
        existing_entry["type"] = "http"
        existing_entry["url"] = expected_url
        servers[server_name] = existing_entry
    else:
        servers[server_name] = {"type": "http", "url": expected_url}
    existing["servers"] = servers
    output = json.dumps(existing, indent=2) + "\n"
    mcp_json_path.write_text(output)
    return True


# =============================================================================
# Filesystem Helpers
# =============================================================================


def _is_cross_filesystem(path: Path) -> bool:
    """Detect if path is on a cross-filesystem mount (WSL /mnt/*, network drives, etc.)."""
    resolved = path.resolve()
    path_str = str(resolved)
    # WSL accessing Windows filesystem
    if path_str.startswith("/mnt/") and len(path_str) > 5 and path_str[5].isalpha():
        return True
    # Common network/remote mounts
    return path_str.startswith(("/run/user/", "/media/", "/net/"))


def _get_xdg_index_dir(repo_root: Path) -> Path:
    """Get XDG-compliant index directory for a repo."""
    xdg_data = Path.home() / ".local" / "share" / "codeplane" / "indices"
    repo_hash = hashlib.sha256(str(repo_root.resolve()).encode()).hexdigest()[:12]
    return xdg_data / repo_hash


def initialize_repo(
    repo_root: Path,
    *,
    reindex: bool = False,
    show_cpl_up_hint: bool = True,
    port: int | None = None,
) -> bool:
    """Initialize a repository for CodePlane, returning True on success.

    Args:
        repo_root: Path to the repository root
        reindex: Wipe and rebuild the entire index from scratch
        show_cpl_up_hint: Show "Run 'cpl up'" hint at end (False when auto-init from cpl up)
        port: Override port (persisted to config.yaml). If None, preserves existing or uses default.
    """
    codeplane_dir = repo_root / ".codeplane"
    console = get_console()

    if codeplane_dir.exists() and not reindex:
        status(f"Already initialized: {codeplane_dir}", style="info")
        status("Use --reindex to rebuild the index", style="info")
        return False

    console.print()
    status(f"Initializing CodePlane in {repo_root}", style="none")
    console.print()

    # Determine port: CLI override > existing config > default
    config_path = codeplane_dir / "config.yaml"
    if port is not None:
        # Explicit port override from CLI
        final_port = port
    elif config_path.exists():
        # Preserve existing config port (for reindex without --port)
        existing_config = load_user_config(config_path)
        final_port = existing_config.port
    else:
        # Fresh init with no port specified
        final_port = DEFAULT_PORT

    # If reindex is set, remove existing data completely to start fresh
    if reindex:
        import shutil

        if codeplane_dir.exists():
            shutil.rmtree(codeplane_dir)
        # Also clear XDG index directory (for cross-filesystem setups like WSL)
        xdg_index_dir = _get_xdg_index_dir(repo_root)
        if xdg_index_dir.exists():
            shutil.rmtree(xdg_index_dir)

    codeplane_dir.mkdir(exist_ok=True)

    # Determine index storage location before writing config
    # Cross-filesystem paths (WSL /mnt/*) need index on native filesystem
    index_dir: Path
    if _is_cross_filesystem(repo_root):
        index_dir = _get_xdg_index_dir(repo_root)
        index_dir.mkdir(parents=True, exist_ok=True)
        status(
            f"Cross-filesystem detected, storing index at: {index_dir}",
            style="info",
        )
    else:
        index_dir = codeplane_dir

    # Write user config
    write_user_config(config_path, UserConfig(port=final_port))

    # Write runtime state (index_path) - auto-generated, not user-editable
    state_path = codeplane_dir / "state.yaml"
    write_runtime_state(state_path, RuntimeState(index_path=str(index_dir)))

    cplignore_path = codeplane_dir / ".cplignore"
    if not cplignore_path.exists() or reindex:
        cplignore_path.write_text(get_cplignore_template())

    # Create .gitignore to exclude artifacts from version control per SPEC.md §7.7
    gitignore_path = codeplane_dir / ".gitignore"
    if not gitignore_path.exists() or reindex:
        gitignore_path.write_text(
            "# Ignore everything except user config files\n"
            "*\n"
            "!.gitignore\n"
            "!config.yaml\n"
            "# state.yaml is auto-generated, do not commit\n"
        )

    # === IDE & Agent Integration ===
    # Ensure VS Code MCP configuration with static port (returns server_name)
    mcp_modified, server_name = _ensure_vscode_mcp_config(repo_root, final_port)
    if mcp_modified:
        status("Created .vscode/mcp.json with CodePlane server", style="info")

    # Derive tool prefix from server_name: VS Code creates tools as mcp_{server_name}_{tool}
    # server_name is already normalized (lowercase, underscores)
    tool_prefix = f"mcp_{server_name}"

    # Inject CodePlane instructions into agent instruction files
    modified_agent_files = _inject_agent_instructions(repo_root, tool_prefix)
    if modified_agent_files:
        for f in modified_agent_files:
            status(f"Updated {f} with CodePlane instructions", style="info")

    # === Discovery Phase ===
    from codeplane.index._internal.grammars import (
        get_needed_grammars,
        install_grammars,
        scan_repo_languages,
    )

    with phase_box("Discovery", width=60) as phase:
        # Step 1: Scan languages
        task_id = phase.add_progress("Scanning", total=100)
        languages = scan_repo_languages(repo_root)
        phase.advance(task_id, 100)
        # Use .value to get string name, not enum repr
        lang_names = ", ".join(sorted(lang.value for lang in languages)) if languages else "none"
        phase.complete(f"{len(languages)} languages: {lang_names}")

        # Step 2: Install grammars if needed
        needed = get_needed_grammars(languages)
        if needed:
            task_id = phase.add_progress("Installing grammars", total=len(needed))
            grammar_result = install_grammars(needed, quiet=True, status_fn=None)
            phase.advance(task_id, len(needed))
            if grammar_result.installed_packages:
                # Log which grammars were installed
                installed_langs = [
                    pkg.replace("tree-sitter-", "").replace("tree_sitter_", "")
                    for pkg in grammar_result.installed_packages
                ]
                phase.complete(f"Installed: {', '.join(installed_langs)}")
            else:
                phase.complete("Grammars ready")

    # === Lexical Indexing Phase ===
    from codeplane.index.ops import IndexCoordinator

    db_path = index_dir / "index.db"
    tantivy_path = index_dir / "tantivy"
    tantivy_path.mkdir(exist_ok=True)

    coord = IndexCoordinator(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
    )

    # Shared state for phase transitions
    indexing_state: dict[str, object] = {
        "indexing_done": False,
        "files_indexed": 0,
        "files_by_ext": {},
    }
    # Track resolution phase box and task IDs
    resolution_phase: PhaseBox | None = None
    refs_task_id: Any = None
    types_task_id: Any = None
    indexing_elapsed = 0.0

    try:
        import time

        start_time = time.time()

        # Phase box 1: Indexing (unified file processing)
        indexing_phase = phase_box("Indexing", width=60)
        indexing_phase.__enter__()
        indexing_task_id = indexing_phase.add_progress("Indexing files", total=100)

        def on_index_progress(
            indexed: int, total: int, files_by_ext: dict[str, int], progress_phase: str
        ) -> None:
            nonlocal resolution_phase, refs_task_id, types_task_id, indexing_elapsed

            if progress_phase == "indexing":
                # Update indexing phase box
                pct = int(indexed / total * 100) if total > 0 else 0
                indexing_phase._progress.update(indexing_task_id, completed=pct)  # type: ignore[union-attr]

                if files_by_ext:
                    table = _make_init_extension_table(files_by_ext)
                    indexing_phase.set_live_table(table)

                # Store latest state
                indexing_state["files_indexed"] = indexed
                indexing_state["files_by_ext"] = files_by_ext

            elif progress_phase in ("resolving_cross_file", "resolving_refs", "resolving_types"):
                # First resolution callback — close indexing box, open resolution box
                if not indexing_state["indexing_done"]:
                    indexing_state["indexing_done"] = True
                    indexing_elapsed = time.time() - start_time

                    # Finalize indexing box
                    indexing_phase.set_live_table(None)
                    files = indexing_state["files_indexed"]
                    indexing_phase.complete(f"{files} files ({indexing_elapsed:.1f}s)")
                    if indexing_state["files_by_ext"]:
                        indexing_phase.add_text("")
                        ext_table = _make_init_extension_table(indexing_state["files_by_ext"])  # type: ignore[arg-type]
                        indexing_phase.add_table(ext_table)
                    indexing_phase.__exit__(None, None, None)

                    # Open resolution phase box
                    resolution_phase = phase_box("Resolution", width=60)
                    resolution_phase.__enter__()

                if progress_phase == "resolving_refs":
                    if resolution_phase is not None:
                        if refs_task_id is None:
                            refs_task_id = resolution_phase.add_progress(
                                "Resolving imports", total=100
                            )
                        pct = int(indexed / total * 100) if total > 0 else 0
                        resolution_phase._progress.update(refs_task_id, completed=pct)  # type: ignore[union-attr]

                elif progress_phase == "resolving_types" and resolution_phase is not None:
                    if types_task_id is None:
                        types_task_id = resolution_phase.add_progress("Resolving types", total=100)
                    pct = int(indexed / total * 100) if total > 0 else 0
                    resolution_phase._progress.update(types_task_id, completed=pct)  # type: ignore[union-attr]

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(coord.initialize(on_index_progress=on_index_progress))
        finally:
            loop.close()

        # Handle case where there were no resolution phases (shouldn't happen normally)
        if not indexing_state["indexing_done"]:
            indexing_elapsed = time.time() - start_time
            indexing_phase.set_live_table(None)
            indexing_phase.complete(f"{result.files_indexed} files ({indexing_elapsed:.1f}s)")
            if result.files_by_ext:
                indexing_phase.add_text("")
                ext_table = _make_init_extension_table(result.files_by_ext)
                indexing_phase.add_table(ext_table)
            indexing_phase.__exit__(None, None, None)

        # Close resolution phase box if it was opened
        if resolution_phase is not None:
            total_elapsed = time.time() - start_time
            resolution_elapsed = total_elapsed - indexing_elapsed
            resolution_phase.complete(f"Done ({resolution_elapsed:.1f}s)")
            resolution_phase.__exit__(None, None, None)

        if result.errors:
            for err in result.errors:
                status(f"Error: {err}", style="error")
            return False

    finally:
        coord.close()

    # Final config confirmation
    console.print()
    rel_config_path = config_path.relative_to(repo_root)
    status(f"Config created at {rel_config_path}", style="success")

    if show_cpl_up_hint:
        console.print()
        status("Ready. Run 'cpl up' to start the server.", style="none")

    return True


def _make_init_extension_table(files_by_ext: dict[str, int]) -> Table:
    """Create extension breakdown table for init output."""
    sorted_exts = sorted(files_by_ext.items(), key=lambda x: -x[1])
    if not sorted_exts:
        return Table(show_header=False, box=None)

    max_count = sorted_exts[0][1]
    max_sqrt = math.sqrt(max_count) if max_count > 0 else 1

    table = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False)
    table.add_column("ext", style="cyan", width=8)
    table.add_column("count", style="white", justify="right", width=4)
    table.add_column("bar", width=20)

    for ext, count in sorted_exts[:8]:
        bar_width = max(1, int(math.sqrt(count) / max_sqrt * 20)) if max_sqrt > 0 else 1
        bar = f"[green]{'━' * bar_width}[/green][dim]{'━' * (20 - bar_width)}[/dim]"
        table.add_row(ext, str(count), bar)

    rest = sorted_exts[8:]
    if rest:
        rest_count = sum(c for _, c in rest)
        bar_width = max(1, int(math.sqrt(rest_count) / max_sqrt * 20)) if max_sqrt > 0 else 1
        bar = f"[dim green]{'━' * bar_width}[/dim green][dim]{'━' * (20 - bar_width)}[/dim]"
        table.add_row("other", str(rest_count), bar, style="dim")

    return table


@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
@click.option(
    "-r", "--reindex", is_flag=True, help="Wipe and rebuild the entire index from scratch"
)
@click.option("--port", "-p", type=int, help="Server port (persisted to config.yaml)")
def init_command(path: Path | None, reindex: bool, port: int | None) -> None:
    """Initialize a repository for CodePlane management.

    Creates .codeplane/ directory with default configuration and builds
    the initial index.

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    from codeplane.cli.utils import find_repo_root

    repo_root = find_repo_root(path)

    if not initialize_repo(repo_root, reindex=reindex, port=port):
        if not reindex:
            return  # Already initialized, message printed
        sys.exit(1)  # Errors occurred
