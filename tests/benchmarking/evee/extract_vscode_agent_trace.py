#!/usr/bin/env python3
"""
extract_vscode_agent_trace.py — Extract a pseudo-trace from VS Code Copilot Chat
/ Agent sessions.

DIRECTORIES SCANNED (in priority order):
  1. ~/.vscode-server/data/User/          (VS Code Remote / WSL server)
  2. ~/.config/Code/User/                 (Linux local)
  3. ~/Library/Application Support/Code/User/  (macOS local)
  4. %APPDATA%/Code/User/                 (Windows local)

Within each, we scan:
  - workspaceStorage/<hash>/GitHub.copilot-chat/chat-session-resources/<session-uuid>/
       → Tool call result directories named: toolu_<id>__vscode-<epoch_ms>
       → Each contains content.json or content.txt (tool results)
  - globalStorage/github.copilot-chat/    (global Copilot Chat metadata)
  - logs/<date>/exthost<N>/GitHub.copilot-chat/GitHub Copilot Chat.log
       → Request IDs, timing, model endpoints, errors, version info

ASSUMPTIONS:
  - Tool call directories follow the naming convention: <tool_call_id>__vscode-<epoch_ms>
  - The "toolu_" prefix indicates Claude-sourced tool calls; "toolu_vrtx_" indicates Vertex.
  - content.json files contain structured MCP-style tool results.
  - content.txt files contain terminal output (run_in_terminal results).
  - Session UUIDs are directory names matching UUID4 pattern.
  - Chat turn text (user prompts, assistant markdown) is stored client-side
    (state.vscdb SQLite) and may NOT be available on remote servers.
  - The Copilot Chat log provides request-level metadata, not per-turn content.
  - Log timestamps are assumed to be local server time (no TZ in log format);
    tool directory epoch_ms timestamps are UTC-based Unix epoch.

KNOWN LIMITATIONS:
  - On remote/WSL servers, user/assistant message text is NOT available (stored client-side).
  - Turn-level input_chars / output_chars are only available if state.vscdb is found.
  - Tool call arguments (input to tools) are NOT persisted — only results.
  - Timestamp in tool directory names may represent registration time, not execution time.
  - Token estimation is character-based heuristic (chars / 4 by default).
  - Schema may change between VS Code / Copilot Chat versions without notice.
  - Multiple exthost log directories may exist; we pick the most recent by mtime.
"""

import argparse
import json
import logging
import math
import os
import platform
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_underscore_case(s: str) -> str:
    """Convert an arbitrary string to lowercase_underscore form.

    Strips leading/trailing whitespace, replaces runs of non-alphanumeric
    characters with a single underscore, and lowercases everything.
    """
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")

# ---------------------------------------------------------------------------
# Parser version — increment on each structural change to the output schema
# ---------------------------------------------------------------------------
PARSER_VERSION = "1.0.0"

# Default prefix used to identify CodePlane MCP tool names
DEFAULT_CODEPLANE_PREFIX = "codeplane_"

# ---------------------------------------------------------------------------
# Logging — all diagnostic output goes to stderr
# ---------------------------------------------------------------------------
log = logging.getLogger("trace_extractor")
log.setLevel(logging.DEBUG)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
log.addHandler(_handler)

# ---------------------------------------------------------------------------
# Constants & patterns
# ---------------------------------------------------------------------------
UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
# Tool call directory naming: <tool_call_id>__vscode-<epoch_ms>
TOOL_DIR_RE = re.compile(
    r"^(toolu_(?:vrtx_)?[A-Za-z0-9]+)__vscode-(\d+)$"
)
# Log line timestamp format: "2026-02-17 15:40:46.582 [info] ..."
LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\[(\w+)\]\s+(.*)$"
)
# Log: "request done: requestId: [UUID] model deployment ID: [...]"
REQUEST_DONE_RE = re.compile(
    r"request done: requestId: \[([^\]]+)\] model deployment ID: \[([^\]]*)\]"
)
# Log: "message N returned. finish reason: [stop]"
MESSAGE_RETURNED_RE = re.compile(
    r"message (\d+) returned\. finish reason: \[(\w+)\]"
)
# Log: ccreq:<hex>.<tag> | status | model -> deployment | Xms | [wrapper]
CCREQ_RE = re.compile(
    r"ccreq:([a-f0-9]+)\.(\S+)\s+\|\s+(\w+)\s+\|\s+(\S+?)(?:\s*->\s*(\S+))?\s+\|\s+([\d.]+)ms"
)
# Log: "[fetchCompletions] Request UUID at URL finished with STATUS status after Xms"
FETCH_COMPLETIONS_RE = re.compile(
    r"\[fetchCompletions\] Request ([a-f0-9\-]+) at .+ finished with (\d+) status after ([\d.]+)ms"
)
# Log: "Copilot Chat: X.Y.Z, VS Code: A.B.C"
VERSION_RE = re.compile(r"Copilot Chat: ([\d.]+), VS Code: ([\d.]+)")
# Log: "Logged in as <user>"
LOGGED_IN_RE = re.compile(r"Logged in as (\S+)")
# Log: "copilot token sku: <sku>"
SKU_RE = re.compile(r"copilot token sku: (.+)")


# ---------------------------------------------------------------------------
# Native tool subkind inference
# ---------------------------------------------------------------------------
_SUBKIND_BY_TOOL_NAME: dict[str, str] = {
    # --- Terminal ---
    "run_in_terminal": "terminal",
    "get_terminal_output": "terminal",
    # --- Agent planning ---
    "manage_todo_list": "planning",
    "ask_questions": "planning",
    "tool_search_tool_regex": "planning",
    # --- CodePlane MCP: read ---
    "read_source": "read_file",
    "read_file_full": "read_file",
    "read_scaffold": "read_file",
    "read_file": "read_file",
    "search": "search",
    "list_files": "read_file",
    "map_repo": "read_file",
    "describe": "read_file",
    # --- CodePlane MCP: edit ---
    "write_source": "edit",
    "write_files": "edit",
    # --- CodePlane MCP: git ---
    "semantic_diff": "git",
    "git_status": "git",
    "git_log": "git",
    "git_diff": "git",
    "git_commit": "git",
    "git_stage": "git",
    "git_stage_and_commit": "git",
    "git_push": "git",
    "git_pull": "git",
    "git_branch": "git",
    "git_checkout": "git",
    "git_stash": "git",
    "git_reset": "git",
    "git_merge": "git",
    "git_rebase": "git",
    "git_remote": "git",
    "git_inspect": "git",
    "git_history": "git",
    "git_submodule": "git",
    "git_worktree": "git",
    # --- CodePlane MCP: lint ---
    "lint_check": "lint",
    "lint_tools": "lint",
    # --- CodePlane MCP: tests ---
    "run_test_targets": "tests",
    "discover_test_targets": "tests",
    "inspect_affected_tests": "tests",
    "get_test_run_status": "tests",
    "cancel_test_run": "tests",
    # --- CodePlane MCP: refactor ---
    "refactor_rename": "edit",
    "refactor_move": "edit",
    "refactor_delete": "edit",
    "refactor_apply": "edit",
    "refactor_cancel": "edit",
    "refactor_inspect": "read_file",
    "refactor_impact": "read_file",
    # --- CodePlane MCP: budget ---
    "reset_budget": "planning",
    # --- GitHub (non-MCP built-in) ---
    "github_api": "git",
    "github_repo": "git",
    # --- VS Code built-in: file editing ---
    "editFiles": "edit",
    "edit_file": "edit",
    "createFile": "edit",
    "create_file": "edit",
    "deleteFile": "edit",
    "delete_file": "edit",
    "renameFile": "edit",
    "rename_file": "edit",
    "insertEdit": "edit",
    "insert_edit": "edit",
    "replaceInFile": "edit",
    "replace_in_file": "edit",
    # --- VS Code built-in: file reading ---
    "readFile": "read_file",
    "listDirectory": "read_file",
    "list_directory": "read_file",
    "openFile": "read_file",
    "open_file": "read_file",
    "getWorkspaceStructure": "read_file",
    "get_workspace_structure": "read_file",
    # --- VS Code built-in: search ---
    "searchFiles": "search",
    "search_files": "search",
    "findTextInFiles": "search",
    "find_text_in_files": "search",
    "findInFiles": "search",
    "find_in_files": "search",
    # --- VS Code built-in: environment ---
    "configure_python_environment": "environment",
    "install_python_packages": "environment",
    "get_python_environment_details": "environment",
    "get_python_executable_details": "environment",
    "configure_python_notebook": "environment",
    "configure_non_python_notebook": "environment",
    "restart_notebook_kernel": "environment",
    # --- VS Code built-in: browser ---
    "fetch_webpage": "browser",
    "open_simple_browser": "browser",
    # --- VS Code built-in: commands ---
    "run_vscode_command": "vscode_cmd",
    "install_extension": "vscode_cmd",
    "vscode_searchExtensions_internal": "vscode_cmd",
    "create_new_workspace": "vscode_cmd",
    "get_project_setup_info": "vscode_cmd",
    "create_and_run_task": "vscode_cmd",
    "get_vscode_api": "vscode_cmd",
    # --- VS Code built-in: tests ---
    "test_failure": "tests",
    # --- GitHub MCP overrides (short names that differ from default "git") ---
    "search_code": "search",
    "search_issues": "search",
    "search_pull_requests": "search",
    "search_repositories": "search",
    "search_users": "search",
    "get_file_contents": "read_file",
    # --- Pylance MCP (short names via _strip_mcp_prefix) ---
    "mcp_s_pylanceDocuments": "read_file",
    "mcp_s_pylanceFileSyntaxErrors": "lint",
    "mcp_s_pylanceImports": "read_file",
    "mcp_s_pylanceInstalledTopLevelModules": "read_file",
    "mcp_s_pylanceInvokeRefactoring": "edit",
    "mcp_s_pylancePythonEnvironments": "environment",
    "mcp_s_pylanceRunCodeSnippet": "terminal",
    "mcp_s_pylanceSettings": "read_file",
    "mcp_s_pylanceSyntaxErrors": "lint",
    "mcp_s_pylanceUpdatePythonEnvironment": "environment",
    "mcp_s_pylanceWorkspaceRoots": "read_file",
    "mcp_s_pylanceWorkspaceUserFiles": "read_file",
}

