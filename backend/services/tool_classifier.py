"""Tool classification for cost analytics.

Maps tool names (as reported by Copilot/Claude SDKs) to normalized
categories and extracts the primary target (file path, command name)
from tool arguments.
"""

from __future__ import annotations

import json
from typing import Any

TOOL_CATEGORIES: dict[str, str] = {
    # file_read — reading file contents
    "read_file": "file_read",
    "view": "file_read",
    "cat": "file_read",
    "Read": "file_read",
    "readFile": "file_read",
    "open_file": "file_read",
    "get_file_contents": "file_read",
    "TodoRead": "file_read",
    "NotebookRead": "file_read",
    # file_write — creating or editing files
    "edit_file": "file_write",
    "edit": "file_write",
    "create_file": "file_write",
    "write_file": "file_write",
    "write": "file_write",
    "Write": "file_write",
    "Edit": "file_write",
    "MultiEdit": "file_write",
    "editFile": "file_write",
    "create": "file_write",
    "replace_string_in_file": "file_write",
    "multi_replace_string_in_file": "file_write",
    "str_replace_based_edit_tool": "file_write",
    "insert_edit_into_file": "file_write",
    "apply_patch": "file_write",
    "create_or_update_file": "file_write",
    "delete_file": "file_write",
    "TodoWrite": "file_write",
    "NotebookEdit": "file_write",
    # file_search — searching and navigating the codebase
    "grep": "file_search",
    "grep_search": "file_search",
    "Grep": "file_search",
    "glob": "file_search",
    "Glob": "file_search",
    "find": "file_search",
    "rg": "file_search",
    "ripgrep": "file_search",
    "search": "file_search",
    "semantic_search": "file_search",
    "codeSearch": "file_search",
    "listDir": "file_search",
    "list_dir": "file_search",
    "LS": "file_search",
    "file_search": "file_search",
    "vscode_listCodeUsages": "file_search",
    "tool_search_tool_regex": "file_search",
    "ToolSearch": "file_search",
    "ListMcpResources": "file_search",
    "ListMcpResourceTemplates": "file_search",
    # shell — running commands in a terminal
    "bash": "shell",
    "Bash": "shell",
    "terminal": "shell",
    "exec": "shell",
    "runCommand": "shell",
    "run_in_terminal": "shell",
    "get_terminal_output": "shell",
    "read_bash": "shell",
    "write_bash": "shell",
    "stop_bash": "shell",
    "sql": "shell",
    # git — version control operations
    "git_diff": "git",
    "git_status": "git",
    "git_log": "git",
    "get_changed_files": "git",
    # browser — web fetches and browsing
    "fetch_url": "browser",
    "web_search": "browser",
    "web_fetch": "browser",
    "WebFetch": "browser",
    "WebSearch": "browser",
    "fetch_webpage": "browser",
    "ReadMcpResource": "browser",
    # agent — delegation to sub-agents
    "task": "agent",
    "subagent": "agent",
    "Agent": "agent",
    "runSubagent": "agent",
    "search_subagent": "agent",
    "skill": "agent",
    "Task": "agent",
    "read_agent": "agent",
    # system — agent-internal bookkeeping
    "report_intent": "system",
    "store_memory": "system",
    "manage_todo_list": "system",
    "memory": "system",
    "Think": "system",
    "Computer": "system",
}


def classify_tool(tool_name: str) -> str:
    """Return the normalized category for a tool name.

    For MCP-style names like ``server/tool``, tries the full name first,
    then falls back to just the tool part after the slash.
    """
    cat = TOOL_CATEGORIES.get(tool_name)
    if cat:
        return cat
    if "/" in tool_name:
        return TOOL_CATEGORIES.get(tool_name.rsplit("/", 1)[-1], "other")
    return "other"


def extract_tool_target(tool_name: str, tool_args: str | None) -> str:
    """Extract the primary target from tool arguments.

    Returns a short identifier suitable for grouping — e.g. a file path
    for file operations, or the command prefix for shell commands.
    """
    if not tool_args:
        return ""

    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
    except (json.JSONDecodeError, TypeError):
        return ""

    if not isinstance(parsed, dict):
        return ""

    category = classify_tool(tool_name)

    if category in ("file_read", "file_write"):
        return str(
            parsed.get("path", "")
            or parsed.get("file", "")
            or parsed.get("file_path", "")
            or parsed.get("filePath", "")
        )

    if category == "file_search":
        return str(parsed.get("pattern", "") or parsed.get("query", ""))

    if category == "shell":
        cmd = str(parsed.get("command", "") or parsed.get("cmd", ""))
        # Return first word of command as the target
        return cmd.split()[0] if cmd else ""

    if category == "git":
        return str(parsed.get("path", "") or parsed.get("file", ""))

    if category == "browser":
        return str(parsed.get("url", ""))

    return ""


def extract_file_paths(tool_name: str, tool_args: str | None) -> list[str]:
    """Extract all file paths referenced in tool arguments."""
    if not tool_args:
        return []

    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(parsed, dict):
        return []

    paths: list[str] = []
    for key in ("path", "file", "file_path", "filePath", "filename"):
        val = parsed.get(key)
        if val and isinstance(val, str):
            paths.append(val)

    # Some tools have a list of files
    for key in ("files", "paths"):
        val = parsed.get(key)
        if isinstance(val, list):
            paths.extend(str(v) for v in val if v)

    return paths
