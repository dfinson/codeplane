"""Tool classification for cost analytics.

Maps tool names (as reported by Copilot/Claude SDKs) to normalized
categories and extracts the primary target (file path, command name)
from tool arguments.
"""

from __future__ import annotations

import json
from typing import Any

TOOL_CATEGORIES: dict[str, str] = {
    # file_read
    "read_file": "file_read",
    "view": "file_read",
    "cat": "file_read",
    "Read": "file_read",
    "readFile": "file_read",
    # file_write
    "edit_file": "file_write",
    "create_file": "file_write",
    "write_file": "file_write",
    "Edit": "file_write",
    "editFile": "file_write",
    "create": "file_write",
    # file_search
    "grep": "file_search",
    "glob": "file_search",
    "find": "file_search",
    "ripgrep": "file_search",
    "search": "file_search",
    "codeSearch": "file_search",
    "listDir": "file_search",
    # shell
    "bash": "shell",
    "terminal": "shell",
    "exec": "shell",
    "runCommand": "shell",
    # git
    "git_diff": "git",
    "git_status": "git",
    "git_log": "git",
    # browser
    "fetch_url": "browser",
    "web_search": "browser",
    "WebFetch": "browser",
    # agent
    "task": "agent",
    "subagent": "agent",
    "Agent": "agent",
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