# Known CodePlane MCP tool names (schema-inferred short names).
# Used for namespace classification since inferred names don't carry a prefix.
_CODEPLANE_TOOL_NAMES: frozenset[str] = frozenset(
    k for k in _SUBKIND_BY_TOOL_NAME
    if k not in (
        "github_api", "github_repo", "run_in_terminal", "get_terminal_output",
        "manage_todo_list", "ask_questions", "tool_search_tool_regex",
    )
    and not k[0].isupper()  # exclude camelCase VS Code builtins
    and not k.startswith("mcp_s_pylance")
    and k not in (
        "edit_file", "create_file", "delete_file", "rename_file",
        "insert_edit", "replace_in_file", "list_directory", "open_file",
        "get_workspace_structure", "search_files", "find_text_in_files",
        "find_in_files", "configure_python_environment",
        "install_python_packages", "get_python_environment_details",
        "get_python_executable_details", "configure_python_notebook",
        "configure_non_python_notebook", "restart_notebook_kernel",
        "fetch_webpage", "open_simple_browser", "run_vscode_command",
        "install_extension", "vscode_searchExtensions_internal",
        "create_new_workspace", "get_project_setup_info",
        "create_and_run_task", "get_vscode_api", "test_failure",
        "search_code", "search_issues", "search_pull_requests",
        "search_repositories", "search_users", "get_file_contents",
    )
)


def _strip_mcp_prefix(tool_name: str) -> str:
    """Strip MCP server prefix: 'mcp_codeplane-eve_git_checkout' -> 'git_checkout'."""
    if tool_name.startswith("mcp_"):
        # Pattern: mcp_<server-label>_<actual_tool>
        # Server label may contain dashes: mcp_codeplane-eve_git_checkout
        parts = tool_name.split("_", 2)  # ['mcp', 'codeplane-eve', 'git_checkout']
        if len(parts) >= 3:
            return parts[2]
    return tool_name


def _infer_call_subkind(tool_name: str, tool_kind: str) -> str:
    """Classify a tool invocation into a broad functional subkind.

    Applies to all tool_kind values (native, mcp, builtin).
    """
    # Try with MCP prefix stripped first
    short = _strip_mcp_prefix(tool_name)
    if short in _SUBKIND_BY_TOOL_NAME:
        return _SUBKIND_BY_TOOL_NAME[short]
    # Then try direct match
    if tool_name in _SUBKIND_BY_TOOL_NAME:
        return _SUBKIND_BY_TOOL_NAME[tool_name]
    # Prefix-based fallback
    for prefix, kind in [
        ("git_", "git"),
        ("refactor_", "edit"),
        ("mcp_codeplane", "mcp_codeplane"),
        ("mcp_github", "git"),
        ("mcp_pylance", "read_file"),
    ]:
        if tool_name.startswith(prefix) or short.startswith(prefix):
            return kind
    if tool_kind == "native":
        return "terminal"
    return "unknown"


def _classify_tool_kind(tool_name: str) -> str:
    """Classify tool into kind: mcp, native, builtin.

    - mcp: MCP server tools (prefixed with ``mcp_``)
    - native: raw terminal access (``run_in_terminal``, ``get_terminal_output``)
    - builtin: all other VS Code / agent platform tools
    """
    if tool_name.startswith("mcp_"):
        return "mcp"
    if tool_name in ("run_in_terminal", "get_terminal_output"):
        return "native"
    return "builtin"


def _derive_tool_namespace(
    tool_name: str, tool_kind: str, codeplane_prefix: str,
) -> str:
    """Classify a tool call into a namespace bucket.

    Returns one of: "codeplane", "github_mcp", "pylance_mcp",
    "other_mcp", "native", "builtin", "unknown".
    """
    if tool_kind == "mcp":
        if tool_name in _CODEPLANE_TOOL_NAMES or tool_name.startswith(codeplane_prefix):
            return "codeplane"
        if "codeplane" in tool_name:
            return "codeplane"
        if tool_name.startswith("mcp_github"):
            return "github_mcp"
        if tool_name.startswith("mcp_pylance"):
            return "pylance_mcp"
        return "other_mcp"
    if tool_kind == "native":
        return "native"
    if tool_kind == "builtin":
        return "builtin"
    return "unknown"



# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------
def discover_vscode_user_dirs() -> list[Path]:
    """
    Return candidate VS Code User data directories, ordered by likelihood.
    On remote/WSL setups, ~/.vscode-server is checked first.
    """
    candidates: list[Path] = []
    home = Path.home()
    system = platform.system()

    # VS Code Remote / WSL — most common for server-side agent work
    vscode_server = home / ".vscode-server" / "data" / "User"
    if vscode_server.is_dir():
        candidates.append(vscode_server)
        log.info("Found VS Code Remote user dir: %s", vscode_server)

    if system == "Linux":
        local = home / ".config" / "Code" / "User"
        if local.is_dir():
            candidates.append(local)
            log.info("Found Linux local VS Code user dir: %s", local)
    elif system == "Darwin":
        local = home / "Library" / "Application Support" / "Code" / "User"
        if local.is_dir():
            candidates.append(local)
            log.info("Found macOS local VS Code user dir: %s", local)
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            local = Path(appdata) / "Code" / "User"
            if local.is_dir():
                candidates.append(local)
                log.info("Found Windows local VS Code user dir: %s", local)

    # WSL: check /mnt/c/Users/*/AppData/Roaming/Code/User/
    if system == "Linux" and Path("/mnt/c/Users").is_dir():
        try:
            for user_home in Path("/mnt/c/Users").iterdir():
                if not user_home.is_dir():
                    continue
                if user_home.name in ("Public", "Default", "All Users", "Default User"):
                    continue
                wsl_code = user_home / "AppData" / "Roaming" / "Code" / "User"
                try:
                    if wsl_code.is_dir():
                        candidates.append(wsl_code)
                        log.info("Found WSL Windows-side VS Code user dir: %s", wsl_code)
                except PermissionError:
                    pass
        except PermissionError:
            pass

    if not candidates:
        log.warning("No VS Code user data directories found.")

    return candidates


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------
def build_session_name_index(user_dirs: list[Path]) -> dict[str, str]:
    """
    Build a mapping from session_id -> chat title by reading
    state.vscdb databases (global and per-workspace) from all user_dirs.
    Works on local installs and WSL (via /mnt/c/).
    """
    import importlib
    try:
        sqlite3_mod = importlib.import_module("sqlite3")
    except ImportError:
        log.debug("sqlite3 not available for session name lookup.")
        return {}

    index: dict[str, str] = {}  # session_id -> title

    for user_dir in user_dirs:
        # Check global state
        candidates = [user_dir / "globalStorage" / "state.vscdb"]
        # Check per-workspace state
        ws_storage = user_dir / "workspaceStorage"
        if ws_storage.is_dir():
            try:
                for ws_dir in ws_storage.iterdir():
                    p = ws_dir / "state.vscdb"
                    try:
                        if p.is_file():
                            candidates.append(p)
                    except (PermissionError, OSError):
                        pass
            except (PermissionError, OSError):
                pass

        for db_path in candidates:
            try:
                if not db_path.is_file():
                    continue
            except (PermissionError, OSError):
                continue
            try:
                conn = sqlite3_mod.connect(str(db_path))
                cur = conn.cursor()
                cur.execute(
                    "SELECT value FROM ItemTable WHERE key = 'chat.ChatSessionStore.index'"
                )
                row = cur.fetchone()
                conn.close()
                if not row:
                    continue
                data = json.loads(row[0])
                entries = data.get("entries", {})
                if isinstance(entries, dict):
                    for sid, meta in entries.items():
                        title = meta.get("title", "")
                        if title and sid not in index:
                            index[sid] = title
                    log.debug(
                        "Loaded %d session names from %s", len(entries), db_path
                    )
            except Exception as e:
                log.debug("Could not read session names from %s: %s", db_path, e)

    log.info("Session name index: %d sessions with titles.", len(index))
    return index


