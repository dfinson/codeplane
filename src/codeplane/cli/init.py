"""cpl init command - initialize a repository for CodePlane."""

import asyncio
import hashlib
import json
import math
import sys
from pathlib import Path

import click
from rich.table import Table

from codeplane.config.user_config import (
    RuntimeState,
    UserConfig,
    write_runtime_state,
    write_user_config,
)
from codeplane.core.progress import (
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
| Run tests | `{tool_prefix}_run_test_targets` | `pytest`, `jest` directly |
| Rename symbols | `{tool_prefix}_refactor_rename` | Find-and-replace, `sed` |
| Move files | `{tool_prefix}_refactor_move` | `mv` + manual import fixes |

### Critical Parameter Reference

**{tool_prefix}_read_files**
```
paths: list[str]           # REQUIRED - file paths relative to repo root
ranges: list[RangeParam]   # optional - NOT "line_ranges"
  start_line: int          # 1-indexed, NOT "start"
  end_line: int            # 1-indexed, NOT "end"
```

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
limit: int                 # default 20, NOT "max_results"
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

**{tool_prefix}_git_commit**
```
message: str               # REQUIRED
paths: list[str]           # optional - files to stage before commit
```

**{tool_prefix}_git_stage**
```
action: "add"|"remove"|"all"|"discard"  # REQUIRED
paths: list[str]           # REQUIRED for add/remove/discard (not for "all")
```

**{tool_prefix}_run_test_targets**
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


def _ensure_vscode_mcp_config(repo_root: Path, port: int = 7654) -> bool:
    """Ensure .vscode/mcp.json has the CodePlane server entry.

    Returns True if file was created or modified.
    """
    vscode_dir = repo_root / ".vscode"
    mcp_json_path = vscode_dir / "mcp.json"

    # Derive server name from repo directory name
    repo_name = repo_root.name
    server_name = f"codeplane-{repo_name}"

    expected_config = {
        "command": "npx",
        "args": ["-y", "mcp-remote", f"http://127.0.0.1:{port}/mcp"],
    }

    if mcp_json_path.exists():
        try:
            existing = json.loads(mcp_json_path.read_text())
        except json.JSONDecodeError:
            existing = {}

        servers = existing.get("servers", {})

        # Check if our server entry already exists (by name or equivalent config)
        if server_name in servers:
            return False  # Already configured

        # Also check for legacy "codeplane" entry with same port
        for name, cfg in servers.items():
            if name.startswith("codeplane") and cfg.get("args", [])[-1:] == [
                f"http://127.0.0.1:{port}/mcp"
            ]:
                return False  # Already has equivalent config

        # Add our server entry
        servers[server_name] = expected_config
        existing["servers"] = servers
        mcp_json_path.write_text(json.dumps(existing, indent=2) + "\n")
        return True
    else:
        # Create new mcp.json
        vscode_dir.mkdir(parents=True, exist_ok=True)
        config = {"servers": {server_name: expected_config}}
        mcp_json_path.write_text(json.dumps(config, indent=2) + "\n")
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


def initialize_repo(repo_root: Path, *, force: bool = False, show_cpl_up_hint: bool = True) -> bool:
    """Initialize a repository for CodePlane, returning True on success.

    Args:
        repo_root: Path to the repository root
        force: Overwrite existing .codeplane directory
        show_cpl_up_hint: Show "Run 'cpl up'" hint at end (False when auto-init from cpl up)
    """
    codeplane_dir = repo_root / ".codeplane"
    console = get_console()

    if codeplane_dir.exists() and not force:
        status(f"Already initialized: {codeplane_dir}", style="info")
        status("Use --force to reinitialize", style="info")
        return False

    console.print()
    status(f"Initializing CodePlane in {repo_root}", style="none")
    console.print()

    # If force is set and directory exists, remove it completely to start fresh
    if force and codeplane_dir.exists():
        import shutil

        shutil.rmtree(codeplane_dir)

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

    # Write minimal user config
    config_path = codeplane_dir / "config.yaml"
    write_user_config(config_path, UserConfig())

    # Write runtime state (index_path) - auto-generated, not user-editable
    state_path = codeplane_dir / "state.yaml"
    write_runtime_state(state_path, RuntimeState(index_path=str(index_dir)))

    cplignore_path = codeplane_dir / ".cplignore"
    if not cplignore_path.exists() or force:
        cplignore_path.write_text(get_cplignore_template())

    # Create .gitignore to exclude artifacts from version control per SPEC.md §7.7
    gitignore_path = codeplane_dir / ".gitignore"
    if not gitignore_path.exists() or force:
        gitignore_path.write_text(
            "# Ignore everything except user config files\n"
            "*\n"
            "!.gitignore\n"
            "!config.yaml\n"
            "# state.yaml is auto-generated, do not commit\n"
        )

    # === IDE & Agent Integration ===
    # Derive tool prefix from server name (codeplane-{repo_name} -> mcp_codeplane_{repo_name})
    repo_name = repo_root.name
    tool_prefix = f"mcp_codeplane_{repo_name.replace('-', '_')}"

    # Inject CodePlane instructions into agent instruction files
    modified_agent_files = _inject_agent_instructions(repo_root, tool_prefix)
    if modified_agent_files:
        for f in modified_agent_files:
            status(f"Updated {f} with CodePlane instructions", style="info")

    # Ensure VS Code MCP configuration
    if _ensure_vscode_mcp_config(repo_root):
        status("Created .vscode/mcp.json with CodePlane server", style="info")

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
            if grammar_result.success:
                phase.complete(f"{len(needed)} grammars installed")
            else:
                # Some grammars failed to install
                # Extract language name from package name (e.g., "tree-sitter-powershell" -> "powershell")
                failed_langs = [
                    pkg.replace("tree-sitter-", "").replace("tree_sitter_", "")
                    for pkg in grammar_result.failed_packages
                ]
                failed_list = ", ".join(failed_langs)
                if grammar_result.installed_packages:
                    # Some succeeded, some failed
                    phase.complete(
                        f"{len(grammar_result.installed_packages)} grammars installed",
                        style="green",
                    )
                else:
                    # All needed grammars failed - just show ready
                    phase.complete("Grammars ready")
                # Show user-facing impact message
                phase.add_text(
                    f"Refactoring and symbol search unavailable for {failed_list} files",
                    style="yellow",
                )
        else:
            phase.complete("Grammars ready")

    # === Indexing Phase ===
    from codeplane.index.ops import IndexCoordinator

    db_path = index_dir / "index.db"
    tantivy_path = index_dir / "tantivy"
    tantivy_path.mkdir(exist_ok=True)

    coord = IndexCoordinator(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
    )

    try:
        import time

        start_time = time.time()

        # Run indexing with phase box and live table updates
        with phase_box("Indexing", width=60) as phase:
            # Progress callback to update the live table
            def on_index_progress(indexed: int, total: int, files_by_ext: dict[str, int]) -> None:
                # Update progress bar (scale to 100)
                if total > 0:
                    pct = int(indexed * 100 / total)
                    phase._progress.update(task_id, completed=pct)  # type: ignore[union-attr]
                # Update live table with current file type counts
                if files_by_ext:
                    table = _make_init_extension_table(files_by_ext)
                    phase.set_live_table(table)

            task_id = phase.add_progress("Indexing files", total=100)

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    coord.initialize(on_index_progress=on_index_progress)
                )
            finally:
                loop.close()

            elapsed = time.time() - start_time

            if result.errors:
                for err in result.errors:
                    phase.add_text(f"Error: {err}", style="red")
                return False

            # Final table state and completion message
            phase.set_live_table(None)  # Remove live table
            phase.complete(f"{result.files_indexed} files indexed ({elapsed:.1f}s)")

            # Add final extension breakdown as static content
            if result.files_by_ext:
                phase.add_text("")  # Spacer
                ext_table = _make_init_extension_table(result.files_by_ext)
                phase.add_table(ext_table)
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
@click.option("--force", "-f", is_flag=True, help="Overwrite existing .codeplane directory")
def init_command(path: Path | None, force: bool) -> None:
    """Initialize a repository for CodePlane management.

    Creates .codeplane/ directory with default configuration and builds
    the initial index.

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    from codeplane.cli.utils import find_repo_root

    repo_root = find_repo_root(path)

    if not initialize_repo(repo_root, force=force):
        if not force:
            return  # Already initialized, message printed
        sys.exit(1)  # Errors occurred