def find_chat_sessions(
    user_dirs: list[Path], max_age_minutes: int | None
) -> list[dict]:
    """
    Scan workspaceStorage directories for Copilot Chat session-resource dirs.
    Returns a list of session descriptors sorted by most-recent tool call mtime (desc).

    Each descriptor:
      {
        "session_id": str,
        "session_path": Path,
        "workspace_hash": str,
        "user_dir": Path,
        "newest_tool_mtime": float,
        "oldest_tool_mtime": float,
        "tool_count": int,
      }
    """
    sessions: list[dict] = []
    cutoff = time.time() - (max_age_minutes * 60) if max_age_minutes else 0

    for user_dir in user_dirs:
        ws_storage = user_dir / "workspaceStorage"
        if not ws_storage.is_dir():
            continue

        for ws_hash_dir in ws_storage.iterdir():
            if not ws_hash_dir.is_dir():
                continue
            chat_res = ws_hash_dir / "GitHub.copilot-chat" / "chat-session-resources"
            if not chat_res.is_dir():
                continue

            log.debug("Scanning workspace storage: %s", ws_hash_dir.name)

            for session_dir in chat_res.iterdir():
                if not session_dir.is_dir():
                    continue
                if not UUID4_RE.match(session_dir.name):
                    continue

                # Enumerate tool call sub-directories
                tool_dirs = [
                    d for d in session_dir.iterdir()
                    if d.is_dir() and TOOL_DIR_RE.match(d.name)
                ]
                if not tool_dirs:
                    continue

                # Parse epoch_ms from directory names (preferred) and
                # collect mtime as fallback.
                max_epoch_ms = 0
                min_epoch_ms = 0
                newest_mtime = 0.0
                oldest_mtime = float("inf")
                epoch_ms_values: list[int] = []
                for td in tool_dirs:
                    m = TOOL_DIR_RE.match(td.name)
                    if m:
                        ems = int(m.group(2))
                        epoch_ms_values.append(ems)
                    for f in td.iterdir():
                        try:
                            mt = f.stat().st_mtime
                            if mt > newest_mtime:
                                newest_mtime = mt
                            if mt < oldest_mtime:
                                oldest_mtime = mt
                        except OSError:
                            pass

                if epoch_ms_values:
                    max_epoch_ms = max(epoch_ms_values)
                    min_epoch_ms = min(epoch_ms_values)
                if oldest_mtime == float("inf"):
                    oldest_mtime = newest_mtime

                # Apply max-age cutoff using epoch_ms if available,
                # fall back to mtime.
                if cutoff:
                    if max_epoch_ms:
                        cutoff_ms = int(cutoff * 1000)
                        if max_epoch_ms < cutoff_ms:
                            log.debug(
                                "  Skipping session %s (too old: epoch_ms %d < cutoff %d)",
                                session_dir.name, max_epoch_ms, cutoff_ms,
                            )
                            continue
                    elif newest_mtime < cutoff:
                        log.debug(
                            "  Skipping session %s (too old: mtime %.0f < cutoff %.0f)",
                            session_dir.name, newest_mtime, cutoff,
                        )
                        continue

                sessions.append({
                    "session_id": session_dir.name,
                    "session_path": session_dir,
                    "workspace_hash": ws_hash_dir.name,
                    "user_dir": user_dir,
                    "newest_tool_mtime": newest_mtime,
                    "oldest_tool_mtime": oldest_mtime,
                    "max_epoch_ms": max_epoch_ms,
                    "min_epoch_ms": min_epoch_ms,
                    "tool_count": len(tool_dirs),
                })

    # Sort by max_epoch_ms (preferred), fall back to newest_tool_mtime
    sessions.sort(
        key=lambda s: (s["max_epoch_ms"] or 0, s["newest_tool_mtime"]),
        reverse=True,
    )

    log.info("Discovered %d viable chat sessions.", len(sessions))
    for s in sessions[:5]:
        ts = datetime.fromtimestamp(s["newest_tool_mtime"], tz=UTC).isoformat()
        log.info(
            "  session=%s  workspace=%s  tools=%d  newest=%s",
            s["session_id"], s["workspace_hash"], s["tool_count"], ts,
        )

    return sessions


# ---------------------------------------------------------------------------
# Tool call extraction
# ---------------------------------------------------------------------------
def extract_tool_calls(session_path: Path) -> list[dict]:
    """
    Parse all tool call result directories within a session path.
    Returns a list of tool call descriptors sorted by timestamp.
    """
    calls: list[dict] = []

    for entry in session_path.iterdir():
        if not entry.is_dir():
            continue
        m = TOOL_DIR_RE.match(entry.name)
        if not m:
            continue

        tool_call_id = m.group(1)
        epoch_ms = int(m.group(2))

        # Infer tool name - keep both raw and refined
        raw_tool_name, raw_record_type = _infer_tool_raw(entry, tool_call_id)
        tool_name = raw_tool_name
        tool_kind = "unknown"

        # Determine result content size
        result_bytes: int | None = None
        status = "unknown"
        content_type = "unknown"
        resource_kind: str | None = None
        source_file: str | None = None

        content_json = entry / "content.json"
        content_txt = entry / "content.txt"

        if content_json.is_file():
            content_type = "json"
            source_file = str(content_json)
            try:
                result_bytes = content_json.stat().st_size
                status = "success"
                # Try to extract resource_kind and other metadata
                with open(content_json, errors="replace") as f:
                    data = json.load(f)
                # Any successfully-parsed content.json is a structured tool
                # result.  Classify by schema inference first, fall back to
                # "mcp" for dict/list payloads, keep "unknown" only on parse
                # failure.
                if tool_name == "github_api":
                    tool_kind = "builtin"
                else:
                    tool_kind = "mcp"
                # Extract additional metadata from dict payloads only
                if isinstance(data, dict):
                    resource_kind = data.get("resource_kind")
                    capability = data.get("capability_used")
                    if resource_kind:
                        tool_name = _refine_tool_name(tool_name, resource_kind, capability)
            except (json.JSONDecodeError, OSError) as e:
                log.debug("  Could not parse %s: %s", content_json, e)
                status = "error"

        elif content_txt.is_file():
            content_type = "text"
            source_file = str(content_txt)
            try:
                result_bytes = content_txt.stat().st_size
                status = "success"
                tool_kind = "native"  # terminal output = native VS Code tool
                if not tool_name or tool_name == "unknown":
                    tool_name = "run_in_terminal"
            except OSError:
                status = "error"

        # Safety-net fixup: classify by tool name when shape-based
        # classification missed (e.g. content.json parse failed but name known).
        if tool_kind == "unknown" and tool_name != "unknown":
            if tool_name == "github_api":
                tool_kind = "builtin"
            elif tool_name in ("git_log", "git_status", "git_diff", "git_commit",
                               "describe", "lint_check", "lint_tools",
                               "discover_test_targets", "run_test_targets",
                               "refactor_preview"):
                tool_kind = "mcp"

        # Derive call_subkind
        call_subkind = _infer_call_subkind(tool_name, tool_kind)

        # Infer args shape hint from result structure
        args_shape_hint = _infer_args_shape_hint(entry, tool_name)

        calls.append({
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "raw_tool_name": raw_tool_name,
            "raw_record_type": raw_record_type,
            "tool_kind": tool_kind,
            "call_subkind": call_subkind,
            "timestamp_epoch_ms": epoch_ms,
            "timestamp_iso": datetime.fromtimestamp(
                epoch_ms / 1000, tz=UTC
            ).isoformat() if epoch_ms > 1_000_000_000_000 else None,
            "result_bytes": result_bytes,
            "args_bytes": None,  # Arguments are not persisted on disk
            "args_shape_hint": args_shape_hint,
            "status": status,
            "content_type": content_type,
            "resource_kind": resource_kind,
            "source_file": source_file,
            "source_dir": str(entry),
            "turn_index": None,  # Will be inferred later if possible
        })

    # Sort by epoch timestamp for ordering
    calls.sort(key=lambda c: c["timestamp_epoch_ms"])
    return calls


def _infer_tool_raw(entry: Path, tool_call_id: str) -> tuple[str, str]:
    """
    Best-effort inference of tool name from directory contents.
    Returns (raw_tool_name, raw_record_type) — the un-refined name and
    the detection method used.

    raw_record_type values:
      - "schema_inference:<matched_pattern>"  e.g. "schema_inference:search"
      - "content_txt"   (terminal output file present)
      - "content_json"  (JSON result but no schema match)
      - "none"          (empty or unrecognized directory)
    """
    # Check schema.json for clues about the tool
    schema_path = entry / "schema.json"
    if schema_path.is_file():
        try:
            with open(schema_path, errors="replace") as f:
                schema = json.load(f)
            if isinstance(schema, dict):
                props = schema.get("properties", {})
                if "results" in props and "pagination" in props:
                    return ("search", "schema_inference:search")
                if "structural_changes" in props:
                    return ("semantic_diff", "schema_inference:semantic_diff")
                if "overview" in props and "structure" in props:
                    return ("map_repo", "schema_inference:map_repo")
                if "files" in props and "delivery" in props:
                    return ("read_source", "schema_inference:read_source")
                if "repository" in props and "branch" in props:
                    return ("describe", "schema_inference:describe")
                if "test_run_id" in props:
                    return ("run_test_targets", "schema_inference:run_test_targets")
                if "tools" in props or "lint_tools" in props:
                    return ("lint_tools", "schema_inference:lint_tools")
                if "diagnostics" in props:
                    return ("lint_check", "schema_inference:lint_check")
                if "test_ids" in props or "test_targets" in props:
                    return ("discover_test_targets", "schema_inference:discover_test_targets")
                if "files" in props and "summary" in props and "pagination" in props:
                    return ("read_file_full", "schema_inference:read_file_full")
                if "action" in props and "agentic_hint" in props:
                    if "impact" in props:
                        return ("write_files", "schema_inference:write_files")
                    if "dry_run" in props or "duration_seconds" in props:
                        return ("git_stage_and_commit", "schema_inference:git_stage_and_commit")
                    return ("codeplane_action", "schema_inference:codeplane_action")
                if "status" in props and "branch" in props and "staged" in props:
                    return ("git_status", "schema_inference:git_status")
                if "commits" in props:
                    return ("git_log", "schema_inference:git_log")
                if "diff" in props or "hunks" in props:
                    return ("git_diff", "schema_inference:git_diff")
                if "refactor_id" in props:
                    return ("refactor_preview", "schema_inference:refactor_preview")
                if "filename" in props or (
                    schema.get("type") == "array" and
                    isinstance(schema.get("items"), dict) and
                    "filename" in schema["items"].get("properties", {})
                ):
                    return ("github_api", "schema_inference:github_api")
                # Top-level array with typical GitHub API item keys
                if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
                    item_props = schema["items"].get("properties", {})
                    github_indicators = {"sha", "html_url", "commit", "node_id", "author_association"}
                    matched = github_indicators & set(item_props.keys())
                    if len(matched) >= 2:
                        return ("github_api", f"schema_inference:github_api_array:{','.join(sorted(matched))}")
                # Generic schema present but no match
                prop_keys = sorted(props.keys())[:5]
                return ("unknown", f"schema_inference:unmatched:{','.join(prop_keys)}")
        except (json.JSONDecodeError, OSError):
            pass

    # content.txt = terminal output
    if (entry / "content.txt").is_file():
        return ("run_in_terminal", "content_txt")

    # content.json present but no schema
    if (entry / "content.json").is_file():
        return ("unknown", "content_json")

    return ("unknown", "none")


def _refine_tool_name(
    current: str, resource_kind: str | None, capability: str | None
) -> str:
    """Refine tool name using resource_kind and capability_used from content.json."""
    if resource_kind:
        # Map known resource kinds to tool names
        kind_map = {
            "semantic_diff": "semantic_diff",
            "search_results": "search",
            "file_content": "read_source",
            "source": "read_source",
            "file_list": "list_files",
            "repo_map": "map_repo",
            "test_results": "run_test_targets",
            "lint_results": "lint_check",
            "git_status": "git_status",
            "git_log": "git_log",
            "git_diff": "git_diff",
            "git_commit": "git_commit",
            "refactor_preview": "refactor_preview",
            "description": "describe",
            "test_discovery": "discover_test_targets",
            "file_delivery": "read_file_full",
            "write_result": "write_files",
            "commit_result": "git_stage_and_commit",
        }
        if resource_kind in kind_map:
            return kind_map[resource_kind]
        return resource_kind
    if capability:
        return capability
    return current


# ---------------------------------------------------------------------------
# Args shape inference
# ---------------------------------------------------------------------------
def _infer_args_shape_hint(entry: Path, tool_name: str) -> dict | None:
    """
    Infer a shape hint about the tool's arguments from the result structure.
    We cannot recover actual args, but the result often reveals:
    - How many items were returned (proxy for scope of request)
    - What file paths appear (proxy for target)
    - Pagination info (proxy for limit/offset args)
    Returns a small dict or None.
    """
    content_json = entry / "content.json"
    if not content_json.is_file():
        return None

    try:
        with open(content_json, errors="replace") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    hint: dict[str, Any] = {}

    if isinstance(data, list):
        hint["result_item_count"] = len(data)
    elif isinstance(data, dict):
        # Files list in read_source / list_files
        files = data.get("files")
        if isinstance(files, list):
            hint["result_file_count"] = len(files)
            paths = [f.get("path") or f.get("filename") for f in files if isinstance(f, dict)]
            paths = [p for p in paths if p]
            if paths:
                hint["result_file_paths"] = paths[:5]  # cap to avoid bloat

        # Pagination info → proxy for limit/offset args
        pagination = data.get("pagination")
        if isinstance(pagination, dict):
            hint["pagination_total"] = pagination.get("total_count") or pagination.get("total")
            hint["pagination_returned"] = pagination.get("returned_count") or pagination.get("count")

        # Search results count
        results = data.get("results")
        if isinstance(results, list):
            hint["result_item_count"] = len(results)

        # Structural changes count (semantic_diff)
        changes = data.get("structural_changes")
        if isinstance(changes, list):
            hint["structural_changes_count"] = len(changes)

        # Test results
        test_results = data.get("test_results") or data.get("results")
        if isinstance(test_results, list) and test_results and isinstance(test_results[0], dict):
            if "test_id" in test_results[0] or "outcome" in test_results[0]:
                hint["test_count"] = len(test_results)

        # Commits (git_log)
        commits = data.get("commits")
        if isinstance(commits, list):
            hint["commit_count"] = len(commits)

    # Remove None values
    hint = {k: v for k, v in hint.items() if v is not None}
    return hint if hint else None


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------
def find_copilot_chat_logs(user_dir: Path) -> list[Path]:
    """
    Find Copilot Chat extension log files, sorted by mtime descending.
    Logs live under: <user_dir>/../logs/<date>/exthost<N>/GitHub.copilot-chat/GitHub Copilot Chat.log
    """
    # Navigate from User dir to the logs directory
    logs_root = user_dir.parent / "logs"
    if not logs_root.is_dir():
        log.debug("No logs directory found at %s", logs_root)
        return []

    chat_logs: list[Path] = []
    for date_dir in logs_root.iterdir():
        if not date_dir.is_dir():
            continue
        for exthost_dir in date_dir.iterdir():
            if not exthost_dir.is_dir() or not exthost_dir.name.startswith("exthost"):
                continue
            chat_log = exthost_dir / "GitHub.copilot-chat" / "GitHub Copilot Chat.log"
            if chat_log.is_file():
                chat_logs.append(chat_log)

    chat_logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    log.info("Found %d Copilot Chat log files.", len(chat_logs))
    return chat_logs


def parse_log_metadata(log_paths: list[Path]) -> dict:
    """
    Parse Copilot Chat logs for version info, request timing, and errors.
    Returns metadata dict.
    """
    meta: dict[str, Any] = {
        "copilot_chat_version": None,
        "vscode_version": None,
        "logged_in_user": None,
        "copilot_sku": None,
        "requests": [],
        "completions": [],
        "ccreqs": [],
        "errors": 0,
        "log_file": None,
        "log_timestamp_timezone_assumption": "local_server_time",
    }

    if not log_paths:
        return meta

    # Use the most recent log
    chosen_log = log_paths[0]
    meta["log_file"] = str(chosen_log)
    log.info("Parsing log: %s", chosen_log)

    try:
        with open(chosen_log, errors="replace") as f:
            for line in f:
                line = line.rstrip()

                # Version info
                vm = VERSION_RE.search(line)
                if vm:
                    meta["copilot_chat_version"] = vm.group(1)
                    meta["vscode_version"] = vm.group(2)

                # Logged-in user
                lm = LOGGED_IN_RE.search(line)
                if lm:
                    meta["logged_in_user"] = lm.group(1)

                # SKU
                sm = SKU_RE.search(line)
                if sm:
                    meta["copilot_sku"] = sm.group(1)

                # Parse log line for timestamp
                lm2 = LOG_LINE_RE.match(line)
                ts_str = lm2.group(1) if lm2 else None
                level = lm2.group(2) if lm2 else None
                msg = lm2.group(3) if lm2 else line

                if level == "error":
                    meta["errors"] += 1

                # Request done
                rdm = REQUEST_DONE_RE.search(msg)
                if rdm:
                    meta["requests"].append({
                        "request_id": rdm.group(1),
                        "model_deployment": rdm.group(2) or None,
                        "timestamp": ts_str,
                    })

                # ccreq line (most reliable completion timing)
                ccm = CCREQ_RE.search(msg)
                if ccm:
                    meta["ccreqs"].append({
                        "ccreq_id": ccm.group(1),
                        "tag": ccm.group(2),
                        "status": ccm.group(3),
                        "model_requested": ccm.group(4),
                        "model_served": ccm.group(5),
                        "duration_ms": float(ccm.group(6)),
                        "timestamp": ts_str,
                    })

                # fetchCompletions timing
                fcm = FETCH_COMPLETIONS_RE.search(msg)
                if fcm:
                    meta["completions"].append({
                        "request_id": fcm.group(1),
                        "status": int(fcm.group(2)),
                        "duration_ms": float(fcm.group(3)),
                        "timestamp": ts_str,
                    })

    except OSError as e:
        log.warning("Could not read log %s: %s", chosen_log, e)

    return meta


# ---------------------------------------------------------------------------
# State.vscdb parsing (local VS Code only — chat turn data)
# ---------------------------------------------------------------------------
def try_parse_state_vscdb(user_dir: Path, session_id: str) -> tuple[list[dict] | None, str]:
    """
    Attempt to read chat turns from VS Code's state.vscdb (SQLite).
    This is only available on local VS Code installations, not remote servers.
    Returns (turns_list_or_None, extraction_status).
    extraction_status: "ok" | "not_found" | "failed" | "partial"
    """
    import importlib
    try:
        sqlite3 = importlib.import_module("sqlite3")
    except ImportError:
        log.debug("sqlite3 not available.")
        return None, "not_found"

    # state.vscdb is typically at:
    #   ~/.config/Code/User/globalStorage/state.vscdb  (Linux)
    #   or alongside workspaceStorage
    candidate_paths = [
        user_dir / "globalStorage" / "state.vscdb",
        user_dir.parent / "state.vscdb",
    ]
    # Also check workspace-level state
    ws_storage = user_dir / "workspaceStorage"
    if ws_storage.is_dir():
        for ws_dir in ws_storage.iterdir():
            p = ws_dir / "state.vscdb"
            if p.is_file():
                candidate_paths.append(p)

    found_any_db = False
    for db_path in candidate_paths:
        if not db_path.is_file():
            continue
        found_any_db = True
        log.info("Found state database: %s", db_path)

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute(
                "SELECT key, value FROM ItemTable WHERE key LIKE '%chat%' OR key LIKE '%session%'"
            )
            rows = cursor.fetchall()
            conn.close()

            for key, value in rows:
                if not value:
                    continue
                try:
                    data = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    continue

                turns = _extract_turns_from_state(data, session_id)
                if turns:
                    log.info("Extracted %d turns from state.vscdb key '%s'", len(turns), key)
                    return turns, "ok"

        except Exception as e:
            log.debug("Could not query %s: %s", db_path, e)
            if found_any_db:
                return None, "failed"

    if not found_any_db:
        return None, "not_found"
    return None, "partial"


def _extract_turns_from_state(data: Any, session_id: str) -> list[dict] | None:
    """
    Try to extract chat turns from a JSON blob found in state.vscdb.
    Schema varies — we try several known structures defensively.
    """
    sessions = []

    if isinstance(data, list):
        sessions = data
    elif isinstance(data, dict):
        for key in ("sessions", "entries", "items", "data"):
            if key in data and isinstance(data[key], list):
                sessions = data[key]
                break
        if not sessions:
            sessions = [data]

    for session in sessions:
        if not isinstance(session, dict):
            continue
        sid = session.get("sessionId") or session.get("id") or session.get("session_id")
        if sid and str(sid) != session_id:
            continue

        exchanges = (
            session.get("requests") or
            session.get("exchanges") or
            session.get("turns") or
            session.get("messages") or
            []
        )
        if not exchanges:
            continue

        turns = []
        for i, ex in enumerate(exchanges):
            if not isinstance(ex, dict):
                continue
            turn = {
                "turn_index": i,
                "role": _infer_role(ex),
                "input_chars": _safe_len(ex.get("message") or ex.get("prompt") or ex.get("input") or ""),
                "output_chars": _safe_len(
                    ex.get("response") or ex.get("result") or ex.get("output") or
                    _extract_response_text(ex) or ""
                ),
            }
            turns.append(turn)

        if turns:
            return turns

    return None


def _infer_role(exchange: dict) -> str:
    """Infer role from exchange dict."""
    role = exchange.get("role")
    if role:
        return str(role)
    if "message" in exchange or "prompt" in exchange:
        return "user"
    if "response" in exchange or "result" in exchange:
        return "assistant"
    return "unknown"


def _extract_response_text(exchange: dict) -> str | None:
    """Try to extract response text from nested structures."""
    resp = exchange.get("response")
    if isinstance(resp, dict):
        return resp.get("value") or resp.get("message") or resp.get("text")
    if isinstance(resp, list):
        parts = []
        for part in resp:
            if isinstance(part, dict):
                parts.append(part.get("value") or part.get("text") or "")
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts) if parts else None
    return None


def _safe_len(val: Any) -> int:
    """Safely get length of a value, treating non-strings as their JSON serialization."""
    if isinstance(val, str):
        return len(val)
    if val is None:
        return 0
    try:
        return len(json.dumps(val))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Workspace name resolution
# ---------------------------------------------------------------------------
def resolve_workspace_name(user_dir: Path, workspace_hash: str) -> str | None:
    """
    Attempt to resolve the workspace folder name from the storage hash.
    Looks for workspace.json or vscode.lock in the workspace storage dir.
    """
    ws_dir = user_dir / "workspaceStorage" / workspace_hash

    wj = ws_dir / "workspace.json"
    if wj.is_file():
        try:
            with open(wj) as f:
                data = json.load(f)
            folder = data.get("folder") or data.get("workspace")
            if folder:
                if folder.startswith("file://"):
                    return Path(folder.replace("file://", "")).name
                return folder
        except (json.JSONDecodeError, OSError):
            pass

    base_hash = workspace_hash.split("-")[0]
    if base_hash != workspace_hash:
        base_dir = user_dir / "workspaceStorage" / base_hash
        wj = base_dir / "workspace.json"
        if wj.is_file():
            try:
                with open(wj) as f:
                    data = json.load(f)
                folder = data.get("folder") or data.get("workspace")
                if folder:
                    if folder.startswith("file://"):
                        return Path(folder.replace("file://", "")).name
                    return folder
            except (json.JSONDecodeError, OSError):
                pass

    return None


# ---------------------------------------------------------------------------
# Git HEAD
# ---------------------------------------------------------------------------
def get_git_head(repo_dir: str | None) -> str | None:
    """Get the current git HEAD SHA for the given repo directory."""
    if not repo_dir:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


# ---------------------------------------------------------------------------
# VS Code version from binary
# ---------------------------------------------------------------------------
def get_vscode_version_from_binary(user_dir: Path) -> str | None:
    """
    Try to extract VS Code version from the server binary path.
    """
    server_root = user_dir.parent.parent  # data -> .vscode-server
    bin_dir = server_root / "bin"
    if bin_dir.is_dir():
        for commit_dir in bin_dir.iterdir():
            pkg = commit_dir / "package.json"
            if pkg.is_file():
                try:
                    with open(pkg) as f:
                        data = json.load(f)
                    return data.get("version")
                except (json.JSONDecodeError, OSError):
                    pass

    try:
        result = subprocess.run(
            ["code", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if lines:
                return lines[0]
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return None


# ---------------------------------------------------------------------------
# Unified events timeline
# ---------------------------------------------------------------------------
def build_events_timeline(
    tool_calls: list[dict],
    log_meta: dict,
) -> list[dict]:
    """
    Build a merged + sorted timeline of tool invocations and LLM completions.

    Each event has:
      - event_type: "tool_invocation" | "completion"
      - timestamp_iso: ISO 8601 UTC timestamp
      - timestamp_epoch_ms: epoch milliseconds
      - (event-specific fields)
    """
    events: list[dict] = []

    # Tool invocation events
    for tc in tool_calls:
        events.append({
            "event_type": "tool_invocation",
            "timestamp_iso": tc.get("timestamp_iso"),
            "timestamp_epoch_ms": tc["timestamp_epoch_ms"],
            "tool_call_id": tc["tool_call_id"],
            "tool_name": tc["tool_name"],
            "tool_kind": tc["tool_kind"],
            "call_subkind": tc["call_subkind"],
            "status": tc["status"],
            "result_bytes": tc["result_bytes"],
        })

    # Completion events from ccreq log lines
    for ccreq in log_meta.get("ccreqs", []):
        ts_str = ccreq.get("timestamp")
        epoch_ms = None
        iso_str = None
        if ts_str:
            try:
                # Log timestamps are local server time, no TZ info in format
                # We parse naively and note the assumption
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
                epoch_ms = int(dt.timestamp() * 1000)
                iso_str = dt.isoformat() + "+00:00[assumed_local]"
            except ValueError:
                pass
        events.append({
            "event_type": "completion",
            "timestamp_iso": iso_str,
            "timestamp_epoch_ms": epoch_ms,
            "ccreq_id": ccreq.get("ccreq_id"),
            "status": ccreq.get("status"),
            "model_requested": ccreq.get("model_requested"),
            "model_served": ccreq.get("model_served"),
            "duration_ms": ccreq.get("duration_ms"),
            "tag": ccreq.get("tag"),
        })

    # Sort by epoch_ms; events without timestamps go last
    events.sort(key=lambda e: e.get("timestamp_epoch_ms") or float("inf"))

    # Add delta_to_next_event_ms
    for i in range(len(events) - 1):
        cur_ts = events[i].get("timestamp_epoch_ms")
        next_ts = events[i + 1].get("timestamp_epoch_ms")
        if cur_ts is not None and next_ts is not None:
            events[i]["delta_to_next_event_ms"] = next_ts - cur_ts
        else:
            events[i]["delta_to_next_event_ms"] = None
    if events:
        events[-1]["delta_to_next_event_ms"] = None  # last event has no next

    return events


# ---------------------------------------------------------------------------
# Pseudo-turn segmentation (heuristic)
# ---------------------------------------------------------------------------
PSEUDO_TURN_GAP_MS = 5000  # 5 seconds gap → new pseudo-turn

def segment_pseudo_turns(
    events: list[dict],
    gap_threshold_ms: int = PSEUDO_TURN_GAP_MS,
) -> list[dict]:
    """
    Heuristic segmentation of events into pseudo-turns.

    A new pseudo-turn starts when:
    1. A completion event follows a tool invocation (model responded, new cycle), OR
    2. There is a time gap > gap_threshold_ms between consecutive events, OR
    3. The first event always starts turn 0.

    Returns a list of pseudo-turn dicts:
      {
        "pseudo_turn_index": int,
        "start_epoch_ms": int,
        "end_epoch_ms": int,
        "start_iso": str,
        "end_iso": str,
        "duration_ms": int,
        "event_count": int,
        "tool_invocation_count": int,
        "completion_count": int,
        "tool_names": [str],
      }
    """
    if not events:
        return []

    pseudo_turns: list[dict] = []
    current_turn_events: list[dict] = []
    turn_index = 0

    def _flush_turn(evts: list[dict]) -> dict:
        epoch_values = [e["timestamp_epoch_ms"] for e in evts if e.get("timestamp_epoch_ms")]
        start_ms = min(epoch_values) if epoch_values else 0
        end_ms = max(epoch_values) if epoch_values else 0
        tool_names = [
            e.get("tool_name", "")
            for e in evts
            if e.get("event_type") == "tool_invocation"
        ]
        return {
            "pseudo_turn_index": -1,  # set by caller
            "start_epoch_ms": start_ms,
            "end_epoch_ms": end_ms,
            "start_iso": (
                datetime.fromtimestamp(start_ms / 1000, tz=UTC).isoformat()
                if start_ms > 1_000_000_000_000 else None
            ),
            "end_iso": (
                datetime.fromtimestamp(end_ms / 1000, tz=UTC).isoformat()
                if end_ms > 1_000_000_000_000 else None
            ),
            "duration_ms": end_ms - start_ms if start_ms and end_ms else 0,
            "event_count": len(evts),
            "tool_invocation_count": sum(
                1 for e in evts if e.get("event_type") == "tool_invocation"
            ),
            "completion_count": sum(
                1 for e in evts if e.get("event_type") == "completion"
            ),
            "tool_names": tool_names,
        }

    for i, event in enumerate(events):
        ts = event.get("timestamp_epoch_ms")

        # Check for turn boundary
        is_boundary = False
        if i == 0:
            is_boundary = False  # first event starts turn 0
        elif ts is not None and current_turn_events:
            prev_ts = current_turn_events[-1].get("timestamp_epoch_ms")
            if prev_ts is not None and (ts - prev_ts) > gap_threshold_ms:
                is_boundary = True

        if is_boundary and current_turn_events:
            turn = _flush_turn(current_turn_events)
            turn["pseudo_turn_index"] = turn_index
            pseudo_turns.append(turn)
            turn_index += 1
            current_turn_events = []

        current_turn_events.append(event)

    # Flush last turn
    if current_turn_events:
        turn = _flush_turn(current_turn_events)
        turn["pseudo_turn_index"] = turn_index
        pseudo_turns.append(turn)

    return pseudo_turns


# ---------------------------------------------------------------------------
# Main trace assembly
# ---------------------------------------------------------------------------
def compute_mcp_comparison_metrics(
    tool_calls: list[dict],
    events: list[dict],
    pseudo_turns: list[dict],
    session_start_ms: float | None,
    session_end_ms: float | None,
) -> dict:
    """
    Compute tiered metrics designed for MCP vs baseline comparison.

    Tier 1: Core proof metrics (tool counts, kind ratios, session duration, density, terminal spam)
    Tier 2: Convergence efficiency (calls/turn, orientation cost, native streaks)
    Tier 3: Cost proxies (result bytes, avg result size by tool)
    Tier 4: Stability signals (error rate — variance needs multi-run)

    All metrics are single-session; cross-session variance is left to the consumer.
    """
    import statistics as stats

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------
    tool_events = [e for e in events if e["event_type"] == "tool_invocation"]
    tool_timestamps = sorted(
        e["timestamp_epoch_ms"] for e in tool_events
        if e.get("timestamp_epoch_ms")
    )

    # -------------------------------------------------------------------
    # Tier 1 — Core proof metrics
    # -------------------------------------------------------------------

    # 1. Total tool calls
    total_tool_calls = len(tool_calls)

    # 2. Tool calls by kind + ratios
    native_calls = sum(1 for tc in tool_calls if tc["tool_kind"] == "native")
    mcp_calls = sum(1 for tc in tool_calls if tc["tool_kind"] == "mcp")
    builtin_calls = sum(1 for tc in tool_calls if tc["tool_kind"] == "builtin")
    other_calls = total_tool_calls - native_calls - mcp_calls - builtin_calls
    native_mcp_ratio = round(native_calls / mcp_calls, 2) if mcp_calls > 0 else None

    # 3. Time to convergence
    session_duration_ms = None
    session_duration_s = None
    tool_calls_per_second = None
    mcp_calls_per_second = None
    if session_start_ms and session_end_ms and session_end_ms > session_start_ms:
        session_duration_ms = session_end_ms - session_start_ms
        session_duration_s = round(session_duration_ms / 1000, 1)
        if session_duration_s > 0:
            tool_calls_per_second = round(total_tool_calls / session_duration_s, 4)
            mcp_calls_per_second = round(mcp_calls / session_duration_s, 4) if mcp_calls else 0.0

    # 4. Tool-call density / thrash shape
    calls_per_second_samples: list[float] = []
    max_burst_1s = 0
    longest_uninterrupted_streak = 0
    if len(tool_timestamps) >= 2:
        # Sliding 1-second window for burst detection
        for i, ts in enumerate(tool_timestamps):
            window_end = ts + 1000  # 1s window
            count_in_window = sum(1 for t in tool_timestamps[i:] if t <= window_end)
            if count_in_window > max_burst_1s:
                max_burst_1s = count_in_window

        # Calls per second in each 1-second bucket
        bucket_start = tool_timestamps[0]
        bucket_end = tool_timestamps[-1]
        bucket_ms = 1000
        t = bucket_start
        while t <= bucket_end:
            count = sum(1 for ts in tool_timestamps if t <= ts < t + bucket_ms)
            calls_per_second_samples.append(count)
            t += bucket_ms

        # Longest streak of consecutive tool calls with < 2s gaps
        current_streak = 1
        for i in range(1, len(tool_timestamps)):
            gap = tool_timestamps[i] - tool_timestamps[i - 1]
            if gap < 2000:  # less than 2s gap = part of same streak
                current_streak += 1
            else:
                if current_streak > longest_uninterrupted_streak:
                    longest_uninterrupted_streak = current_streak
                current_streak = 1
        if current_streak > longest_uninterrupted_streak:
            longest_uninterrupted_streak = current_streak

    cps_mean = round(stats.mean(calls_per_second_samples), 2) if calls_per_second_samples else None
    cps_max = max(calls_per_second_samples) if calls_per_second_samples else None
    cps_stddev = round(stats.stdev(calls_per_second_samples), 2) if len(calls_per_second_samples) > 1 else None

    # 5. Native terminal calls
    native_terminal_calls = sum(
        1 for tc in tool_calls
        if tc["tool_kind"] == "native" and tc["call_subkind"] == "terminal"
    )

    tier1 = {
        "total_tool_calls": total_tool_calls,
        "by_kind": {
            "native": native_calls,
            "mcp": mcp_calls,
            "builtin": builtin_calls,
            "other": other_calls,
        },
        "native_mcp_ratio": native_mcp_ratio,
        "session_duration_s": session_duration_s,
        "tool_calls_per_second": tool_calls_per_second,
        "mcp_calls_per_second": mcp_calls_per_second,
        "thrash_shape": {
            "max_burst_1s": max_burst_1s,
            "longest_uninterrupted_streak": longest_uninterrupted_streak,
            "calls_per_second_mean": cps_mean,
            "calls_per_second_max": cps_max,
            "calls_per_second_stddev": cps_stddev,
        },
        "native_terminal_calls": native_terminal_calls,
    }

    # -------------------------------------------------------------------
    # Tier 2 — Convergence efficiency
    # -------------------------------------------------------------------

    # 6. Tool calls per pseudo-turn
    pseudo_turn_count = len(pseudo_turns)
    tool_calls_per_turn = (
        round(total_tool_calls / pseudo_turn_count, 2)
        if pseudo_turn_count > 0 else None
    )

    # 7. Tool calls before first MCP call
    calls_before_first_mcp = None
    for i, e in enumerate(tool_events):
        if e.get("tool_kind") == "mcp":
            calls_before_first_mcp = i
            break
    if calls_before_first_mcp is None and mcp_calls == 0:
        calls_before_first_mcp = total_tool_calls  # never used MCP

    # 8. Longest native-only streak
    longest_native_streak = 0
    current_native = 0
    for e in tool_events:
        if e.get("tool_kind") == "native":
            current_native += 1
        else:
            if current_native > longest_native_streak:
                longest_native_streak = current_native
            current_native = 0
    if current_native > longest_native_streak:
        longest_native_streak = current_native

    tier2 = {
        "total_pseudo_turns": pseudo_turn_count,
        "tool_calls_per_pseudo_turn": tool_calls_per_turn,
        "calls_before_first_mcp": calls_before_first_mcp,
        "longest_native_only_streak": longest_native_streak,
    }

    # -------------------------------------------------------------------
    # Tier 3 — Cost & payload proxies
    # -------------------------------------------------------------------

    # 9. Total tool result bytes
    total_result_bytes = sum(tc["result_bytes"] or 0 for tc in tool_calls)

    # 10. Average result size by tool
    result_by_tool: dict[str, list[int]] = {}
    for tc in tool_calls:
        name = tc["tool_name"]
        rb = tc["result_bytes"] or 0
        result_by_tool.setdefault(name, []).append(rb)

    avg_result_by_tool = {
        name: {
            "count": len(sizes),
            "total_bytes": sum(sizes),
            "avg_bytes": round(sum(sizes) / len(sizes)),
        }
        for name, sizes in sorted(result_by_tool.items())
    }

    tier3 = {
        "total_result_bytes": total_result_bytes,
        "avg_result_bytes_per_call": round(total_result_bytes / total_tool_calls) if total_tool_calls else 0,
        "avg_result_by_tool": avg_result_by_tool,
    }

    # -------------------------------------------------------------------
    # Tier 4 — Stability & reliability
    # -------------------------------------------------------------------

    # 12. Error rate
    error_calls = sum(1 for tc in tool_calls if tc.get("status") != "success")
    error_rate = round(error_calls / total_tool_calls, 4) if total_tool_calls else 0.0

    tier4 = {
        "error_calls": error_calls,
        "error_rate": error_rate,
        "note": "cross-session variance requires multi-run comparison (Tier 4 metric 11)",
    }

    return {
        "tier1_core": tier1,
        "tier2_convergence": tier2,
        "tier3_cost_proxies": tier3,
        "tier4_stability": tier4,
    }


def build_trace(
    session: dict,
    tool_calls: list[dict],
    log_meta: dict,
    turns: list[dict] | None,
    turns_extraction_status: str,
    tokens_per_char: float,
    repo_dir: str | None,
    selection_reason: str,
    max_age_minutes_used: int | None,
    paths_scanned_count: int,
    sessions_found_count: int,
    codeplane_prefix: str = DEFAULT_CODEPLANE_PREFIX,
) -> dict:
    """Assemble the final trace JSON structure."""
    user_dir = session["user_dir"]

    # --- Session time bounds from tool call timestamps ---
    tool_epoch_ms_values = [tc["timestamp_epoch_ms"] for tc in tool_calls if tc["timestamp_epoch_ms"]]
    session_start_iso = None
    session_end_iso = None
    if tool_epoch_ms_values:
        min_ms = min(tool_epoch_ms_values)
        max_ms = max(tool_epoch_ms_values)
        if min_ms > 1_000_000_000_000:
            session_start_iso = datetime.fromtimestamp(min_ms / 1000, tz=UTC).isoformat()
        if max_ms > 1_000_000_000_000:
            session_end_iso = datetime.fromtimestamp(max_ms / 1000, tz=UTC).isoformat()

    # --- Run metadata ---
    vscode_version = (
        log_meta.get("vscode_version") or
        get_vscode_version_from_binary(user_dir)
    )
    workspace_name = resolve_workspace_name(user_dir, session["workspace_hash"])

    # Extraction warnings collector
    extraction_warnings: list[str] = []

    run_metadata = {
        "extraction_timestamp": datetime.now(UTC).isoformat(),
        "parser_version": PARSER_VERSION,
        "vscode_version": vscode_version,
        "copilot_chat_version": log_meta.get("copilot_chat_version"),
        "copilot_sku": log_meta.get("copilot_sku"),
        "workspace_folder_name": workspace_name,
        "workspace_storage_hash": session["workspace_hash"],
        "session_id": session["session_id"],
        "chat_title": session.get("chat_title"),
        "session_start_iso": session_start_iso,
        "session_end_iso": session_end_iso,
        "git_head_sha": get_git_head(repo_dir),
        "log_file_used": log_meta.get("log_file"),
        "log_timestamp_timezone_assumption": log_meta.get("log_timestamp_timezone_assumption"),
        "session_path": str(session["session_path"]),
        "sensitive": {
            "logged_in_user": log_meta.get("logged_in_user"),
        },
    }

    # --- Selection criteria ---
    selection_criteria = {
        "selected_session_reason": selection_reason,
        "max_age_minutes_used": max_age_minutes_used,
        "paths_scanned_count": paths_scanned_count,
        "sessions_found_total": sessions_found_count,
    }

    # --- Turns ---
    if turns:
        for t in turns:
            t["estimated_input_tokens"] = math.ceil(t["input_chars"] * tokens_per_char)
            t["estimated_output_tokens"] = math.ceil(t["output_chars"] * tokens_per_char)
    else:
        turns = []
        log.info("Chat turn text not available (likely remote server; stored client-side).")

    # Explicit total_turns handling: null = unknown, 0 = known-zero
    if turns_extraction_status == "ok":
        total_turns = len(turns)
    elif turns_extraction_status == "not_found":
        total_turns = None  # unknown — state.vscdb not present
    elif turns_extraction_status == "failed":
        total_turns = None  # unknown — extraction failed
    elif turns_extraction_status == "partial":
        total_turns = len(turns) if turns else None
    else:
        total_turns = None

    # --- Tool invocations ---
    tool_invocations = []
    for i, tc in enumerate(tool_calls):
        tool_namespace = _derive_tool_namespace(
            tc["tool_name"], tc["tool_kind"], codeplane_prefix,
        )
        tool_invocations.append({
            "order_index": i,
            "turn_index": tc.get("turn_index"),
            "tool_call_id": tc["tool_call_id"],
            "tool_name": tc["tool_name"],
            "raw_tool_name": tc["raw_tool_name"],
            "raw_record_type": tc["raw_record_type"],
            "tool_kind": tc["tool_kind"],
            "tool_namespace": tool_namespace,
            "call_subkind": tc["call_subkind"],
            "args_bytes": tc["args_bytes"],
            "args_shape_hint": tc.get("args_shape_hint"),
            "result_bytes": tc["result_bytes"],
            "status": tc["status"],
            "content_type": tc.get("content_type"),
            "resource_kind": tc.get("resource_kind"),
            "timestamp_epoch_ms": tc["timestamp_epoch_ms"],
            "timestamp_iso": tc.get("timestamp_iso"),
            "source_file": tc.get("source_file"),
            "source_dir": tc.get("source_dir"),
        })

    # --- Unified events timeline ---
    events = build_events_timeline(tool_calls, log_meta)

    # --- Summaries ---
    total_chars_in = sum(t.get("input_chars", 0) for t in turns)
    total_chars_out = sum(t.get("output_chars", 0) for t in turns)

    tool_calls_by_name: dict[str, int] = {}
    for tc in tool_calls:
        name = tc["tool_name"]
        tool_calls_by_name[name] = tool_calls_by_name.get(name, 0) + 1

    tool_calls_by_kind: dict[str, int] = {}
    for tc in tool_calls:
        kind = tc["tool_kind"]
        tool_calls_by_kind[kind] = tool_calls_by_kind.get(kind, 0) + 1

    tool_calls_by_subkind: dict[str, int] = {}
    for tc in tool_calls:
        sk = tc["call_subkind"]
        tool_calls_by_subkind[sk] = tool_calls_by_subkind.get(sk, 0) + 1

    # Namespace aggregations
    tool_calls_by_namespace: dict[str, int] = {}
    result_bytes_by_namespace: dict[str, int] = {}
    for ti in tool_invocations:
        ns = ti["tool_namespace"]
        tool_calls_by_namespace[ns] = tool_calls_by_namespace.get(ns, 0) + 1
        rb = ti.get("result_bytes") or 0
        result_bytes_by_namespace[ns] = result_bytes_by_namespace.get(ns, 0) + rb

    total_result_bytes = sum(tc["result_bytes"] or 0 for tc in tool_calls)

    # CodePlane rollups
    codeplane_tool_calls_total = tool_calls_by_namespace.get("codeplane", 0)
    codeplane_result_bytes_total = result_bytes_by_namespace.get("codeplane", 0)
    total_tc = len(tool_calls) or 1  # avoid div-by-zero
    codeplane_share_of_all_tool_calls = round(codeplane_tool_calls_total / total_tc, 4)
    codeplane_share_of_all_result_bytes = (
        round(codeplane_result_bytes_total / total_result_bytes, 4)
        if total_result_bytes else 0.0
    )

    # Token estimation — clearly hypothetical
    tool_result_chars = sum(tc["result_bytes"] or 0 for tc in tool_calls)
    tool_result_tokens_est_if_inlined = math.ceil(tool_result_chars * tokens_per_char)

    summaries = {
        "total_turns": total_turns,
        "turns_extraction_status": turns_extraction_status,
        "total_tool_calls": len(tool_calls),
        "tool_calls_by_name": tool_calls_by_name,
        "tool_calls_by_kind": tool_calls_by_kind,
        "tool_calls_by_subkind": tool_calls_by_subkind,
        "tool_calls_by_namespace": tool_calls_by_namespace,
        "result_bytes_by_namespace": result_bytes_by_namespace,
        "codeplane_tool_calls_total": codeplane_tool_calls_total,
        "codeplane_result_bytes_total": codeplane_result_bytes_total,
        "codeplane_share_of_all_tool_calls": codeplane_share_of_all_tool_calls,
        "codeplane_share_of_all_result_bytes": codeplane_share_of_all_result_bytes,
        "total_chars_in": total_chars_in if turns else None,
        "total_chars_out": total_chars_out if turns else None,
        "total_token_est": (
            math.ceil((total_chars_in + total_chars_out) * tokens_per_char)
            if turns else None
        ),
        "tool_result_bytes_total": total_result_bytes,
        "tool_result_tokens_est_if_inlined": tool_result_tokens_est_if_inlined,
        "log_requests_count": len(log_meta.get("requests", [])),
        "log_completions_count": len(log_meta.get("ccreqs", [])),
        "log_errors_count": log_meta.get("errors", 0),
        "tokens_per_char_used": tokens_per_char,
    }

    # --- Completion timing from ccreqs ---
    completions_summary: dict | None = None
    ccreqs = log_meta.get("ccreqs", [])
    if ccreqs:
        durations = [c["duration_ms"] for c in ccreqs]
        success_count = sum(1 for c in ccreqs if c["status"] == "success")
        rate_limited_count = sum(1 for c in ccreqs if c["status"] == "rateLimited")
        models_used = list(set(c["model_requested"] for c in ccreqs))
        completions_summary = {
            "count": len(ccreqs),
            "successful": success_count,
            "rate_limited": rate_limited_count,
            "total_ms": round(sum(durations), 2),
            "mean_ms": round(sum(durations) / len(durations), 2),
            "min_ms": round(min(durations), 2),
            "max_ms": round(max(durations), 2),
            "models_used": models_used,
        }

    # --- Pseudo-turn segmentation ---
    pseudo_turns = segment_pseudo_turns(events)

    # --- MCP comparison metrics ---
    session_start_ms = min(tool_epoch_ms_values) if tool_epoch_ms_values else None
    session_end_ms = max(tool_epoch_ms_values) if tool_epoch_ms_values else None
    mcp_metrics = compute_mcp_comparison_metrics(
        tool_calls=tool_calls,
        events=events,
        pseudo_turns=pseudo_turns,
        session_start_ms=session_start_ms,
        session_end_ms=session_end_ms,
    )

    # --- Inference counts ---
    schema_inferred = sum(
        1 for tc in tool_calls
        if tc.get("raw_record_type", "").startswith("schema_inference:")
    )
    name_unknown = sum(1 for tc in tool_calls if tc["tool_name"] == "unknown")
    run_metadata["inference_counts"] = {
        "tool_name_inferred_from_schema_count": schema_inferred,
        "tool_name_unknown_count": name_unknown,
    }

    # --- Warnings ---
    if name_unknown:
        extraction_warnings.append(
            f"could not infer tool name for {name_unknown} of {len(tool_calls)} tool calls"
        )
    if turns_extraction_status == "not_found":
        extraction_warnings.append(
            "state.vscdb not found; chat turns unavailable (remote server)"
        )
    elif turns_extraction_status == "failed":
        extraction_warnings.append(
            "state.vscdb found but extraction failed"
        )
    if not log_meta.get("log_file"):
        extraction_warnings.append("no Copilot Chat log file found")

    run_metadata["extraction_warnings"] = extraction_warnings

    # Update summaries with pseudo-turn count
    summaries["pseudo_turn_count"] = len(pseudo_turns)

    # --- Assemble trace ---
    trace = {
        "schema_version": "0.6.0",
        "run_metadata": run_metadata,
        "selection_criteria": selection_criteria,
        "turns": turns if turns else [],
        "pseudo_turns": pseudo_turns,
        "tool_invocations": tool_invocations,
        "events": events,
        "summaries": summaries,
        "completions_timing": completions_summary,
        "mcp_comparison_metrics": mcp_metrics,
    }

    return trace


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Extract a pseudo-trace from a VS Code Copilot Chat / Agent session.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--out", "-o",
        default=None,
        help="Output JSON file path. If omitted and --chat-name is used, "
             "auto-derives <chat_name_underscore_case>_trace.json.",
    )
    parser.add_argument(
        "--repo-dir",
        default=None,
        help="Repository directory to read git HEAD from.",
    )
    parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=None,
        help="Only consider sessions with tool calls newer than N minutes.",
    )
    parser.add_argument(
        "--tokens-per-char",
        type=float,
        default=0.25,
        help="Token estimation ratio (default: 0.25 ~ 4 chars/token).",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Specific session UUID to extract (default: most recent).",
    )
    parser.add_argument(
        "--workspace-hash",
        default=None,
        help="Specific workspace storage hash to scan (default: all).",
    )
    parser.add_argument(
        "--chat-name",
        default=None,
        help="Substring match against chat session title (case-insensitive). "
             "Requires state.vscdb access (local or WSL /mnt/c/).",
    )
    parser.add_argument(
        "--codeplane-prefix",
        default=DEFAULT_CODEPLANE_PREFIX,
        help=f"Prefix for CodePlane MCP tool names (default: {DEFAULT_CODEPLANE_PREFIX!r}).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    args = parser.parse_args()

    if not args.verbose:
        log.setLevel(logging.INFO)

    log.info("Starting VS Code Agent trace extraction...")

    # 1. Discover VS Code user directories
    user_dirs = discover_vscode_user_dirs()
    if not user_dirs:
        log.error("FATAL: No VS Code user data directories found.")
        sys.exit(1)

    # 2. Find chat sessions
    sessions = find_chat_sessions(user_dirs, args.max_age_minutes)
    paths_scanned_count = len(user_dirs)
    sessions_found_count = len(sessions)
    if not sessions:
        log.error("FATAL: No usable Copilot Chat Agent sessions found.")
        sys.exit(1)

    # 2b. Build session name index (for --chat-name and enrichment)
    session_name_index = build_session_name_index(user_dirs)

    # 3. Select session
    chosen = None
    selection_reason = "most_recent_by_mtime"
    if args.session_id:
        selection_reason = f"explicit_session_id:{args.session_id}"
        for s in sessions:
            if s["session_id"] == args.session_id:
                chosen = s
                break
        if not chosen:
            log.error("Session %s not found.", args.session_id)
            sys.exit(1)
    elif args.chat_name:
        needle = args.chat_name.lower()
        matches = []
        for s in sessions:
            title = session_name_index.get(s["session_id"], "")
            if needle in title.lower():
                matches.append(s)
        if not matches:
            log.error("No sessions matching chat name '%s'.", args.chat_name)
            # Show available named sessions to help the user pick
            named = [(s["session_id"], session_name_index.get(s["session_id"], "(unnamed)"))
                     for s in sessions if session_name_index.get(s["session_id"])]
            if named:
                log.error("Available named sessions:")
                for sid, title in named[:20]:
                    log.error('  %s  "%s"', sid[:8] + '..', title)
            sys.exit(1)
        elif len(matches) == 1:
            chosen = matches[0]
            selection_reason = f"chat_name_match:{args.chat_name}"
        else:
            # Multiple matches — pick most recent, but warn
            chosen = matches[0]  # already sorted by newest mtime
            selection_reason = f"chat_name_match:{args.chat_name} (1_of_{len(matches)}_matches)"
            log.warning(
                "Multiple sessions match '%s' — using most recent. Matches:",
                args.chat_name,
            )
            for m in matches:
                title = session_name_index.get(m["session_id"], "(unnamed)")
                log.warning('  %s  "%s"', m['session_id'][:8] + '..', title)
    elif args.workspace_hash:
        selection_reason = f"explicit_workspace_hash:{args.workspace_hash}"
        for s in sessions:
            if s["workspace_hash"] == args.workspace_hash or s["workspace_hash"].startswith(args.workspace_hash):
                chosen = s
                break
        if not chosen:
            log.error("No sessions found for workspace hash %s.", args.workspace_hash)
            sys.exit(1)
    else:
        chosen = sessions[0]
        if chosen.get("max_epoch_ms"):
            selection_reason = "max_epoch_ms"
        else:
            selection_reason = "newest_mtime_fallback"

    # Enrich chosen session with chat title if available
    chosen["chat_title"] = session_name_index.get(chosen["session_id"])

    log.info(
        "Selected session: %s (workspace: %s, %d tool calls, title: %s)",
        chosen["session_id"], chosen["workspace_hash"], chosen["tool_count"],
        chosen["chat_title"] or "(unnamed)",
    )

    # 4. Extract tool calls
    tool_calls = extract_tool_calls(chosen["session_path"])
    log.info("Extracted %d tool call artifacts.", len(tool_calls))

    # 5. Parse extension logs
    chat_logs = find_copilot_chat_logs(chosen["user_dir"])
    log_meta = parse_log_metadata(chat_logs)

    # 6. Try to extract chat turns from state.vscdb (local VS Code only)
    turns, turns_extraction_status = try_parse_state_vscdb(chosen["user_dir"], chosen["session_id"])

    # 7. Build trace
    trace = build_trace(
        session=chosen,
        tool_calls=tool_calls,
        log_meta=log_meta,
        turns=turns,
        turns_extraction_status=turns_extraction_status,
        tokens_per_char=args.tokens_per_char,
        repo_dir=args.repo_dir,
        selection_reason=selection_reason,
        max_age_minutes_used=args.max_age_minutes,
        paths_scanned_count=paths_scanned_count,
        sessions_found_count=sessions_found_count,
        codeplane_prefix=args.codeplane_prefix,
    )

    # 8. Resolve output path
    if args.out:
        out_path = Path(args.out)
    elif chosen.get("chat_title"):
        out_path = Path(f"{_to_underscore_case(chosen['chat_title'])}_trace.json")
    else:
        out_path = Path("trace.json")

    with open(out_path, "w") as f:
        json.dump(trace, f, indent=2, default=str)

    log.info("Trace written to: %s", out_path.resolve())

    # Brief summary to stderr
    s = trace["summaries"]
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Session:      {trace['run_metadata']['session_id']}", file=sys.stderr)
    if trace['run_metadata'].get('chat_title'):
        print(f"Chat title:   {trace['run_metadata']['chat_title']}", file=sys.stderr)
    print(f"VS Code:      {trace['run_metadata']['vscode_version']}", file=sys.stderr)
    print(f"Copilot Chat: {trace['run_metadata']['copilot_chat_version']}", file=sys.stderr)
    print(f"Time range:   {trace['run_metadata']['session_start_iso']} -> {trace['run_metadata']['session_end_iso']}", file=sys.stderr)
    print(f"Tool calls:   {s['total_tool_calls']}", file=sys.stderr)
    print(f"Turns status: {s['turns_extraction_status']}", file=sys.stderr)
    if s["total_turns"] is not None:
        print(f"Turns:        {s['total_turns']}", file=sys.stderr)
        print(f"Chars in:     {s['total_chars_in']}", file=sys.stderr)
        print(f"Chars out:    {s['total_chars_out']}", file=sys.stderr)
        print(f"Token est:    {s['total_token_est']}", file=sys.stderr)
    else:
        print(f"Turns:        unknown (state.vscdb {s['turns_extraction_status']})", file=sys.stderr)
    print(f"Result bytes: {s['tool_result_bytes_total']}", file=sys.stderr)
    print(f"Tool tokens (hypothetical if inlined):  {s['tool_result_tokens_est_if_inlined']}", file=sys.stderr)
    if trace.get("completions_timing"):
        ct = trace["completions_timing"]
        print(f"Completions:  {ct['count']} reqs ({ct['successful']} ok, {ct['rate_limited']} rate-limited), {ct['total_ms']:.0f}ms total, {ct['mean_ms']:.0f}ms avg", file=sys.stderr)
        print(f"Models used:  {ct['models_used']}", file=sys.stderr)
    print(f"Events:       {len(trace['events'])} unified timeline entries", file=sys.stderr)
    print(f"Pseudo turns: {s.get('pseudo_turn_count', 'N/A')}", file=sys.stderr)
    print(f"By tool name: {json.dumps(s['tool_calls_by_name'], indent=None)}", file=sys.stderr)
    print(f"By subkind:   {json.dumps(s['tool_calls_by_subkind'], indent=None)}", file=sys.stderr)
    print(f"By namespace: {json.dumps(s['tool_calls_by_namespace'], indent=None)}", file=sys.stderr)

    # MCP comparison metrics summary
    if trace.get("mcp_comparison_metrics"):
        m = trace["mcp_comparison_metrics"]
        t1 = m["tier1_core"]
        t2 = m["tier2_convergence"]
        t3 = m["tier3_cost_proxies"]
        t4 = m["tier4_stability"]
        print(f"{'─'*60}", file=sys.stderr)
        print("MCP COMPARISON METRICS", file=sys.stderr)
        print(f"{'─'*60}", file=sys.stderr)
        print(f"  T1 │ By kind:     native={t1['by_kind']['native']}  mcp={t1['by_kind']['mcp']}  builtin={t1['by_kind']['builtin']}", file=sys.stderr)
        print(f"  T1 │ native/MCP:  {t1['native_mcp_ratio']}", file=sys.stderr)
        print(f"  T1 │ Duration:    {t1['session_duration_s']}s", file=sys.stderr)
        print(f"  T1 │ Calls/sec:   {t1['tool_calls_per_second']}  (MCP: {t1['mcp_calls_per_second']})", file=sys.stderr)
        ts = t1["thrash_shape"]
        print(f"  T1 │ Thrash:      burst₁ₛ={ts['max_burst_1s']}  streak={ts['longest_uninterrupted_streak']}  cps_mean={ts['calls_per_second_mean']}  cps_max={ts['calls_per_second_max']}", file=sys.stderr)
        print(f"  T1 │ Terminal:    {t1['native_terminal_calls']} native terminal calls", file=sys.stderr)
        print(f"  T2 │ Calls/turn:  {t2['tool_calls_per_pseudo_turn']}", file=sys.stderr)
        print(f"  T2 │ Before MCP:  {t2['calls_before_first_mcp']} calls before first MCP call", file=sys.stderr)
        print(f"  T2 │ Nat streak:  {t2['longest_native_only_streak']} longest native-only streak", file=sys.stderr)
        print(f"  T3 │ Result:      {t3['total_result_bytes']} bytes total, {t3['avg_result_bytes_per_call']} avg/call", file=sys.stderr)
        print(f"  T4 │ Errors:      {t4['error_calls']} ({t4['error_rate']:.1%})", file=sys.stderr)
    print(f"Selection:    {trace['selection_criteria']['selected_session_reason']}", file=sys.stderr)
    if trace['run_metadata'].get('extraction_warnings'):
        print(f"Warnings:     {trace['run_metadata']['extraction_warnings']}", file=sys.stderr)
    print(f"Parser:       {trace['run_metadata']['parser_version']}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)


if __name__ == "__main__":
    main()
